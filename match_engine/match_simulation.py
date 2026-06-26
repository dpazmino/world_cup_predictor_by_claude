"""
Match Simulation Engine — main orchestrator.

Flow per play:
  1. Ball holder (LLM) decides action
  2. Evaluator validates/corrects the decision
  3. All non-holders (parallel LLM) reposition
  4. Evaluator checks each new position
  5. Resolution engine determines outcome
  6. Referee handles fouls, cards, set pieces
  7. Commentator narrates
  8. State updates; time advances
"""
from __future__ import annotations
import sys
import time

# Force UTF-8 output on Windows to handle international player names
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import importlib
from pathlib import Path
from typing import Optional

from .game_state import GameState, PlayerState, Position, Substitution
from .formations import get_formation_positions
from .resolution import resolve_action, resolve_set_piece, apply_fatigue
from .field import CENTER_X, CENTER_Y, PITCH_LENGTH, clamp_to_pitch
from .agents.player_agent import decide_with_ball, decide_with_ball_rule, reposition_all_parallel
from .player_mind import init_mind, update_mind
from .agents.coach_agent import CoachAgent
from .agents.referee_agent import RefereeAgent
from .agents.evaluator_agent import EvaluatorAgent
from .agents.commentator_agent import CommentatorAgent
from .coach_prompts import get_coach_prompt
from .player_skills import extract_skills, extract_athleticism
from .team_tactics import get_team_tactics

# Directory containing *_prompts.py files
PROMPTS_DIR = Path(__file__).parent.parent

MAX_PLAYS_PER_HALF = 250
SUB_WINDOWS = [55, 65, 75, 85]   # minutes when substitutions are considered

import random as _random

# Base shooting tendency by role (0.0 = never shoots, 1.0 = always shoots)
_ROLE_SHOOT_TENDENCY = {
    "GK":  0.00, "CB":  0.04, "RB":  0.06, "LB":  0.06,
    "RWB": 0.08, "LWB": 0.08, "CDM": 0.10, "CM":  0.22,
    "CAM": 0.48, "RM":  0.36, "LM":  0.36,
    "RW":  0.55, "LW":  0.55, "CF":  0.65, "ST":  0.72,
}

def _shot_pressure(holder: "PlayerState", state: "GameState",
                   action: dict, morale: float = 1.0) -> dict:
    """
    Player-aware shot override.  When a player chose 'pass' but is in
    shooting range, give them a chance to shoot based on their role and
    shoot_skill.  Defenders almost never override; strikers do often.
    Returns (possibly modified) action dict.
    """
    if action.get("action") != "pass":
        return action   # only consider overriding a pass decision

    attacking_right = state.team_attacks_right(holder.team)
    goal_x = 105.0 if attacking_right else 0.0
    dist = ((holder.position.x - goal_x) ** 2 +
            (holder.position.y - 34.0) ** 2) ** 0.5
    angle_y = abs(holder.position.y - 34.0)

    # Classify shot quality (mirrors player_agent.py logic)
    if dist < 18 and angle_y < 12:
        quality_mult = 1.0          # prime position
    elif dist < 28 and angle_y < 20:
        quality_mult = 0.55         # shootable
    elif dist < 38 and angle_y < 26:
        quality_mult = 0.20         # long range
    else:
        return action               # too far — don't override

    role_base = _ROLE_SHOOT_TENDENCY.get(holder.role, 0.25)
    shoot_skill = holder.shoot_skill
    probability = role_base * quality_mult * (0.4 + shoot_skill * 0.6) * morale

    if _random.random() < probability:
        return {**action, "action": "shoot",
                "reasoning": f"{holder.name} decides to shoot ({holder.role}, {dist:.0f}m out)"}
    return action


_ATT_ROLES = {"ST", "CF", "CAM", "RW", "LW", "RM", "LM"}
_DEF_ROLES = {"CB", "RB", "LB", "RWB", "LWB", "CDM"}


def _mark_target(player: "PlayerState", state: "GameState"):
    """The opponent attacker this defender should mark (deterministic pairing): the most
    dangerous attackers in our defensive third are picked up by our deepest defenders.
    Returns the PlayerState to mark, or None. Stops a striker sitting unmarked in the box."""
    ogx = 0.0 if state.team_attacks_right(player.team) else 105.0
    opp = "away" if player.team == "home" else "home"
    attackers = [p for p in state.get_team_players(opp)
                 if p.is_on_pitch and p.role in _ATT_ROLES
                 and abs(p.position.x - ogx) < 40.0]          # only in our defensive third
    markers = [p for p in state.get_team_players(player.team)
               if p.is_on_pitch and p.role in _DEF_ROLES]
    if not attackers or player.name not in {m.name for m in markers}:
        return None
    attackers.sort(key=lambda p: abs(p.position.x - ogx))     # most dangerous first
    markers.sort(key=lambda p: abs(p.position.x - ogx))       # deepest defenders first
    idx = [m.name for m in markers].index(player.name)
    return attackers[idx] if idx < len(attackers) else None


