"""
Pure-Python play resolution engine.

LLM agents decide INTENT (what to do). This module decides OUTCOME
(what actually happens) using field geometry and probability.
No LLM calls here.
"""
from __future__ import annotations
import random
import math
from dataclasses import dataclass
from typing import Optional
from .game_state import GameState, PlayerState, Position
from .field import (
    is_offside, pass_lane_blocked, in_home_penalty_area,
    in_away_penalty_area, in_penalty_area, is_out_of_bounds,
    clamp_to_pitch, HOME_PENALTY_SPOT, AWAY_PENALTY_SPOT,
    PITCH_LENGTH, PITCH_WIDTH, CENTER_X, CENTER_Y,
    GOAL_Y_MIN, GOAL_Y_MAX,
)


@dataclass
class PlayResult:
    success: bool
    new_ball_holder: Optional[str]      # None = set piece / keeper hold
    new_ball_position: Position
    event_type: str                      # "pass", "interception", "shot_goal",
                                         # "shot_saved", "shot_blocked", "dribble",
                                         # "foul", "offside", "corner", "goal_kick",
                                         # "throw_in", "cross_success", "cross_cleared"
    description: str
    scoring_team: Optional[str] = None   # set if goal
    time_seconds: int = 15               # simulated seconds this play took
    new_phase: str = "open_play"
    set_piece_team: Optional[str] = None
    foul_by: Optional[str] = None        # defender who committed a foul (for cards)


def _fatigue_factor(player: PlayerState) -> float:
    """Returns 0.7-1.0 multiplier. More fatigue = worse performance."""
    return 1.0 - player.fatigue * 0.003


def _shot_on_target_prob(distance: float, angle_deg: float, fatigue: float) -> float:
    """Probability shot is on target."""
    base = max(0.10, 0.85 - distance * 0.022)
    angle_bonus = max(0.0, (45 - abs(angle_deg - 90)) / 90)
    return base * (1 - fatigue * 0.002) * (0.6 + 0.4 * angle_bonus)


def _keeper_save_prob(keeper: PlayerState, distance: float, shot_y: float) -> float:
    """
    Keeper save probability — factored by keeper's own skill stats.
    GK intercept_skill (from stats_loader) encodes reflexes + positioning.
    """
    from .stats_loader import get_player_stats
    gk_stats = get_player_stats(keeper.name)
    # GK skill: use overall as proxy for reflexes (0.0-1.0); fallback 0.72
    gk_skill = gk_stats["overall"] if gk_stats else 0.72

    if distance < 6:
        base = 0.44 + gk_skill * 0.15   # point-blank
    else:
        base = min(0.94, 0.62 + distance * 0.016 + gk_skill * 0.18)

    # Positional bonus — how close is keeper to where the shot crosses the line
    keeper_dist_to_shot = abs(keeper.position.y - shot_y)
    pos_bonus = max(0.0, 1.0 - keeper_dist_to_shot / 4.5)
    return min(0.96, base * (0.45 + 0.55 * pos_bonus))


def _keeper_save_narrative(keeper: PlayerState, shooter: PlayerState,
                            shot_y: float, saved: bool) -> str:
    """Generate a descriptive line for the GK vs shooter duel."""
    goal_center_y = 34.0
    side = "to his left" if shot_y < goal_center_y else "to his right"
    height = "low" if abs(shot_y - goal_center_y) < 2 else "diving"
    if saved:
        verbs = ["pushes it away", "tips it round the post", "palms it clear",
                 "makes a brilliant stop", "dives and saves"]
        verb = verbs[hash(keeper.name + shooter.name) % len(verbs)]
        return f"{keeper.name} dives {side} and {verb}!"
    else:
        verbs = ["can't reach it", "is beaten", "had no chance",
                 "gets a fingertip but can't stop it", "is wrong-footed"]
        verb = verbs[hash(shooter.name + keeper.name) % len(verbs)]
        return f"{keeper.name} {verb}!"


def _pass_success_prob(distance: float, pressure: float, fatigue: float) -> float:
    """pressure = nearest defender distance (metres). Higher = less pressure."""
    # Base: ~98% for short passes, decaying with distance. Real WC pass completion
    # is ~82%, so the typical pass should land comfortably, not flip a coin.
    base = max(0.74, 0.985 - distance * 0.0045)
    # Pressure only bites when a defender is genuinely close.
    pressure_factor = min(1.0, 0.78 + pressure * 0.11)
    return min(0.985, base * pressure_factor * _fatigue_factor_value(fatigue))


def _fatigue_factor_value(fatigue: float) -> float:
    return 1.0 - fatigue * 0.003


def _dribble_success_prob(pressure: float, fatigue: float) -> float:
    """pressure = nearest defender distance."""
    if pressure > 8:
        return 0.92
    base = max(0.35, 0.40 + pressure * 0.065)
    return base * _fatigue_factor_value(fatigue)


def _award_corner(state: GameState, defending_team: str) -> None:
    """Credit a corner to whichever side is attacking (the conceding side defends)."""
    state.corners_home += (1 if defending_team == "away" else 0)
    state.corners_away += (1 if defending_team == "home" else 0)


