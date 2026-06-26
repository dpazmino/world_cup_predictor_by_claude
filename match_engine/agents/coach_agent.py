"""
Coach Agent — manages starting XI selection, tactical instructions, and substitutions.
One CoachAgent per team, used before the match and at half-time / substitution points.
"""
from __future__ import annotations
from ..game_state import GameState, PlayerState
from ..formations import FORMATIONS, DEFAULT_FORMATION
from .base_agent import llm_call, SMART_MODEL


_ROLE_GROUP = {
    "GK":  "GK",
    "RB":  "DEF", "LB":  "DEF", "CB":  "DEF",
    "RWB": "DEF", "LWB": "DEF",
    "CDM": "MID", "CM":  "MID", "CAM": "MID",
    "RM":  "MID", "LM":  "MID",
    "RW":  "FWD", "LW":  "FWD", "CF":  "FWD",
}


def _infer_group(prompt: str) -> str:
    """Infer position group from the opening lines of the player's prompt."""
    # Use a longer window to be safe, then check in priority order
    text = prompt[:300].lower()

    # 1. GK — unambiguous
    if any(k in text for k in ("goalkeeper", "first-choice goalkeeper", "backup goalkeeper",
                                "third goalkeeper", "number one")):
        return "GK"

    # 2. DEF — check BEFORE FWD to avoid false matches like "strikers" in defender prompts
    if any(k in text for k in ("centre-back", "center-back", " cb ", " rb ", " lb ",
                                "right back", "left back", "central defend",
                                "full back", "full-back", "sweeper",
                                "defender who", "defending", "wing-back",
                                "right-back", "left-back")):
        return "DEF"

    # 3. FWD — only match clear forward-specific phrases
    if any(k in text for k in ("centre-forward", "center-forward", "is a striker",
                                "your striker", "left winger", "right winger",
                                "wide attacker", "wide forward", "attacking forward",
                                "most dangerous attacker", "primary striker",
                                "young striker", "physical striker", "veteran striker",
                                "main striker", "second striker", "young forward",
                                "experienced forward", "direct forward")):
        return "FWD"

    # 4. MID (default)
    return "MID"


def _squad_groups(squad: list[str],
                   squad_prompts: dict[str, str] | None = None) -> dict[str, list[str]]:
    """
    Categorize squad by position group using prompt text when available;
    fall back to fixed-index split.
    """
    if squad_prompts:
        gk, def_, mid, fwd = [], [], [], []
        for name in squad:
            prompt = squad_prompts.get(name, "")
            group = _infer_group(prompt)
            {"GK": gk, "DEF": def_, "MID": mid, "FWD": fwd}[group].append(name)
        return {"GK": gk, "DEF": def_, "MID": mid, "FWD": fwd}
    # Fallback
    return {
        "GK":  squad[0:3],
        "DEF": squad[3:11],
        "MID": squad[11:19],
        "FWD": squad[19:],
    }


def _pick_starting_xi(squad: list[str], formation: str,
                       coach_prompt: str, team_name: str,
                       opponent: str,
                       squad_prompts: dict[str, str] | None = None) -> list[str]:
    """
    Deterministically assign best players to formation roles by position group.
    LLM is only used for tactical notes (not selection) to avoid hallucinations.
    """
    roles = [r for r, _, _ in FORMATIONS.get(formation, FORMATIONS[DEFAULT_FORMATION])]
    groups = _squad_groups(squad, squad_prompts)
    cursors = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}

    selected = []
    for role in roles:
        group = _ROLE_GROUP.get(role, "MID")
        pool = groups[group]
        idx = cursors[group]
        if idx < len(pool):
            selected.append(pool[idx])
            cursors[group] += 1
        else:
            # Overflow — take from bench players not yet selected
            bench = [n for n in squad if n not in selected]
            if bench:
                selected.append(bench[0])

    return selected[:11]


def _choose_formation_offline(coach_prompt: str) -> str:
    """Deterministic formation pick: first formation token named in the coach prompt."""
    for f in FORMATIONS:
        if f in coach_prompt:
            return f
    return DEFAULT_FORMATION