def _rule_pos(player: "PlayerState", state: "GameState") -> "Position":
    """
    Rule-based off-ball repositioning — no LLM needed.
    Each role moves to a sensible zone based on ball position and possession.
    """
    ar  = state.team_attacks_right(player.team)
    ip  = state.get_attacking_team() == player.team
    bx  = state.ball_position.x
    by  = state.ball_position.y
    sgn = 1 if ar else -1          # +1 home (attacks right), -1 away
    ogx = 0.0 if ar else 105.0     # own goal x
    agx = 105.0 if ar else 0.0     # attacking goal x

    # Game-state urgency: late in the game a chasing side commits bodies forward,
    # a leading side sits deeper and compact.
    opp = "away" if player.team == "home" else "home"
    diff = state.score[player.team] - state.score[opp]
    late = state.minute >= 70
    urgency = 1.0
    if late and diff <= -1:
        urgency = 1.35
    elif late and diff >= 1:
        urgency = 0.70

    # How far forward we push when in possession
    fwd = (12 * sgn * urgency) if ip else 0

    role = player.role
    cx, cy = player.position.x, player.position.y   # fallback

    if role == "GK":
        cx = ogx + 5 * sgn
        cy = max(20.0, min(48.0, by))

    elif role == "CB":
        cx = ogx + (22 + fwd * 0.2) * sgn
        # Split two CBs left/right of center
        cy = max(18.0, min(50.0, by + (7 if player.position.y >= 34 else -7)))

    elif role == "RB":
        cx = ogx + (30 + fwd * 0.5) * sgn
        cy = 55.0 if ip else 48.0

    elif role == "LB":
        cx = ogx + (30 + fwd * 0.5) * sgn
        cy = 13.0 if ip else 20.0

    elif role in ("RWB",):
        cx = ogx + (36 + fwd * 0.6) * sgn
        cy = 60.0

    elif role in ("LWB",):
        cx = ogx + (36 + fwd * 0.6) * sgn
        cy = 8.0

    elif role == "CDM":
        cx = ogx + (38 + fwd * 0.3) * sgn
        cy = max(22.0, min(46.0, by))

    elif role == "CM":
        cx = 52.5 + (8 if ip else -8) * sgn
        cy = max(18.0, min(50.0, by + (5 if player.position.y >= 34 else -5)))

    elif role == "CAM":
        cx = agx - (28 - fwd * 0.3) * sgn
        cy = max(20.0, min(48.0, by))

    elif role in ("RM", "RW"):
        cx = agx - (22 if ip else 38) * sgn
        cy = 58.0

    elif role in ("LM", "LW"):
        cx = agx - (22 if ip else 38) * sgn
        cy = 10.0

    elif role in ("ST", "CF"):
        cx = agx - (14 - fwd * 0.1) * sgn
        cy = max(24.0, min(44.0, by))

    # Man-mark the most dangerous attacker in our defensive third when out of possession,
    # so a striker can't sit unmarked goal-side of the defence and tap in repeat chances.
    if not ip and role in _DEF_ROLES:
        mark = _mark_target(player, state)
        if mark is not None:
            gs = 1.0 if ogx > mark.position.x else -1.0   # step goal-side of the attacker
            cx, cy = mark.position.x + 3.0 * gs, mark.position.y

    # Small jitter so the identical passing lane never reopens exactly play-to-play.
    cx += _random.uniform(-2.5, 2.5)
    cy += _random.uniform(-2.5, 2.5)
    return clamp_to_pitch(Position(cx, cy))


def _ball_loc(pos: Position) -> str:
    """Return a compact human-readable ball-location string: zone + coordinates."""
    x, y = pos.x, pos.y
    # Horizontal zone (absolute — home attacks right x=105)
    if x < 5.5:
        h = "home-box-6yd"
    elif x < 16.5:
        h = "home-pen-box"
    elif x < 35.0:
        h = "home-def-3rd"
    elif x < 52.5:
        h = "midfield-L"
    elif x < 70.0:
        h = "midfield-R"
    elif x < 88.5:
        h = "away-att-3rd"
    elif x < 99.5:
        h = "away-pen-box"
    else:
        h = "away-box-6yd"
    # Lateral band
    if y < 18:
        side = "left"
    elif y > 50:
        side = "right"
    else:
        side = "ctr"
    return f"[{h}-{side} ({x:.0f},{y:.0f})]"


def _load_player_prompts(team_name: str) -> dict[str, str]:
    """
    Dynamically import <team>_prompts.py and return the prompts dict.
    Falls back to empty dict if module not found.
    """
    module_name = team_name.lower().replace(" ", "_") + "_prompts"
    try:
        sys.path.insert(0, str(PROMPTS_DIR))
        mod = importlib.import_module(module_name)
        # The dict is named e.g. ARGENTINA_PROMPTS, SOUTH_AFRICA_PROMPTS, etc.
        dict_name = team_name.upper().replace(" ", "_") + "_PROMPTS"
        return getattr(mod, dict_name, {})
    except (ModuleNotFoundError, AttributeError):
        return {}


