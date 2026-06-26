"""
Formation templates.

Each formation maps role labels to (x_pct, y_pct) where:
  x_pct is 0-1 relative to the team's OWN half (so GK=0.05, CF=0.90)
  y_pct is 0-1 across the pitch width (0.5 = centre)

Home team: multiply x_pct * 105, y_pct * 68
Away team: x = 105 - x_pct * 105, y = y_pct * 68
"""
from __future__ import annotations
from .game_state import Position, PlayerState

# Base positions as (x_fraction of pitch, y_fraction of pitch)
# x_fraction: 0=own goal, 1=opponent goal; y_fraction: 0=bottom, 1=top
FORMATIONS: dict[str, list[tuple[str, float, float]]] = {
    "4-3-3": [
        ("GK",  0.05, 0.50),
        ("RB",  0.28, 0.82), ("CB",  0.25, 0.62), ("CB",  0.25, 0.38), ("LB",  0.28, 0.18),
        ("CM",  0.50, 0.72), ("CDM", 0.45, 0.50), ("CM",  0.50, 0.28),
        ("RW",  0.75, 0.85), ("CF",  0.78, 0.50), ("LW",  0.75, 0.15),
    ],
    "4-4-2": [
        ("GK",  0.05, 0.50),
        ("RB",  0.28, 0.82), ("CB",  0.25, 0.62), ("CB",  0.25, 0.38), ("LB",  0.28, 0.18),
        ("RM",  0.52, 0.85), ("CM",  0.50, 0.65), ("CM",  0.50, 0.35), ("LM",  0.52, 0.15),
        ("CF",  0.78, 0.60), ("CF",  0.78, 0.40),
    ],
    "4-2-3-1": [
        ("GK",  0.05, 0.50),
        ("RB",  0.28, 0.82), ("CB",  0.25, 0.62), ("CB",  0.25, 0.38), ("LB",  0.28, 0.18),
        ("CDM", 0.42, 0.62), ("CDM", 0.42, 0.38),
        ("RW",  0.65, 0.85), ("CAM", 0.65, 0.50), ("LW",  0.65, 0.15),
        ("CF",  0.80, 0.50),
    ],
    "3-5-2": [
        ("GK",  0.05, 0.50),
        ("CB",  0.25, 0.72), ("CB",  0.25, 0.50), ("CB",  0.25, 0.28),
        ("RWB", 0.45, 0.90), ("CM",  0.48, 0.70), ("CDM", 0.43, 0.50), ("CM",  0.48, 0.30), ("LWB", 0.45, 0.10),
        ("CF",  0.78, 0.60), ("CF",  0.78, 0.40),
    ],
    "5-3-2": [
        ("GK",  0.05, 0.50),
        ("RWB", 0.30, 0.90), ("CB",  0.22, 0.72), ("CB",  0.22, 0.50), ("CB",  0.22, 0.28), ("LWB", 0.30, 0.10),
        ("CM",  0.50, 0.70), ("CDM", 0.45, 0.50), ("CM",  0.50, 0.30),
        ("CF",  0.76, 0.60), ("CF",  0.76, 0.40),
    ],
    "4-1-4-1": [
        ("GK",  0.05, 0.50),
        ("RB",  0.28, 0.85), ("CB",  0.25, 0.65), ("CB",  0.25, 0.35), ("LB",  0.28, 0.15),
        ("CDM", 0.40, 0.50),
        ("RM",  0.58, 0.88), ("CM",  0.55, 0.65), ("CM",  0.55, 0.35), ("LM",  0.58, 0.12),
        ("CF",  0.80, 0.50),
    ],
}

DEFAULT_FORMATION = "4-3-3"


def get_formation_positions(formation: str, team: str,
                             squad: list[str]) -> dict[str, PlayerState]:
    """
    Assign players to formation positions.
    First player is GK, then defenders, midfielders, forwards.
    `squad` must have exactly 11 players (the starting XI).
    Returns dict[player_name -> PlayerState].
    """
    template = FORMATIONS.get(formation, FORMATIONS[DEFAULT_FORMATION])
    states = {}
    for i, (role, xf, yf) in enumerate(template):
        name = squad[i] if i < len(squad) else f"Player_{i}"
        if team == "home":
            x = xf * 105.0
        else:
            x = (1.0 - xf) * 105.0  # mirror for away
        y = yf * 68.0
        states[name] = PlayerState(
            name=name,
            team=team,
            role=role,
            position=Position(x=x, y=y),
            shirt_number=i + 1,
        )
    return states


def formation_roles(formation: str) -> list[str]:
    """Return list of role labels for a formation (in positional order)."""
    return [r for r, _, _ in FORMATIONS.get(formation, FORMATIONS[DEFAULT_FORMATION])]


def press_adjustment(pressing: bool, team: str) -> float:
    """Return x-offset to apply to all outfield players when pressing high."""
    if not pressing:
        return 0.0
    offset = 10.0   # metres
    return offset if team == "home" else -offset