def _consider_substitution_offline(state: GameState, team: str,
                                   bench: list[str], subs_used: int,
                                   squad_prompts: dict[str, str] | None) -> dict | None:
    """Deterministic sub: replace the most-fatigued outfielder with a like-for-like bench player."""
    if subs_used >= 5 or not bench:
        return None
    team_players = state.get_team_players(team)
    high_fatigue = [p for p in team_players if p.fatigue > 65 and p.role != "GK"]
    if not high_fatigue:
        return None
    candidate_off = max(high_fatigue, key=lambda p: p.fatigue)

    # Prefer a bench player from the same position group, else the first available.
    off_group = _ROLE_GROUP.get(candidate_off.role, "MID")
    player_on = bench[0]
    if squad_prompts:
        for name in bench:
            if _infer_group(squad_prompts.get(name, "")) == off_group:
                player_on = name
                break
    return {"player_off": candidate_off.name, "player_on": player_on, "reason": "fatigue"}


def _choose_formation(coach_prompt: str, team_name: str, opponent: str) -> str:
    """Ask coach LLM which formation to play."""
    system = f"{coach_prompt}\n\nRespond ONLY with valid JSON."
    user = f"""
You are the coach of {team_name}. You are about to play {opponent}.

Choose the best formation. Options: {list(FORMATIONS.keys())}

Respond with JSON ONLY:
{{
  "formation": "4-3-3",
  "reasoning": "one sentence"
}}
""".strip()

    result = llm_call(system, user, model=SMART_MODEL, max_tokens=100)
    formation = result.get("formation", DEFAULT_FORMATION)
    return formation if formation in FORMATIONS else DEFAULT_FORMATION


def _consider_substitution(state: GameState, team: str,
                             bench: list[str],
                             coach_prompt: str,
                             subs_used: int) -> dict | None:
    """
    Decide whether to make a substitution. Returns sub dict or None.
    dict: {"player_off": str, "player_on": str, "reason": str}
    """
    if subs_used >= 5:
        return None
    if not bench:
        return None

    team_players = state.get_team_players(team)
    high_fatigue = [p for p in team_players if p.fatigue > 65 and p.role != "GK"]
    if not high_fatigue:
        return None

    high_fatigue.sort(key=lambda p: p.fatigue, reverse=True)
    candidate_off = high_fatigue[0]

    system = f"{coach_prompt}\n\nRespond ONLY with valid JSON."
    user = f"""
## SUBSTITUTION DECISION
Match: {state.score_str()} | Minute: {state.minute}'

## PLAYER TO REPLACE
{candidate_off.name} ({candidate_off.role}) — fatigue: {candidate_off.fatigue:.0f}%

## BENCH AVAILABLE
{chr(10).join(f"  - {name}" for name in bench)}

## DECIDE
Should you substitute {candidate_off.name}? If yes, who comes on?

Respond with JSON ONLY:
{{
  "make_sub": true | false,
  "player_off": "{candidate_off.name}",
  "player_on": "Bench Player Name",
  "reason": "fatigue | tactical | injury"
}}
""".strip()

    result = llm_call(system, user, model=SMART_MODEL, max_tokens=150)
    if not result.get("make_sub", False):
        return None

    player_on = result.get("player_on", "")
    if player_on not in bench:
        player_on = bench[0]   # fallback to first bench player

    return {
        "player_off": candidate_off.name,
        "player_on": player_on,
        "reason": result.get("reason", "fatigue"),
    }


