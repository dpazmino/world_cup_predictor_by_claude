"""Field geometry constants and helper functions."""
from __future__ import annotations
from typing import Optional
from .game_state import Position, PlayerState

# Pitch dimensions (metres)
PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0
CENTER_X = 52.5
CENTER_Y = 34.0

# Goal dimensions
GOAL_WIDTH = 7.32
GOAL_Y_MIN = CENTER_Y - GOAL_WIDTH / 2   # 30.34
GOAL_Y_MAX = CENTER_Y + GOAL_WIDTH / 2   # 37.66

# Penalty areas (x ranges for home team's end = right side)
PENALTY_AREA_DEPTH = 16.5
PENALTY_AREA_WIDTH_HALF = 20.15  # each side from centre

HOME_PENALTY_X = PITCH_LENGTH - PENALTY_AREA_DEPTH   # 88.5
AWAY_PENALTY_X = PENALTY_AREA_DEPTH                  # 16.5
PENALTY_Y_MIN = CENTER_Y - PENALTY_AREA_WIDTH_HALF   # 13.85
PENALTY_Y_MAX = CENTER_Y + PENALTY_AREA_WIDTH_HALF   # 54.15

# Six-yard boxes
SIX_YARD_DEPTH = 5.5
HOME_SIX_YARD_X = PITCH_LENGTH - SIX_YARD_DEPTH      # 99.5
AWAY_SIX_YARD_X = SIX_YARD_DEPTH                     # 5.5
SIX_YARD_Y_MIN = CENTER_Y - 9.16
SIX_YARD_Y_MAX = CENTER_Y + 9.16

# Penalty spot
HOME_PENALTY_SPOT = Position(PITCH_LENGTH - 11, CENTER_Y)
AWAY_PENALTY_SPOT = Position(11.0, CENTER_Y)

# Zone thresholds (home team perspective — flip for away)
DEFENSIVE_THIRD_X = PITCH_LENGTH / 3        # 35.0
ATTACKING_THIRD_X = PITCH_LENGTH * 2 / 3   # 70.0


def in_home_penalty_area(pos: Position) -> bool:
    return (pos.x >= HOME_PENALTY_X and
            PENALTY_Y_MIN <= pos.y <= PENALTY_Y_MAX)


def in_away_penalty_area(pos: Position) -> bool:
    return (pos.x <= AWAY_PENALTY_X and
            PENALTY_Y_MIN <= pos.y <= PENALTY_Y_MAX)


def in_penalty_area(pos: Position, defending_team: str) -> bool:
    """Is `pos` inside the penalty area that `defending_team` is defending?"""
    if defending_team == "home":
        return in_home_penalty_area(pos)
    return in_away_penalty_area(pos)


def is_out_of_bounds(pos: Position) -> Optional[str]:
    """Returns 'touchline', 'home_endline', 'away_endline', or None."""
    if pos.y < 0 or pos.y > PITCH_WIDTH:
        return "touchline"
    if pos.x > PITCH_LENGTH:
        return "home_endline"
    if pos.x < 0:
        return "away_endline"
    return None


def clamp_to_pitch(pos: Position) -> Position:
    return Position(
        x=max(0.0, min(PITCH_LENGTH, pos.x)),
        y=max(0.0, min(PITCH_WIDTH, pos.y)),
    )


def get_zone(pos: Position, team: str) -> str:
    """Return zone name from the given team's perspective."""
    if team == "home":
        x = pos.x
    else:
        x = PITCH_LENGTH - pos.x   # mirror for away

    if x < DEFENSIVE_THIRD_X:
        if in_penalty_area(pos, team):
            return "own_box"
        return "defensive_third"
    elif x < ATTACKING_THIRD_X:
        return "middle_third"
    else:
        defending = "away" if team == "home" else "home"
        if in_penalty_area(pos, defending):
            return "opponent_box"
        return "attacking_third"


def nearest_player_to(pos: Position,
                       players: list[PlayerState],
                       exclude: str = None) -> Optional[PlayerState]:
    best, best_dist = None, float("inf")
    for p in players:
        if p.name == exclude or not p.is_on_pitch:
            continue
        d = pos.distance_to(p.position)
        if d < best_dist:
            best_dist = d
            best = p
    return best


def is_offside(attacker: PlayerState,
               target_pos: Position,
               defenders: list[PlayerState],
               attacking_right: bool) -> bool:
    """
    Simple offside check: attacker is offside if they are beyond the
    second-to-last defender (only in attacking half).
    """
    if attacking_right:
        if target_pos.x <= CENTER_X:
            return False   # not in attacking half
        defender_xs = sorted([d.position.x for d in defenders if d.is_on_pitch], reverse=True)
    else:
        if target_pos.x >= CENTER_X:
            return False
        defender_xs = sorted([d.position.x for d in defenders if d.is_on_pitch])

    second_last_x = defender_xs[1] if len(defender_xs) > 1 else (0 if attacking_right else PITCH_LENGTH)

    if attacking_right:
        return target_pos.x > second_last_x and target_pos.x > attacker.position.x
    else:
        return target_pos.x < second_last_x and target_pos.x < attacker.position.x


def pass_lane_blocked(from_pos: Position,
                      to_pos: Position,
                      defenders: list[PlayerState],
                      intercept_radius: float = 2.5) -> Optional[PlayerState]:
    """
    Check if any defender is within `intercept_radius` metres of the
    straight line between `from_pos` and `to_pos`.
    Returns the best-positioned interceptor, or None.
    """
    dx = to_pos.x - from_pos.x
    dy = to_pos.y - from_pos.y
    length = math.sqrt(dx**2 + dy**2)
    if length < 0.1:
        return None

    best_defender = None
    best_dist = intercept_radius

    for d in defenders:
        if not d.is_on_pitch:
            continue
        # Project defender onto the pass line
        t = ((d.position.x - from_pos.x) * dx +
             (d.position.y - from_pos.y) * dy) / (length**2)
        if t < 0.05 or t > 0.95:   # outside the segment
            continue
        closest_x = from_pos.x + t * dx
        closest_y = from_pos.y + t * dy
        dist = math.sqrt((d.position.x - closest_x)**2 +
                         (d.position.y - closest_y)**2)
        if dist < best_dist:
            best_dist = dist
            best_defender = d

    return best_defender


import math