def _gk_reflex(keeper: Optional[PlayerState]) -> float:
    """Keeper overall skill (0-1), used for shootouts and conversion math."""
    if not keeper:
        return 0.72
    from .stats_loader import get_player_stats
    gk_stats = get_player_stats(keeper.name)
    return gk_stats["overall"] if gk_stats else 0.72


def _gk_can_engage(state: GameState, p: PlayerState) -> bool:
    """A keeper should only contest a loose/misplaced ball near their own goal —
    never out in midfield. Outfield players always engage."""
    if p.role != "GK":
        return True
    own_goal_x = 0.0 if state.team_attacks_right(p.team) else 105.0
    return abs(state.ball_position.x - own_goal_x) < 25.0


def _shot_aim(shooter: PlayerState, distance: float) -> tuple[float, int]:
    """
    Choose where in the goal the shot is aimed. Better finishers (and closer
    range) place the ball nearer a post; tired/long-range shots scatter centrally.
    Returns (target_y, side) where side is +1 (toward GOAL_Y_MAX) or -1.
    """
    placement = shooter.shoot_skill * (1.0 - shooter.fatigue * 0.003)
    spread = max(0.4, (1.0 - placement) * 2.8 + distance * 0.03)
    side = 1 if random.random() < 0.5 else -1
    post = GOAL_Y_MAX if side > 0 else GOAL_Y_MIN
    aim = post - side * (0.35 + abs(random.gauss(0.0, spread)))
    return max(GOAL_Y_MIN - 1.0, min(GOAL_Y_MAX + 1.0, aim)), side


def _goal_from_shot(state: GameState, shooter: PlayerState) -> PlayResult:
    """Resolve a shot on goal: block → off-target → woodwork → keeper duel → goal."""
    attacking_right = state.team_attacks_right(shooter.team)
    goal_x = 105.0 if attacking_right else 0.0
    goal_center = Position(goal_x, CENTER_Y)
    pos = shooter.position

    distance = pos.distance_to(goal_center)
    dx = goal_x - pos.x
    dy = CENTER_Y - pos.y
    angle_deg = math.degrees(math.atan2(abs(dy), abs(dx))) if dx != 0 else 90.0

    defending_team = "away" if shooter.team == "home" else "home"
    keeper = next((p for p in state.get_team_players(defending_team)
                   if p.role == "GK"), None)

    # ── BLOCK: an outfield defender throws himself in the shot lane ──────────
    outfield = [p for p in state.get_team_players(defending_team) if p.role != "GK"]
    blocker = pass_lane_blocked(pos, goal_center, outfield, intercept_radius=2.0)
    if blocker:
        block_prob = min(0.70, 0.30 + blocker.intercept_skill * 0.45)
        if random.random() < block_prob:
            if random.random() < 0.15:
                corner_y = 0.0 if pos.y < CENTER_Y else PITCH_WIDTH
                _award_corner(state, defending_team)
                return PlayResult(
                    success=False, new_ball_holder=None,
                    new_ball_position=Position(goal_x, corner_y),
                    event_type="shot_blocked",
                    description=f"{shooter.name}'s shot is blocked by {blocker.name} — corner!",
                    time_seconds=5, new_phase="corner", set_piece_team=shooter.team,
                )
            loose = clamp_to_pitch(Position(pos.x + dx * 0.15, pos.y + random.uniform(-4, 4)))
            return PlayResult(
                success=False, new_ball_holder=None, new_ball_position=loose,
                event_type="shot_blocked",
                description=f"{blocker.name} blocks {shooter.name}'s effort!",
                time_seconds=5,
            )

    on_target_prob = _shot_on_target_prob(distance, angle_deg, shooter.fatigue) * shooter.shoot_skill
    is_on_target = random.random() < min(0.95, on_target_prob)
    shot_y, side = _shot_aim(shooter, distance)

    if not is_on_target:
        # Off target — the ball runs out for a goal kick. A slice are deflected
        # wide off a defender for a corner (clean misses do NOT earn corners).
        if random.random() < 0.09:
            corner_y = 0.0 if shot_y < CENTER_Y else PITCH_WIDTH
            _award_corner(state, defending_team)
            return PlayResult(
                success=False, new_ball_holder=None,
                new_ball_position=Position(goal_x, corner_y), event_type="corner",
                description=f"{shooter.name} shoots — deflected wide, corner.",
                time_seconds=5, new_phase="corner", set_piece_team=shooter.team,
            )
        gk_pos = keeper.position if keeper else goal_center
        return PlayResult(
            success=False, new_ball_holder=keeper.name if keeper else None,
            new_ball_position=gk_pos, event_type="shot_missed",
            description=f"{shooter.name} shoots — off target.",
            time_seconds=5, new_phase="goal_kick", set_piece_team=defending_team,
        )

    # ── WOODWORK: on target but smashed against the frame ───────────────────
    post_y = GOAL_Y_MAX if side > 0 else GOAL_Y_MIN
    if abs(shot_y - post_y) < 0.35 and random.random() < 0.10:
        if random.random() < 0.5:
            rebound = clamp_to_pitch(Position(
                goal_x - (8 if attacking_right else -8),
                CENTER_Y + random.uniform(-6, 6)))
            return PlayResult(
                success=False, new_ball_holder=None, new_ball_position=rebound,
                event_type="woodwork",
                description=f"OFF THE POST! {shooter.name} rattles the woodwork — ball loose!",
                time_seconds=5,
            )
        corner_y = 0.0 if shot_y < CENTER_Y else PITCH_WIDTH
        _award_corner(state, defending_team)
        return PlayResult(
            success=False, new_ball_holder=None,
            new_ball_position=Position(goal_x, corner_y), event_type="woodwork",
            description=f"OFF THE POST! {shooter.name} inches away — corner.",
            time_seconds=5, new_phase="corner", set_piece_team=shooter.team,
        )

    # ── On target — GK vs Shooter duel ──────────────────────────────────────
    if keeper:
        save_prob = _keeper_save_prob(keeper, distance, shot_y)
        effective_save = save_prob * (1.0 - shooter.shoot_skill * 0.25)
        if random.random() < effective_save:
            duel_narrative = _keeper_save_narrative(keeper, shooter, shot_y, True)
            r = random.random()
            if r < 0.22:
                # Parried into the danger zone — rebound loose in the box
                rebound = clamp_to_pitch(Position(
                    goal_x - (7 if attacking_right else -7),
                    CENTER_Y + random.uniform(-7, 7)))
                return PlayResult(
                    success=False, new_ball_holder=None, new_ball_position=rebound,
                    event_type="shot_saved",
                    description=f"{shooter.name} shoots — {duel_narrative} Parried, rebound loose!",
                    time_seconds=4,
                )
            if r < 0.42:
                corner_y = 0.0 if shot_y < CENTER_Y else PITCH_WIDTH
                _award_corner(state, defending_team)
                return PlayResult(
                    success=False, new_ball_holder=None,
                    new_ball_position=Position(goal_x, corner_y),
                    event_type="shot_saved",
                    description=f"{shooter.name} shoots — {duel_narrative} Corner.",
                    time_seconds=5, new_phase="corner", set_piece_team=shooter.team,
                )
            return PlayResult(
                success=False, new_ball_holder=keeper.name,
                new_ball_position=keeper.position, event_type="shot_saved",
                description=f"{shooter.name} shoots — {duel_narrative}",
                time_seconds=6, new_phase="open_play",
            )

    # ── GOAL! ─────────────────────────────────────────────────────────────────
    gk_line = _keeper_save_narrative(keeper, shooter, shot_y, False) if keeper else ""
    return PlayResult(
        success=True,
        new_ball_holder=None,
        new_ball_position=Position(goal_x, shot_y),   # goalmouth, for a sensible log tag
        event_type="goal",
        description=f"GOAL! {shooter.name} scores! {gk_line}",
        scoring_team=shooter.team,
        time_seconds=5,
        new_phase="kickoff",
    )