def _apply_substitution(state: GameState, sub: dict,
                          team: str,
                          bench_prompts: dict[str, str],
                          player_prompts: dict[str, str]) -> None:
    """Apply a substitution to the game state."""
    player_off = sub["player_off"]
    player_on_name = sub["player_on"]

    if player_off not in state.players:
        return

    # Take off
    off_player = state.players[player_off]
    off_player.is_on_pitch = False

    # Add the new player in the same position + role
    new_player = PlayerState(
        name=player_on_name,
        team=team,
        role=off_player.role,
        position=Position(off_player.position.x, off_player.position.y),
        fatigue=0.0,
        shirt_number=99,
    )
    state.players[player_on_name] = new_player

    # Transfer prompt and apply skill profile for incoming player
    if player_on_name in bench_prompts:
        player_prompts[player_on_name] = bench_prompts[player_on_name]
    from .player_skills import extract_skills as _extract_skills, extract_athleticism as _extract_ath
    sub_prompt = player_prompts.get(player_on_name, "")
    sub_skills = _extract_skills(sub_prompt, new_player.role, name=player_on_name)
    new_player.pass_skill = sub_skills["pass"]
    new_player.intercept_skill = sub_skills["intercept"]
    new_player.shoot_skill = sub_skills["shoot"]
    new_player.dribble_skill = sub_skills["dribble"]
    sub_ath = _extract_ath(new_player.role, name=player_on_name)
    new_player.pace = sub_ath["pace"]
    new_player.physical = sub_ath["physical"]
    new_player.stamina = sub_ath["stamina"]

    state.substitutions.append(Substitution(
        minute=state.minute,
        team=team,
        player_off=player_off,
        player_on=player_on_name,
        reason=sub.get("reason", "tactical"),
    ))

    if team == "home":
        state.home_subs_used += 1
    else:
        state.away_subs_used += 1

    print(f"  SUBSTITUTION ({state.minute}'): {team.upper()} — {player_off} OFF, {player_on_name} ON")


def _handle_goal(state: GameState, scoring_team: str) -> None:
    """Update score, update morale, and reset to kickoff."""
    state.score[scoring_team] += 1
    scorer_name = state.ball_holder or "Unknown"
    if scorer_name in state.players:
        state.players[scorer_name].goals += 1
    state.add_event("goal", f"GOAL! {scorer_name} — {state.score_str()}", scorer_name, scoring_team)

    # Morale: scoring team lifts, conceding team drops
    conceding_team = "away" if scoring_team == "home" else "home"
    if scoring_team == "home":
        state.morale_home = min(1.3, state.morale_home + 0.12)
        state.morale_away = max(0.7, state.morale_away - 0.06)
    else:
        state.morale_away = min(1.3, state.morale_away + 0.12)
        state.morale_home = max(0.7, state.morale_home - 0.06)

    state.ball_position = Position(CENTER_X, CENTER_Y)
    state.ball_holder = None
    state.phase = "kickoff"


def _advance_time(state: GameState, seconds: int) -> None:
    state.second += seconds
    while state.second >= 60:
        state.second -= 60
        state.minute += 1


def _kickoff(state: GameState, kicking_team: str) -> None:
    """Give ball to centre-forward of kicking team at centre circle."""
    team_players = state.get_team_players(kicking_team)
    cf = next((p for p in team_players if p.role == "CF"), team_players[0] if team_players else None)
    if cf:
        cf.position = Position(CENTER_X, CENTER_Y)
        state.ball_holder = cf.name
        state.ball_position = cf.position
    state.phase = "open_play"