class CoachAgent:
    """Stateful coach for one team across the full match."""

    def __init__(self, team_name: str, team_side: str,
                 squad: list[str], coach_prompt: str,
                 squad_prompts: dict[str, str] | None = None,
                 offline: bool = False):
        self.team_name = team_name
        self.team_side = team_side    # "home" or "away"
        self.full_squad = squad
        self.coach_prompt = coach_prompt
        self.squad_prompts = squad_prompts or {}
        self.offline = offline        # no-LLM deterministic decisions
        self.formation: str = DEFAULT_FORMATION
        self.starting_xi: list[str] = []
        self.bench: list[str] = []
        self.tactical_notes: str = ""

    def prepare_match(self, opponent: str) -> None:
        """Call before kickoff: choose formation and starting XI."""
        if self.offline:
            self.formation = _choose_formation_offline(self.coach_prompt)
        else:
            self.formation = _choose_formation(self.coach_prompt, self.team_name, opponent)
        self.starting_xi = _pick_starting_xi(
            self.full_squad, self.formation, self.coach_prompt,
            self.team_name, opponent,
            squad_prompts=self.squad_prompts,
        )
        self.bench = [n for n in self.full_squad if n not in self.starting_xi]
        xi_safe = ", ".join(n.encode("ascii", "replace").decode("ascii") for n in self.starting_xi)
        print(f"  [{self.team_name}] Formation: {self.formation} | XI: {xi_safe}")

    def consider_substitution(self, state: GameState) -> dict | None:
        subs_used = state.home_subs_used if self.team_side == "home" else state.away_subs_used
        if self.offline:
            sub = _consider_substitution_offline(
                state, self.team_side, self.bench, subs_used, self.squad_prompts
            )
        else:
            sub = _consider_substitution(
                state, self.team_side, self.bench, self.coach_prompt, subs_used
            )
        if sub:
            if sub["player_on"] in self.bench:
                self.bench.remove(sub["player_on"])
        return sub

    def half_time_talk(self, state: GameState) -> str:
        """
        Analyse the first half and return tactical instructions for the second half.
        These replace the generic team tactics in every player's prompt.
        """
        my_score  = state.score.get(self.team_side, 0)
        opp_side  = "away" if self.team_side == "home" else "home"
        opp_score = state.score.get(opp_side, 0)
        my_shots  = state.shots_home if self.team_side == "home" else state.shots_away
        opp_shots = state.shots_away if self.team_side == "home" else state.shots_home
        poss_pct  = state.possession_home if self.team_side == "home" else state.possession_away
        total_poss = state.possession_home + state.possession_away
        poss_str  = f"{round(100*poss_pct/max(1,total_poss))}%" if total_poss else "50%"

        system = f"{self.coach_prompt}\n\nRespond with plain text — max 4 sentences. No JSON."
        user = f"""
HALF TIME — {self.team_name}
Score: {my_score}-{opp_score} {'(WINNING)' if my_score > opp_score else '(LOSING)' if my_score < opp_score else '(DRAWING)'}
First half: {my_shots} shots for us, {opp_shots} for them. Possession: {poss_str}.

Give your players 3-4 sharp tactical instructions for the second half.
Be specific: who should push forward, who should press higher, when to shoot.
If losing — be urgent and attacking. If winning — be composed and defensive.
""".strip()
        from .base_agent import llm_text
        talk = llm_text(system, user, model=SMART_MODEL, max_tokens=150)
        print(f"\n  [{self.team_name} HALF-TIME TALK]\n  {talk}\n")
        return talk

    def in_game_message(self, state: GameState, event: str) -> str:
        """
        One or two urgent tactical sentences after a key event (goal scored/conceded, red card).
        Injected into the next several player decision prompts.
        """
        my_score  = state.score.get(self.team_side, 0)
        opp_side  = "away" if self.team_side == "home" else "home"
        opp_score = state.score.get(opp_side, 0)

        system = f"{self.coach_prompt}\n\nRespond with plain text — 1-2 sentences max. No JSON."
        user = f"""
{state.minute}' — {event}
Score is now {my_score}-{opp_score}.
Give ONE urgent tactical instruction to your players. Be direct.
""".strip()
        from .base_agent import llm_text
        return llm_text(system, user, model=SMART_MODEL, max_tokens=60)

    def sub_briefing(self, incoming: str, outgoing: str, state: GameState) -> str:
        """
        Personal instructions for the substitute coming on.
        Returns a short paragraph injected into the sub's prompt.
        """
        my_score  = state.score.get(self.team_side, 0)
        opp_side  = "away" if self.team_side == "home" else "home"
        opp_score = state.score.get(opp_side, 0)
        situation = (
            "We are winning — stay disciplined and hold the result." if my_score > opp_score else
            "We are losing — you need to make something happen. Be direct and attack." if my_score < opp_score else
            "It is level — take risks, we want to win this."
        )
        incoming_prompt = self.squad_prompts.get(incoming, "")
        role_hint = incoming_prompt[:120] if incoming_prompt else incoming

        system = f"{self.coach_prompt}\n\nRespond with plain text — 2-3 sentences. No JSON."
        user = f"""
{incoming} is coming on for {outgoing} at {state.minute} minutes.
{situation}
Score: {my_score}-{opp_score}.
Player: {role_hint}

Give {incoming} specific personal instructions — their role, what to focus on, one or two key tasks.
""".strip()
        from .base_agent import llm_text
        briefing = llm_text(system, user, model=SMART_MODEL, max_tokens=100)
        print(f"  [{self.team_name} SUB BRIEF] {incoming}: {briefing}")
        return briefing