def _maybe_foul(state: GameState, holder: PlayerState) -> Optional[PlayResult]:
    """
    Defensive foul on the ball carrier, independent of the chosen action.
    Rate rises with how tight the nearest defender is. A foul in the defending
    team's box is a penalty.
    """
    if holder.role == "GK":
        return None
    defending_team = "away" if holder.team == "home" else "home"
    defenders = state.get_team_players(defending_team)
    nd = min(defenders, key=lambda p: holder.position.distance_to(p.position), default=None)
    if not nd:
        return None
    d = holder.position.distance_to(nd.position)
    if d > 12.0:
        return None

    in_box = in_penalty_area(holder.position, defending_team)
    if in_box:
        # Defenders are cautious in their own box — genuine penalties are rare.
        if d < 2.5 and random.random() < 0.04:
            state.fouls_home += (1 if defending_team == "home" else 0)
            state.fouls_away += (1 if defending_team == "away" else 0)
            return PlayResult(
                success=False, new_ball_holder=None, new_ball_position=holder.position,
                event_type="foul", description=f"{nd.name} fouls {holder.name} — Penalty!",
                time_seconds=20, new_phase="penalty", set_piece_team=holder.team,
                foul_by=nd.name,
            )
        return None

    # Rates tuned for man-marking positioning (defenders legitimately sit ~3m off their
    # mark, so per-encounter foul probability is lower than under loose zonal spacing).
    base = 0.12 if d < 3.0 else 0.075 if d < 7.0 else 0.035
    if random.random() >= base:
        return None

    state.fouls_home += (1 if defending_team == "home" else 0)
    state.fouls_away += (1 if defending_team == "away" else 0)
    return PlayResult(
        success=False, new_ball_holder=None, new_ball_position=holder.position,
        event_type="foul",
        description=f"{nd.name} fouls {holder.name} — free kick.",
        time_seconds=18, new_phase="free_kick", set_piece_team=holder.team,
        foul_by=nd.name,
    )