class MatchSimulation:
    """
    Runs a single match between home_team and away_team.
    """

    def __init__(self,
                 home_team: str,
                 away_team: str,
                 verbose: bool = True,
                 time_scale: float = 1.0,
                 fast_mode: bool = False,
                 knockout: bool = False,
                 offline: bool = False,
                 ai=None):
        self.home_team = home_team
        self.away_team = away_team
        self.verbose = verbose
        self.time_scale = max(1.0, float(time_scale))

        # ── Which decision surfaces use the LLM ("AI") vs deterministic rules ──
        # Surfaces: decisions (on-ball action), evaluator, repositioning,
        # commentary, coach. Presets pick a set; `ai` overrides for fine control.
        #   default      → full AI (original behaviour)
        #   fast_mode    → AI on the ball only (human-like decisions, rules elsewhere)
        #   offline      → none (fully deterministic, zero LLM calls)
        #   ai={...}     → exactly these surfaces use the LLM
        _ALL = {"decisions", "evaluator", "repositioning", "commentary", "coach"}
        if ai is not None:
            surfaces = {s.lower() for s in ai}
        elif offline:
            surfaces = set()
        elif fast_mode:
            surfaces = {"decisions", "evaluator"}
        else:
            surfaces = set(_ALL)

        self.ai_decisions     = "decisions" in surfaces
        self.ai_evaluator     = ("evaluator" in surfaces) and self.ai_decisions
        self.ai_repositioning = "repositioning" in surfaces
        self.ai_commentary    = "commentary" in surfaces
        self.ai_coach         = "coach" in surfaces
        self.ai_surfaces      = surfaces

        # Convenience flags (kept for back-compat / display).
        self.uses_ai = bool(surfaces)
        self.offline = not surfaces
        self.fast_mode = not self.ai_repositioning   # rule-based off-ball positioning

        # Knockout ties cannot end level: play extra time then a shootout.
        self.knockout = knockout
        self.went_to_extra_time = False
        self.shootout_result: Optional[dict] = None

        # Load prompts
        home_raw = _load_player_prompts(home_team)
        away_raw = _load_player_prompts(away_team)
        self.all_player_prompts: dict[str, str] = {**home_raw, **away_raw}

        # Coaches
        home_coach_prompt = get_coach_prompt(home_team)
        away_coach_prompt = get_coach_prompt(away_team)

        home_squad = list(home_raw.keys())
        away_squad = list(away_raw.keys())

        self.home_coach = CoachAgent(home_team, "home", home_squad, home_coach_prompt,
                                      squad_prompts=home_raw, offline=not self.ai_coach)
        self.away_coach = CoachAgent(away_team, "away", away_squad, away_coach_prompt,
                                      squad_prompts=away_raw, offline=not self.ai_coach)

        # Sub-agents
        self.referee = RefereeAgent()
        self.evaluator = EvaluatorAgent()
        self.commentator = CommentatorAgent(home_team, away_team)

        self.state: Optional[GameState] = None
        self.decision_log: list[dict] = []

        # Team tactical briefs — replaced at half-time if coach adjusts
        self._home_tactics = get_team_tactics(home_team)
        self._away_tactics = get_team_tactics(away_team)

        # In-game coach messages — injected into next N player decisions
        self._coach_msg: dict[str, str] = {"home": "", "away": ""}
        self._coach_msg_ttl: dict[str, int] = {"home": 0, "away": 0}  # plays remaining

        # Substitution windows already processed (one pass per window).
        self._subs_done: set[int] = set()

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def _player_prompt(self, player: "PlayerState") -> str:
        """Individual character + team tactics + coach message + fatigue state."""
        individual = self.all_player_prompts.get(player.name, "")
        tactics    = self._home_tactics if player.team == "home" else self._away_tactics
        parts      = [p for p in [individual, tactics] if p]

        # Game-state instruction (scoreline + clock aware)
        gsi = self._game_state_instruction(player.team)
        if gsi:
            parts.append(gsi)

        # Active coach message (after goal / red card)
        side = player.team
        if self._coach_msg_ttl.get(side, 0) > 0 and self._coach_msg.get(side):
            parts.append(f"## COACH INSTRUCTION (NOW)\n{self._coach_msg[side]}")

        # Fatigue warning — affects decision-making
        if player.fatigue >= 80:
            parts.append(
                f"⚠️ YOU ARE EXHAUSTED (fatigue {player.fatigue:.0f}%) — keep decisions simple. "
                "Pass to the nearest open teammate. Do NOT attempt long dribbles."
            )
        elif player.fatigue >= 60:
            parts.append(
                f"Note: You are tiring (fatigue {player.fatigue:.0f}%) — conserve energy, "
                "prefer simple passes over complex moves."
            )

        return "\n\n".join(parts)

    def _score_diff(self, team: str) -> int:
        opp = "away" if team == "home" else "home"
        return self.state.score[team] - self.state.score[opp]

    def _urgency(self, team: str) -> float:
        """Attacking aggression multiplier from scoreline + clock (1.0 = neutral)."""
        diff = self._score_diff(team)
        minute = self.state.minute
        if minute < 60:
            return 1.0
        if diff <= -2:
            return 1.30
        if diff == -1:
            return 1.20 if minute >= 70 else 1.10
        if diff >= 1 and minute >= 75:
            return 0.80          # game-managing
        return 1.0

    def _game_state_instruction(self, team: str) -> str:
        """A short scoreline/clock brief injected into player decisions."""
        diff = self._score_diff(team)
        minute = self.state.minute
        if minute < 60:
            return ""
        if diff <= -1 and minute >= 75:
            return ("## GAME STATE\nYou are CHASING the game and time is short. Play with urgency — "
                    "push higher, take more risks, shoot when in range, get bodies into the box.")
        if diff <= -1:
            return ("## GAME STATE\nYou are behind. Raise the tempo, look for forward passes and "
                    "shooting chances rather than safe sideways play.")
        if diff >= 1 and minute >= 80:
            return ("## GAME STATE\nYou are PROTECTING a lead late. Keep the ball, slow the tempo, "
                    "stay compact and disciplined, avoid risky passes near your own goal.")
        if diff >= 1 and minute >= 70:
            return ("## GAME STATE\nYou lead. Be smart — pick your moments to attack, don't get "
                    "caught over-committing; security over flair.")
        return ""

    def _set_coach_message(self, side: str, message: str, ttl: int = 6) -> None:
        """Broadcast a coach message to all players on `side` for the next `ttl` plays."""
        self._coach_msg[side]     = message
        self._coach_msg_ttl[side] = ttl

    def _tick_coach_messages(self) -> None:
        """Decrement TTL each play."""
        for side in ("home", "away"):
            if self._coach_msg_ttl[side] > 0:
                self._coach_msg_ttl[side] -= 1

    def _apply_player_skills(self) -> None:
        """Populate each player's skill profile — CSV stats first, prompt keywords as fallback."""
        for name, player in self.state.players.items():
            prompt = self.all_player_prompts.get(name, "")
            skills = extract_skills(prompt, player.role, name=name)
            player.pass_skill = skills["pass"]
            player.intercept_skill = skills["intercept"]
            player.shoot_skill = skills["shoot"]
            player.dribble_skill = skills["dribble"]
            athletic = extract_athleticism(player.role, name=name)
            player.pace = athletic["pace"]
            player.physical = athletic["physical"]
            player.stamina = athletic["stamina"]
            # Mental traits (composure, risk appetite) from the same prompt text.
            init_mind(player, prompt)

    def setup(self) -> None:
        """Coach selection and formation setup."""
        self._log(f"\n{'='*60}")
        self._log(f"  {self.home_team} vs {self.away_team}")
        self._log(f"{'='*60}")
        self._log("\nCoach selection:")
        self.home_coach.prepare_match(self.away_team)
        self.away_coach.prepare_match(self.home_team)

        home_positions = get_formation_positions(
            self.home_coach.formation, "home", self.home_coach.starting_xi
        )
        away_positions = get_formation_positions(
            self.away_coach.formation, "away", self.away_coach.starting_xi
        )

        all_players = {**home_positions, **away_positions}
        self.state = GameState(
            home_team=self.home_team,
            away_team=self.away_team,
            home_formation=self.home_coach.formation,
            away_formation=self.away_coach.formation,
            players=all_players,
            ball_position=Position(CENTER_X, CENTER_Y),
            ball_holder=None,
        )
        self._apply_player_skills()

    def _play_half(self, half: int) -> None:
        state = self.state
        state.half = half
        # Regulation halves (1,2) plus extra-time halves (3,4).
        period_bounds = {1: (0, 45), 2: (45, 90), 3: (90, 105), 4: (105, 120)}
        start_minute, base_end = period_bounds.get(half, (0, 45))
        state.minute = start_minute
        state.second = 0

        # Stoppage time for regulation halves; extra-time halves run exact.
        if half == 1:
            stoppage = _random.randint(1, 3)
        elif half == 2:
            stoppage = _random.randint(2, 6)
        else:
            stoppage = _random.randint(0, 2) if half in (3, 4) else 0
        end_minute = base_end + stoppage

        # Kickoff
        kicking_team = "home" if half in (1, 3) else (
            "away" if state.score["home"] == state.score["away"]
            else ("home" if state.score["home"] > state.score["away"] else "away")
        )
        _kickoff(state, kicking_team)
        label = {1: "HALF 1", 2: "HALF 2", 3: "EXTRA TIME 1", 4: "EXTRA TIME 2"}.get(half, f"HALF {half}")
        self._log(f"\n{'─'*60}")
        self._log(f"  KICK OFF — {label}")
        if stoppage:
            self._log(f"  (+{stoppage} min stoppage time)")
        self._log(f"{'─'*60}")

        plays = 0
        consecutive_holds: dict[str, int] = {}   # player_name -> hold count

        while state.minute < end_minute and plays < MAX_PLAYS_PER_HALF:
            plays += 1
            # Decay fast-break transition window
            if state.transition_ttl > 0:
                state.transition_ttl -= 1
                if state.transition_ttl == 0:
                    state.transition_team = None

            # ── SET PIECE RESOLUTION ──────────────────────────────────────
            if state.phase in ("corner", "free_kick", "penalty", "throw_in", "goal_kick"):
                result = resolve_set_piece(state, state.phase)
                state.ball_position = result.new_ball_position
                if result.new_ball_holder and result.new_ball_holder in state.players:
                    state.ball_holder = result.new_ball_holder
                    team = state.players[result.new_ball_holder].team
                    if team == "home":
                        state.possession_home += 1
                    else:
                        state.possession_away += 1
                state.phase = result.new_phase
                sp_ball_tag = _ball_loc(result.new_ball_position)
                if result.scoring_team:
                    _handle_goal(state, result.scoring_team)
                    self._log(f"\n  {state.time_str()} ★ {result.description} {sp_ball_tag}")
                    if self.ai_commentary:
                        comment = self.commentator.describe(
                            state, "goal", result.description,
                            player=state.ball_holder, scoring_team=result.scoring_team
                        )
                        self._log(f"  {comment}")
                    self._log(f"  Score: {state.score_str()}\n")
                else:
                    if not self.ai_commentary:
                        self._log(f"  {state.time_str()} {result.description} {sp_ball_tag}")
                    else:
                        comment = self.commentator.describe(state, result.event_type, result.description)
                        self._log(f"  {state.time_str()} {comment} {sp_ball_tag}")
                _advance_time(state, int(result.time_seconds * self.time_scale))
                continue

            # ── KICKOFF HANDLING ──────────────────────────────────────────
            if state.phase == "kickoff":
                _kickoff(state, kicking_team)
                state.phase = "open_play"
                continue

            # ── LOOSE BALL — give to nearest ─────────────────────────────
            if not state.ball_holder:
                all_on_pitch = [p for p in state.players.values() if p.is_on_pitch]
                if all_on_pitch:
                    # Foot race: nearer + faster player wins the loose ball.
                    nearest = min(all_on_pitch,
                                  key=lambda p: p.position.distance_to(state.ball_position) / (0.6 + 0.4 * p.pace))
                    state.ball_holder = nearest.name
                    state.ball_position = nearest.position
                continue

            holder = state.players.get(state.ball_holder)
            if not holder or not holder.is_on_pitch:
                state.ball_holder = None
                continue

            # ── 1. BALL HOLDER DECIDES ───────────────────────────────────
            # AI mimics the human decision on the ball; rules are the cheap fallback.
            if self.ai_decisions:
                holder_prompt = self._player_prompt(holder)
                raw_action = decide_with_ball(state, holder, holder_prompt)
            else:
                raw_action = decide_with_ball_rule(state, holder)

            # Break hold loops: force a pass after 2 consecutive holds
            if raw_action.get("action") == "hold":
                consecutive_holds[holder.name] = consecutive_holds.get(holder.name, 0) + 1
                if consecutive_holds[holder.name] > 2:
                    raw_action = {**raw_action, "action": "pass",
                                  "reasoning": "forced pass to break hold loop"}
                    consecutive_holds[holder.name] = 0
            else:
                consecutive_holds[holder.name] = 0

            # Player-specific shot pressure — morale + scoreline urgency boost aggression.
            # Only for AI decisions (it nudges a conservative LLM); the rule policy
            # already makes its own shot calls.
            if self.ai_decisions:
                morale = state.morale_home if holder.team == "home" else state.morale_away
                morale *= self._urgency(holder.team)
                raw_action = _shot_pressure(holder, state, raw_action, morale)

            # Tick coach message countdown
            self._tick_coach_messages()

            # ── 2. EVALUATOR VALIDATES ───────────────────────────────────
            # Rule-based decisions are already valid → only run the LLM evaluator
            # when it is enabled (and only AI decisions need validating).
            if self.ai_evaluator:
                prev_corrections = self.evaluator.corrections_made
                action = self.evaluator.validate_and_correct(state, holder, raw_action)
                was_corrected = self.evaluator.corrections_made > prev_corrections
            else:
                action = raw_action
                was_corrected = False

            # ── 3. REPOSITIONING ─────────────────────────────────────────
            if not self.ai_repositioning:
                # Rule-based: no LLM calls, just geometry
                for name, p in state.players.items():
                    if name != state.ball_holder and p.is_on_pitch:
                        p.position = _rule_pos(p, state)
            else:
                tactics_prompts = {
                    name: self._player_prompt(p)
                    for name, p in state.players.items()
                }
                new_positions = reposition_all_parallel(state, tactics_prompts)
                for name, pos in new_positions.items():
                    if name in state.players:
                        validated = self.evaluator.validate_position(
                            state.players[name], pos, state
                        )
                        state.players[name].position = validated

            # ── 4. RESOLVE ACTION ────────────────────────────────────────
            result = resolve_action(state, action)

            # Referee: issue cards for fouls flagged by resolution.
            if result.event_type == "foul" and result.foul_by:
                card = self.referee.card_for_foul(state, result.foul_by)
                if card == "red":
                    self._log(f"  {state.time_str()} 🟥 RED CARD — {result.foul_by} is sent off!")
                elif card == "yellow":
                    self._log(f"  {state.time_str()} 🟨 Yellow card — {result.foul_by}.")

            # Update possession counters (touches + time-weighted)
            if holder.team == "home":
                state.possession_home += 1
                state.possession_secs_home += result.time_seconds
            else:
                state.possession_away += 1
                state.possession_secs_away += result.time_seconds

            # Apply fatigue
            apply_fatigue(state, action, result.time_seconds)

            # Evolve players' mental/emotional state from this play's outcome
            update_mind(state, holder, result)

            # ── DECISION LOG ─────────────────────────────────────────────
            self.decision_log.append({
                "minute": state.minute,
                "second": state.second,
                "player": holder.name,
                "role": holder.role,
                "team": holder.team,
                "action": action.get("action"),
                "target": action.get("target_player"),
                "direction": action.get("direction"),
                "reasoning": raw_action.get("reasoning", action.get("reasoning", "")),
                "evaluator_corrected": was_corrected,
                "outcome": result.event_type,
                "outcome_desc": result.description,
                "ball_x": round(state.ball_position.x, 1),
                "ball_y": round(state.ball_position.y, 1),
                "ball_zone": _ball_loc(state.ball_position),
            })

            # ── 5. UPDATE STATE ──────────────────────────────────────────
            state.ball_position = result.new_ball_position
            state.phase = result.new_phase

            ball_tag = _ball_loc(result.new_ball_position)

            if result.scoring_team:
                _handle_goal(state, result.scoring_team)
                self._log(f"\n  {state.time_str()} ★ GOAL! {result.description} {ball_tag}")
                if self.ai_commentary:
                    comment = self.commentator.describe(
                        state, "goal", result.description,
                        player=holder.name, scoring_team=result.scoring_team
                    )
                    self._log(f"  ★ {comment}")
                self._log(f"  Score: {state.score_str()}\n")
                kicking_team = "away" if result.scoring_team == "home" else "home"

                # Coach reacts to goal — both teams get a message
                conceding = "away" if result.scoring_team == "home" else "home"
                scoring_coach  = self.home_coach if result.scoring_team == "home" else self.away_coach
                conceding_coach = self.away_coach if result.scoring_team == "home" else self.home_coach
                if self.ai_coach:
                    score_msg   = scoring_coach.in_game_message(state,  f"We just scored — {state.score_str()}")
                    concede_msg = conceding_coach.in_game_message(state, f"We just conceded — {state.score_str()}")
                    self._set_coach_message(result.scoring_team, score_msg)
                    self._set_coach_message(conceding, concede_msg)
                    self._log(f"  [{scoring_coach.team_name}] {score_msg}")
                    self._log(f"  [{conceding_coach.team_name}] {concede_msg}")

            elif result.event_type in ("shot_saved", "shot_missed", "shot_blocked",
                                        "woodwork", "penalty_saved", "interception",
                                        "yellow_card", "red_card", "foul", "offside",
                                        "tackle", "corner", "goal_kick"):
                if not self.ai_commentary:
                    self._log(f"  {state.time_str()} {result.description} {ball_tag}")
                else:
                    comment = self.commentator.describe(
                        state, result.event_type, result.description, player=holder.name
                    )
                    self._log(f"  {state.time_str()} {comment} {ball_tag}")
                state.set_piece_team = result.set_piece_team

            else:
                # Routine play — log briefly
                self._log(f"  {state.time_str()} {result.description} {ball_tag}")

            # Update ball holder
            if result.new_ball_holder and result.new_ball_holder in state.players:
                # Track who passed so we can block immediate back-passes
                if result.event_type == "pass":
                    state.last_ball_holder = holder.name
                else:
                    state.last_ball_holder = None
                # Fast break: ball won off the opponent → brief transition edge
                new_team = state.players[result.new_ball_holder].team
                if new_team != holder.team and result.event_type in (
                        "interception", "tackle", "misplaced_pass", "clearance"):
                    state.transition_team = new_team
                    state.transition_ttl = 2
                state.ball_holder = result.new_ball_holder
                state.ball_position = state.players[result.new_ball_holder].position
            elif not result.new_ball_holder and result.event_type not in (
                    "goal", "corner", "free_kick", "penalty", "throw_in", "goal_kick"):
                state.last_ball_holder = None
                state.ball_holder = None

            _advance_time(state, int(result.time_seconds * self.time_scale))
            state.play_count += 1

            # ── 6. SUBSTITUTIONS ─────────────────────────────────────────
            # Each window is handled once (the minute spans many plays), so a
            # coach makes at most one change per window rather than one per play.
            if state.minute in SUB_WINDOWS and state.minute not in self._subs_done:
                self._subs_done.add(state.minute)
                for coach in (self.home_coach, self.away_coach):
                    sub = coach.consider_substitution(state)
                    if sub:
                        # Generate personalised briefing for incoming sub
                        if self.ai_coach:
                            briefing = coach.sub_briefing(
                                sub["player_on"], sub["player_off"], state
                            )
                            # Prepend briefing to sub's prompt so they know their mission
                            existing = self.all_player_prompts.get(sub["player_on"], "")
                            self.all_player_prompts[sub["player_on"]] = (
                                f"COACH SAYS: {briefing}\n\n{existing}" if existing
                                else f"COACH SAYS: {briefing}"
                            )
                        _apply_substitution(
                            state, sub, coach.team_side,
                            {n: self.all_player_prompts.get(n, "")
                             for n in coach.bench},
                            self.all_player_prompts,
                        )

    def print_decision_log(self, team: str = None) -> None:
        """Print the full play-by-play decision log. Filter by 'home' or 'away'."""
        for entry in self.decision_log:
            if team and entry["team"] != team:
                continue
            corrected = " [CORRECTED BY EVALUATOR]" if entry["evaluator_corrected"] else ""
            target = f" → {entry['target']}" if entry.get("target") else ""
            direction = f" ({entry['direction']})" if entry.get("direction") else ""
            reasoning = entry.get("reasoning", "") or ""
            ball_pos = entry.get("ball_zone", f"({entry.get('ball_x','?')},{entry.get('ball_y','?')})")
            print(
                f"  {entry['minute']:02d}:{entry['second']:02d}  "
                f"{entry['player']} ({entry['role']}, {entry['team']}){corrected}\n"
                f"    Ball     : {ball_pos}\n"
                f"    Decision : {entry['action']}{target}{direction}\n"
                f"    Reasoning: {reasoning[:120]}\n"
                f"    Outcome  : {entry['outcome']} — {entry['outcome_desc'][:80]}"
            )

    def _play_extra_time(self) -> None:
        """Two 15-minute halves of extra time, then a shootout if still level."""
        self.went_to_extra_time = True
        self._log(f"\n{'═'*60}")
        self._log(f"  EXTRA TIME — level at {self.state.score_str()}")
        self._log(f"{'═'*60}")
        self._play_half(3)
        self._log(f"\n  END OF FIRST PERIOD (ET): {self.state.score_str()}")
        self._play_half(4)
        self._log(f"\n  END OF EXTRA TIME: {self.state.score_str()}")
        if self.state.score["home"] == self.state.score["away"]:
            self._penalty_shootout()

    def _penalty_shootout(self) -> None:
        """Best-of-five spot kicks, then sudden death. Skill-weighted outcomes."""
        from .resolution import penalty_kick_scores
        state = self.state
        self._log(f"\n{'═'*60}")
        self._log("  PENALTY SHOOTOUT")
        self._log(f"{'═'*60}")

        def takers(team: str) -> list:
            ps = [p for p in state.get_team_players(team) if p.role != "GK"]
            ps.sort(key=lambda p: p.shoot_skill, reverse=True)
            return ps or list(state.get_team_players(team))

        def keeper(team: str):
            return next((p for p in state.get_team_players(team) if p.role == "GK"), None)

        tk = {"home": takers("home"), "away": takers("away")}
        gk = {"home": keeper("home"), "away": keeper("away")}
        goals = {"home": 0, "away": 0}
        taken = {"home": 0, "away": 0}

        def kick(team: str) -> None:
            pool = tk[team]
            t = pool[taken[team] % len(pool)] if pool else None
            taken[team] += 1
            opp = "away" if team == "home" else "home"
            scored = penalty_kick_scores(t, gk[opp])
            if scored:
                goals[team] += 1
            tname = self.home_team if team == "home" else self.away_team
            self._log(f"   {tname:<14} {t.name if t else '?':<22} "
                      f"{'GOAL ⚽' if scored else 'MISS ❌'}   "
                      f"[{self.home_team} {goals['home']}-{goals['away']} {self.away_team}]")

        def decided() -> bool:
            h_left = max(0, 5 - taken["home"])
            a_left = max(0, 5 - taken["away"])
            return (goals["home"] > goals["away"] + a_left or
                    goals["away"] > goals["home"] + h_left)

        # Best of five, alternating, stop as soon as mathematically decided.
        for _ in range(5):
            broke = False
            for team in ("home", "away"):
                kick(team)
                if decided():
                    broke = True
                    break
            if broke:
                break

        # Sudden death (equal kicks each round).
        while goals["home"] == goals["away"]:
            kick("home")
            kick("away")

        winner = "home" if goals["home"] > goals["away"] else "away"
        winner_name = self.home_team if winner == "home" else self.away_team
        self.shootout_result = {"home": goals["home"], "away": goals["away"], "winner": winner}
        self._log(f"\n  SHOOTOUT RESULT: {self.home_team} {goals['home']}-{goals['away']} "
                  f"{self.away_team} — {winner_name} go through!")

    def run(self) -> dict:
        """Run the full match (90' + extra time/shootout in knockout mode). Returns result dict."""
        self.setup()

        # First half
        self._play_half(1)

        # Half time
        self._log(f"\n{'─'*60}")
        self._log(f"  HALF TIME: {self.state.score_str()}")
        if self.ai_commentary:
            ht_comment = self.commentator.half_time(self.state)
            self._log(f"  {ht_comment}")
        if self.ai_coach:
            # Coaches deliver half-time talks — updates second-half tactics
            home_talk = self.home_coach.half_time_talk(self.state)
            away_talk = self.away_coach.half_time_talk(self.state)
            # Replace team tactics with coach's adjusted second-half plan
            self._home_tactics = (
                f"## {self.home_team.upper()} — SECOND HALF PLAN\n{home_talk}"
            )
            self._away_tactics = (
                f"## {self.away_team.upper()} — SECOND HALF PLAN\n{away_talk}"
            )
            # Clear any stale coach messages from first half
            self._coach_msg = {"home": "", "away": ""}
            self._coach_msg_ttl = {"home": 0, "away": 0}
        self._log(f"{'─'*60}")
        time.sleep(0.5)

        # Second half
        self._play_half(2)

        # Full time (90')
        self._log(f"\n{'─'*60}")
        self._log(f"  FULL TIME (90'): {self.state.score_str()}")
        if self.ai_commentary:
            ft_comment = self.commentator.full_time(self.state)
            self._log(f"  {ft_comment}")

        # Knockout tie level after 90' → extra time, then a shootout if needed.
        if self.knockout and self.state.score["home"] == self.state.score["away"]:
            self._play_extra_time()

        stats = self.state.stats_summary()
        self._log(f"\n  Stats:")
        self._log(f"    Possession: {self.home_team} {stats['possession']['home']}% — "
                  f"{self.away_team} {stats['possession']['away']}%")
        self._log(f"    Shots:      {self.home_team} {stats['shots']['home']} — "
                  f"{self.away_team} {stats['shots']['away']}")
        self._log(f"    Corners:    {self.home_team} {stats['corners']['home']} — "
                  f"{self.away_team} {stats['corners']['away']}")
        self._log(f"    Fouls:      {self.home_team} {stats['fouls']['home']} — "
                  f"{self.away_team} {stats['fouls']['away']}")
        # Action breakdown from decision log
        from collections import Counter
        actions = Counter(e["action"] for e in self.decision_log)
        outcomes = Counter(e["outcome"] for e in self.decision_log)
        self._log(f"\n  Actions taken: { dict(actions) }")
        self._log(f"  Outcomes:      { dict(outcomes) }")
        self._log(f"\n  {self.evaluator.summary()}")
        self._log(f"  Cards issued: {len(self.referee.cards_issued)}")
        self._log(f"{'='*60}\n")

        # Determine winner (knockout-aware).
        if self.state.score["home"] > self.state.score["away"]:
            winner = "home"
        elif self.state.score["away"] > self.state.score["home"]:
            winner = "away"
        elif self.shootout_result:
            winner = self.shootout_result["winner"]
        else:
            winner = None
        winner_team = (self.home_team if winner == "home"
                       else self.away_team if winner == "away" else None)

        return {
            "home_team": self.home_team,
            "away_team": self.away_team,
            "score": self.state.score,
            "winner": winner,
            "winner_team": winner_team,
            "went_to_extra_time": self.went_to_extra_time,
            "shootout": self.shootout_result,
            "stats": stats,
            "events": [
                {"minute": e.minute, "type": e.event_type, "desc": e.description}
                for e in self.state.events
            ],
            "substitutions": [
                {"minute": s.minute, "team": s.team, "off": s.player_off, "on": s.player_on}
                for s in self.state.substitutions
            ],
            "evaluator_corrections": self.evaluator.corrections_made,
            "cards": self.referee.cards_issued,
            "decision_log": self.decision_log,
        }