def resolve_action(state: GameState, action: dict) -> PlayResult:
    """
    Main entry point. `action` is the dict from PlayerAgent.decide_with_ball().

    Required keys: "action" (str)
    Optional: "target_player", "target_zone", "direction", "reasoning"
    """
    if not state.ball_holder:
        # Loose ball — won by whoever reaches it first (distance discounted by pace).
        # Keepers don't chase loose balls out in midfield.
        all_players = [p for p in state.players.values() if p.is_on_pitch]
        contenders = [p for p in all_players if _gk_can_engage(state, p)] or all_players
        nearest = min(contenders,
                      key=lambda p: p.position.distance_to(state.ball_position) / (0.6 + 0.4 * p.pace))
        return PlayResult(
            success=True,
            new_ball_holder=nearest.name,
            new_ball_position=nearest.position,
            event_type="loose_ball",
            description=f"{nearest.name} collects the loose ball.",
            time_seconds=5,
        )

    holder = state.players[state.ball_holder]

    # A defender under-the-ball may foul the carrier regardless of intended action.
    foul = _maybe_foul(state, holder)
    if foul:
        return foul

    act = action.get("action", "hold").lower()

    if act == "shoot":
        attacking_team = holder.team
        if attacking_team == "home":
            stat = "shots_home"
        else:
            stat = "shots_away"
        setattr(state, stat, getattr(state, stat) + 1)
        return _goal_from_shot(state, holder)

    elif act == "pass":
        return _resolve_pass(state, holder, action)

    elif act == "cross":
        return _resolve_cross(state, holder, action)

    elif act == "dribble":
        return _resolve_dribble(state, holder, action)

    elif act == "clear":
        return _resolve_clear(state, holder)

    else:  # "hold" or unknown
        return PlayResult(
            success=True,
            new_ball_holder=holder.name,
            new_ball_position=holder.position,
            event_type="hold",
            description=f"{holder.name} holds the ball.",
            time_seconds=8,
        )


def _resolve_pass(state: GameState, holder: PlayerState, action: dict) -> PlayResult:
    target_name = action.get("target_player")
    defending_team = "away" if holder.team == "home" else "home"
    defenders = state.get_team_players(defending_team)

    attacking_right = state.team_attacks_right(holder.team)

    # Find target player — reject passing back to whoever just gave us the ball
    target: Optional[PlayerState] = None
    if target_name and target_name in state.players:
        t = state.players[target_name]
        if t.team == holder.team and t.name != state.last_ball_holder:
            target = t

    if target is None:
        # Forward-biased fallback: prefer teammates further upfield, exclude last passer
        teammates = [p for p in state.get_team_players(holder.team)
                     if p.name != holder.name and p.name != state.last_ball_holder]
        if not teammates:
            teammates = [p for p in state.get_team_players(holder.team)
                         if p.name != holder.name]
        if teammates:
            def _fwd_score(p: PlayerState) -> float:
                fwd_x = p.position.x if attacking_right else (105 - p.position.x)
                dist = holder.position.distance_to(p.position)
                return fwd_x - max(0, 10 - dist)
            teammates.sort(key=_fwd_score, reverse=True)
            target = teammates[0]

    if not target:
        return PlayResult(
            success=False,
            new_ball_holder=None,
            new_ball_position=holder.position,
            event_type="misplaced_pass",
            description=f"{holder.name} misplaces the pass — no target found.",
            time_seconds=10,
        )

    dist = holder.position.distance_to(target.position)

    # Offside check
    if is_offside(target, target.position, defenders, attacking_right):
        return PlayResult(
            success=False,
            new_ball_holder=None,
            new_ball_position=holder.position,
            event_type="offside",
            description=f"OFFSIDE! {target.name} caught offside.",
            time_seconds=10,
            new_phase="free_kick",
            set_piece_team=defending_team,
        )

    # Interception check — defender's intercept_skill, scaled by pass distance
    # (a longer ball gives the defender more time to read and step across).
    # Tuned low: even a defender in the lane only picks off a minority of passes.
    interceptor = pass_lane_blocked(holder.position, target.position, defenders,
                                    intercept_radius=1.2)
    if interceptor and not _gk_can_engage(state, interceptor):
        interceptor = None
    if interceptor:
        reach = interceptor.intercept_skill * (0.22 + 0.20 * min(1.0, dist / 28.0))
        if random.random() < reach:
            return PlayResult(
                success=False,
                new_ball_holder=interceptor.name,
                new_ball_position=interceptor.position,
                event_type="interception",
                description=f"{interceptor.name} intercepts {holder.name}'s pass!",
                time_seconds=12,
            )

    # Pressure from nearest defender
    nearest_def = min(defenders, key=lambda p: holder.position.distance_to(p.position),
                      default=None)
    pressure = nearest_def.position.distance_to(holder.position) if nearest_def else 10.0

    # Through-ball risk: ambitious long forward passes complete less often, but a
    # quick runner beating a slow defensive line gets on the end of more of them.
    fwd_gain = (target.position.x - holder.position.x) * (1 if attacking_right else -1)
    through_penalty = 1.0
    if fwd_gain > 18 and dist > 22:
        avg_def_pace = (sum(d.pace for d in defenders) / len(defenders)) if defenders else 0.70
        pace_edge = target.pace - avg_def_pace
        through_penalty = max(0.55, min(1.0, 0.80 + pace_edge * 0.6))
    # Fast-break edge while the opponent is out of shape.
    transition_boost = 1.08 if getattr(state, "transition_team", None) == holder.team else 1.0

    # Skill raises the completion floor rather than scaling the whole prob, so
    # a good passer in space completes ~95%+ instead of being capped low.
    skill_factor = 0.88 + 0.12 * holder.pass_skill
    prob = (_pass_success_prob(dist, pressure, holder.fatigue)
            * skill_factor * through_penalty * transition_boost)
    success = random.random() < min(0.98, prob)
    if success:
        return PlayResult(
            success=True,
            new_ball_holder=target.name,
            new_ball_position=target.position,
            event_type="pass",
            description=f"{holder.name} passes to {target.name}.",
            time_seconds=max(8, int(dist * 0.5)),
        )
    else:
        # Misplaced — recovered by the nearest defender (but not the keeper unless
        # the ball is near his own goal).
        recoverers = [d for d in defenders if _gk_can_engage(state, d)] or defenders
        nearest_loose = min(recoverers, key=lambda p: target.position.distance_to(p.position),
                            default=None)
        loose_pos = Position(
            x=(holder.position.x + target.position.x) / 2 + random.uniform(-5, 5),
            y=(holder.position.y + target.position.y) / 2 + random.uniform(-5, 5),
        )
        loose_pos = clamp_to_pitch(loose_pos)
        return PlayResult(
            success=False,
            new_ball_holder=nearest_loose.name if nearest_loose else None,
            new_ball_position=loose_pos,
            event_type="misplaced_pass",
            description=f"{holder.name}'s pass goes astray — {nearest_loose.name if nearest_loose else 'lost'} wins it.",
            time_seconds=10,
        )


def _resolve_dribble(state: GameState, holder: PlayerState, action: dict) -> PlayResult:
    direction = action.get("direction", "forward")
    attacking_right = state.team_attacks_right(holder.team)

    dir_map = {
        "forward": (12.0, 0.0) if attacking_right else (-12.0, 0.0),
        "left":    (4.0, -8.0) if attacking_right else (-4.0, 8.0),
        "right":   (4.0, 8.0)  if attacking_right else (-4.0, -8.0),
        "backward": (-10.0, 0.0) if attacking_right else (10.0, 0.0),
        "into_box": (15.0, 0.0) if attacking_right else (-15.0, 0.0),
    }
    dx, dy = dir_map.get(direction, (8.0, 0.0))

    target_pos = clamp_to_pitch(Position(
        x=holder.position.x + dx + random.uniform(-2, 2),
        y=holder.position.y + dy + random.uniform(-2, 2),
    ))

    # Check out of bounds
    oob = is_out_of_bounds(target_pos)
    if oob == "touchline":
        defending_team = "away" if holder.team == "home" else "home"
        return PlayResult(
            success=False,
            new_ball_holder=None,
            new_ball_position=target_pos,
            event_type="throw_in",
            description=f"{holder.name} dribbles out — throw-in to {holder.team}.",
            time_seconds=12,
            new_phase="throw_in",
            set_piece_team=holder.team,
        )

    defending_team = "away" if holder.team == "home" else "home"
    defenders = state.get_team_players(defending_team)
    nearest_def = min(defenders, key=lambda p: holder.position.distance_to(p.position),
                      default=None)
    pressure = nearest_def.position.distance_to(holder.position) if nearest_def else 10.0

    transition_boost = 1.08 if getattr(state, "transition_team", None) == holder.team else 1.0
    # A quick dribbler beats a slower defender; a quick defender shuts him down.
    pace_edge = holder.pace - (nearest_def.pace if nearest_def else 0.70)
    pace_mult = max(0.80, min(1.20, 1.0 + pace_edge * 0.5))
    success = random.random() < (_dribble_success_prob(pressure, holder.fatigue)
                                 * holder.dribble_skill * transition_boost * pace_mult)
    if success:
        return PlayResult(
            success=True,
            new_ball_holder=holder.name,
            new_ball_position=target_pos,
            event_type="dribble",
            description=f"{holder.name} drives forward with the ball.",
            time_seconds=15,
        )
    else:
        # Tackled
        tackler = nearest_def
        foul = random.random() < 0.25
        if foul:
            state.fouls_home += (1 if defending_team == "home" else 0)
            state.fouls_away += (1 if defending_team == "away" else 0)
            # Foul inside the area the defender is protecting → penalty.
            in_box = in_penalty_area(holder.position, defending_team)
            phase = "penalty" if in_box else "free_kick"
            piece = "Penalty!" if in_box else "free kick."
            return PlayResult(
                success=False,
                new_ball_holder=None,
                new_ball_position=holder.position,
                event_type="foul",
                description=f"{tackler.name if tackler else 'Defender'} fouls {holder.name} — {piece}",
                time_seconds=20,
                new_phase=phase,
                set_piece_team=holder.team,
                foul_by=tackler.name if tackler else None,
            )
        return PlayResult(
            success=False,
            new_ball_holder=tackler.name if tackler else None,
            new_ball_position=holder.position,
            event_type="tackle",
            description=f"{tackler.name if tackler else 'Defender'} wins the ball from {holder.name}.",
            time_seconds=12,
        )


def _resolve_cross(state: GameState, holder: PlayerState, action: dict) -> PlayResult:
    attacking_right = state.team_attacks_right(holder.team)
    goal_x = 105.0 if attacking_right else 0.0

    target_zone = action.get("target_zone", "six_yard_box")
    if "back_post" in target_zone:
        target_y = GOAL_Y_MIN + 1.0 if holder.position.y > CENTER_Y else GOAL_Y_MAX - 1.0
    elif "far_post" in target_zone:
        target_y = GOAL_Y_MAX - 1.0 if holder.position.y < CENTER_Y else GOAL_Y_MIN + 1.0
    else:
        target_y = CENTER_Y + random.uniform(-6, 6)

    target_pos = Position(goal_x - (8 if attacking_right else -8), target_y)

    # Find attacker near target
    teammates = [p for p in state.get_team_players(holder.team)
                 if p.name != holder.name and p.role in ("CF", "CAM", "RW", "LW", "CM")]
    attacker = min(teammates, key=lambda p: p.position.distance_to(target_pos),
                   default=None)

    defending_team = "away" if holder.team == "home" else "home"
    defenders = state.get_team_players(defending_team)
    defender_near = min(defenders, key=lambda p: p.position.distance_to(target_pos),
                        default=None)

    cross_dist = holder.position.distance_to(target_pos)
    cross_quality = max(0.30, 0.80 - cross_dist * 0.005)

    if random.random() > cross_quality:
        # Poor cross — cleared; sometimes deflected behind for a corner.
        if random.random() < 0.56:
            _award_corner(state, defending_team)
            return PlayResult(
                success=False, new_ball_holder=None,
                new_ball_position=Position(goal_x, 0.0 if holder.position.y < CENTER_Y else PITCH_WIDTH),
                event_type="corner",
                description=f"{holder.name}'s cross is deflected behind — corner.",
                time_seconds=8, new_phase="corner", set_piece_team=holder.team,
            )
        return PlayResult(
            success=False,
            new_ball_holder=defender_near.name if defender_near else None,
            new_ball_position=target_pos,
            event_type="cross_cleared",
            description=f"{holder.name}'s cross is cleared by {defender_near.name if defender_near else 'the defense'}.",
            time_seconds=12,
        )

    # ── Aerial duel: proximity to the ball + physicality decide who rises ──
    def _aerial_rating(p: Optional[PlayerState]) -> float:
        if not p:
            return -1.0
        closeness = max(0.0, 6.0 - p.position.distance_to(target_pos)) * 0.12
        return p.physical + closeness

    att_rating = _aerial_rating(attacker)
    def_rating = _aerial_rating(defender_near)
    attacker_wins = attacker is not None and random.random() < (
        0.5 if (att_rating + def_rating) <= 0 else att_rating / (att_rating + def_rating))

    if attacker_wins:
        # Header on goal — quality scales with finishing AND aerial physicality
        setattr(state, f"shots_{holder.team}", getattr(state, f"shots_{holder.team}") + 1)
        keeper = next((p for p in state.get_team_players(defending_team)
                       if p.role == "GK"), None)
        goal_prob = (0.03 + attacker.shoot_skill * 0.10 + attacker.physical * 0.06) * _fatigue_factor(attacker)
        goal_prob = max(0.03, goal_prob - _gk_reflex(keeper) * 0.05)
        if random.random() < goal_prob:
            return PlayResult(
                success=True,
                new_ball_holder=None,
                new_ball_position=Position(state.attacking_goal_x(holder.team), CENTER_Y),
                event_type="goal",
                description=f"GOAL! {attacker.name} heads home {holder.name}'s cross!",
                scoring_team=holder.team,
                time_seconds=10,
                new_phase="kickoff",
            )
        return PlayResult(
            success=False,
            new_ball_holder=keeper.name if keeper else None,
            new_ball_position=Position(goal_x, CENTER_Y),
            event_type="shot_saved",
            description=f"{attacker.name} heads — saved!",
            time_seconds=10,
        )

    # Defender wins aerial — sometimes heads it behind for a corner.
    if random.random() < 0.30:
        _award_corner(state, defending_team)
        return PlayResult(
            success=False, new_ball_holder=None,
            new_ball_position=Position(goal_x, 0.0 if holder.position.y < CENTER_Y else PITCH_WIDTH),
            event_type="corner",
            description=f"{defender_near.name if defender_near else 'Defender'} heads behind — corner.",
            time_seconds=8, new_phase="corner", set_piece_team=holder.team,
        )
    return PlayResult(
        success=False,
        new_ball_holder=defender_near.name if defender_near else None,
        new_ball_position=target_pos,
        event_type="cross_cleared",
        description=f"{defender_near.name if defender_near else 'Defender'} wins the header.",
        time_seconds=10,
    )


def _resolve_clear(state: GameState, holder: PlayerState) -> PlayResult:
    """Defensive clearance — ball travels long, changes possession likely."""
    attacking_right = state.team_attacks_right(holder.team)

    # A clearance under pressure near the carrier's own goal can deflect behind.
    own_goal_x = 0.0 if attacking_right else 105.0
    near_own_goal = abs(holder.position.x - own_goal_x) < 22
    if near_own_goal and random.random() < 0.46:
        _award_corner(state, holder.team)   # holder's team is defending → concede corner
        corner_y = 0.0 if holder.position.y < CENTER_Y else PITCH_WIDTH
        return PlayResult(
            success=False, new_ball_holder=None,
            new_ball_position=Position(own_goal_x, corner_y), event_type="corner",
            description=f"{holder.name}'s clearance is charged down behind — corner.",
            time_seconds=8, new_phase="corner",
            set_piece_team="away" if holder.team == "home" else "home",
        )

    clear_x = holder.position.x + (35 if attacking_right else -35) + random.uniform(-10, 10)
    clear_y = CENTER_Y + random.uniform(-15, 15)
    clear_pos = clamp_to_pitch(Position(clear_x, clear_y))

    defending_team = "away" if holder.team == "home" else "home"
    all_nearby = list(state.players.values())
    nearest = min(all_nearby, key=lambda p: p.position.distance_to(clear_pos),
                  default=None)
    return PlayResult(
        success=True,
        new_ball_holder=nearest.name if nearest else None,
        new_ball_position=clear_pos,
        event_type="clearance",
        description=f"{holder.name} clears the ball long.",
        time_seconds=10,
    )


# Per-role running load — wide players and box-to-box midfielders cover the
# most ground; keepers and centre-backs the least.
_ROLE_FATIGUE_MULT = {
    "GK": 0.35, "CB": 0.80, "RB": 1.15, "LB": 1.15, "RWB": 1.30, "LWB": 1.30,
    "CDM": 0.95, "CM": 1.20, "CAM": 1.05, "RM": 1.20, "LM": 1.20,
    "RW": 1.25, "LW": 1.25, "CF": 1.05, "ST": 1.05,
}


def apply_fatigue(state: GameState, action: dict, elapsed_seconds: int) -> None:
    """Update fatigue for all players based on time, role, and activity."""
    base_rate = 0.018    # per second of play
    sprint_bonus = 0.08  # extra for ball holder active actions

    # Players near the ball (within ~20m) work harder — pressing/supporting.
    ball = state.ball_position
    for p in state.players.values():
        if not p.is_on_pitch:
            continue
        role_mult = _ROLE_FATIGUE_MULT.get(p.role, 1.0)
        proximity = 1.25 if p.position.distance_to(ball) < 20 else 1.0
        # Higher stamina resists fatigue; low stamina tires faster.
        stamina_mult = max(0.80, min(1.20, 1.0 - (p.stamina - 0.75) * 0.6))
        p.fatigue = min(100.0, p.fatigue + base_rate * elapsed_seconds * role_mult * proximity * stamina_mult)

    # Ball holder works harder
    if state.ball_holder and state.ball_holder in state.players:
        act = action.get("action", "hold")
        if act in ("dribble", "shoot", "cross"):
            state.players[state.ball_holder].fatigue = min(
                100.0,
                state.players[state.ball_holder].fatigue + sprint_bonus,
            )


def _pick_penalty_taker(state: GameState, team: str) -> Optional[PlayerState]:
    """Best on-pitch penalty taker: highest finisher among the forward line."""
    on_pitch = state.get_team_players(team)
    candidates = [p for p in on_pitch if p.role in ("CF", "ST", "CAM", "RW", "LW", "CM")]
    pool = candidates or [p for p in on_pitch if p.role != "GK"] or on_pitch
    return max(pool, key=lambda p: p.shoot_skill, default=None)


def penalty_kick_scores(taker: Optional[PlayerState],
                        keeper: Optional[PlayerState]) -> bool:
    """Skill-weighted penalty outcome — used for in-match pens and shootouts."""
    taker_skill = taker.shoot_skill if taker else 0.6
    fatigue_pen = (taker.fatigue * 0.0008) if taker else 0.0
    goal_prob = 0.62 + taker_skill * 0.24 - _gk_reflex(keeper) * 0.12 - fatigue_pen
    return random.random() < max(0.45, min(0.92, goal_prob))


def resolve_set_piece(state: GameState, piece_type: str) -> PlayResult:
    """Quick resolution for set pieces (corner, free kick, penalty, throw-in, goal kick)."""
    set_team = state.set_piece_team or state.home_team
    other_team = "away" if set_team == "home" else "home"
    attacking_right = state.team_attacks_right(set_team)
    goal_x = 105.0 if attacking_right else 0.0
    keeper = next((p for p in state.get_team_players(other_team)
                   if p.role == "GK"), None)

    if piece_type == "penalty":
        penalty_pos = HOME_PENALTY_SPOT if set_team == "home" else AWAY_PENALTY_SPOT
        taker = _pick_penalty_taker(state, set_team)
        if penalty_kick_scores(taker, keeper):
            return PlayResult(
                success=True, new_ball_holder=None,
                new_ball_position=Position(goal_x, CENTER_Y), event_type="goal",
                description=f"PENALTY GOAL! {taker.name if taker else 'The striker'} converts from the spot!",
                scoring_team=set_team, time_seconds=30, new_phase="kickoff",
            )
        return PlayResult(
            success=False, new_ball_holder=keeper.name if keeper else None,
            new_ball_position=penalty_pos, event_type="penalty_saved",
            description=f"{keeper.name if keeper else 'Keeper'} saves the penalty from {taker.name if taker else 'the striker'}!",
            time_seconds=30, new_phase="open_play",
        )

    elif piece_type == "corner":
        corner_taker = next((p for p in state.get_team_players(set_team)
                              if p.role in ("RW", "LW", "CM", "RB", "LB")), None)
        if not corner_taker:
            corner_taker = list(state.get_team_players(set_team))[0]
        target_pos = Position(goal_x - (5 if attacking_right else -5), CENTER_Y)
        # Aerial targets weighted by height/physical + finishing, then picked at
        # RANDOM (weighted) so the same player doesn't head in every single corner.
        targets = [p for p in state.get_team_players(set_team)
                   if p.role in ("CB", "CF", "ST", "CAM")] or [corner_taker]
        weights = [max(0.05, p.physical * 0.6 + p.shoot_skill * 0.4) for p in targets]
        scorer = random.choices(targets, weights=weights, k=1)[0]
        # Real-world corner conversion is ~2-3%. Scale modestly with aerial threat.
        goal_prob = max(0.01, 0.012 + scorer.physical * 0.020 + scorer.shoot_skill * 0.016
                        - _gk_reflex(keeper) * 0.022)
        if random.random() < goal_prob:
            verb = random.choice([
                f"{scorer.name} rises highest and heads it in",
                f"{scorer.name} powers a header home",
                f"{scorer.name} steals in at the near post to score",
                f"{scorer.name} bundles it home from the set piece",
                f"a flick on and {scorer.name} stabs it in",
            ])
            return PlayResult(
                success=True, new_ball_holder=None,
                new_ball_position=Position(goal_x, CENTER_Y), event_type="goal",
                description=f"GOAL from the corner! {verb}!",
                scoring_team=set_team, time_seconds=20, new_phase="kickoff",
            )
        def_player = next((p for p in state.get_team_players(other_team)
                           if p.role in ("CB", "GK")), None)
        return PlayResult(
            success=False, new_ball_holder=def_player.name if def_player else None,
            new_ball_position=target_pos, event_type="corner_cleared",
            description="Corner swung in — cleared by the defense.", time_seconds=20,
        )

    elif piece_type == "free_kick":
        # Dangerous central free kick within range → direct shot at goal.
        ball = state.ball_position
        dist = ((ball.x - goal_x) ** 2 + (ball.y - CENTER_Y) ** 2) ** 0.5
        central = abs(ball.y - CENTER_Y) < 18
        taker = _pick_penalty_taker(state, set_team)
        if dist < 30 and central and taker:
            on_target = random.random() < (0.45 + taker.shoot_skill * 0.25)
            if on_target:
                goal_prob = max(0.05, 0.10 + taker.shoot_skill * 0.16 - _gk_reflex(keeper) * 0.10)
                if random.random() < goal_prob:
                    return PlayResult(
                        success=True, new_ball_holder=None,
                        new_ball_position=Position(goal_x, CENTER_Y), event_type="goal",
                        description=f"FREE KICK GOAL! {taker.name} curls it home!",
                        scoring_team=set_team, time_seconds=25, new_phase="kickoff",
                    )
                if random.random() < 0.4:
                    _award_corner(state, other_team)
                    return PlayResult(
                        success=False, new_ball_holder=None,
                        new_ball_position=Position(goal_x, 0.0 if ball.y < CENTER_Y else PITCH_WIDTH),
                        event_type="shot_saved",
                        description=f"{taker.name}'s free kick is tipped over — corner!",
                        time_seconds=25, new_phase="corner", set_piece_team=set_team,
                    )
                return PlayResult(
                    success=False, new_ball_holder=keeper.name if keeper else None,
                    new_ball_position=keeper.position if keeper else Position(goal_x, CENTER_Y),
                    event_type="shot_saved",
                    description=f"{taker.name}'s free kick is saved!", time_seconds=25,
                )
            return PlayResult(
                success=False, new_ball_holder=keeper.name if keeper else None,
                new_ball_position=keeper.position if keeper else Position(goal_x, CENTER_Y),
                event_type="shot_missed",
                description=f"{taker.name} bends the free kick over the bar.",
                time_seconds=20, new_phase="goal_kick", set_piece_team=other_team,
            )
        # Otherwise played short to a sensible nearby teammate to build again.
        return _restart_to_teammate(state, set_team, "free_kick")

    elif piece_type in ("throw_in", "goal_kick"):
        return _restart_to_teammate(state, set_team, piece_type)

    # Fallback
    return _restart_to_teammate(state, set_team, "set_piece")


def _restart_to_teammate(state: GameState, set_team: str, piece_type: str) -> PlayResult:
    """Give the ball to the nearest sensible teammate to the restart spot."""
    ball = state.ball_position
    on_pitch = [p for p in state.get_team_players(set_team)]
    if piece_type == "goal_kick":
        # Keeper distributes; receiver is a defender/midfielder in space.
        options = [p for p in on_pitch if p.role in ("CB", "RB", "LB", "CDM", "CM")] or on_pitch
        receiver = min(options, key=lambda p: p.fatigue, default=None)
    else:
        # Nearest teammate to the restart location (excluding the keeper).
        outfield = [p for p in on_pitch if p.role != "GK"] or on_pitch
        receiver = min(outfield, key=lambda p: p.position.distance_to(ball), default=None)
    if receiver:
        return PlayResult(
            success=True, new_ball_holder=receiver.name,
            new_ball_position=receiver.position, event_type=piece_type,
            description=f"{piece_type.replace('_', ' ').title()} taken — {receiver.name} receives.",
            time_seconds=20,
        )
    players = on_pitch
    fallback = players[0] if players else None
    return PlayResult(
        success=True, new_ball_holder=fallback.name if fallback else None,
        new_ball_position=ball, event_type="set_piece",
        description="Set piece taken.", time_seconds=20,
    )
