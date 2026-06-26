"""
Extract individual player skill profiles from their behavioral prompts.

Skill values are 0.0-1.0 modifiers used by the resolution engine.
No LLM calls — purely keyword-driven from the existing prompt text.
"""
from __future__ import annotations

# Role-based baseline skill profiles
_ROLE_BASES: dict[str, dict[str, float]] = {
    "GK":  {"pass": 0.72, "intercept": 0.10, "shoot": 0.15, "dribble": 0.35},
    "CB":  {"pass": 0.80, "intercept": 0.42, "shoot": 0.32, "dribble": 0.48},
    "RB":  {"pass": 0.82, "intercept": 0.36, "shoot": 0.36, "dribble": 0.60},
    "LB":  {"pass": 0.82, "intercept": 0.36, "shoot": 0.36, "dribble": 0.60},
    "RWB": {"pass": 0.81, "intercept": 0.34, "shoot": 0.38, "dribble": 0.62},
    "LWB": {"pass": 0.81, "intercept": 0.34, "shoot": 0.38, "dribble": 0.62},
    "CDM": {"pass": 0.86, "intercept": 0.50, "shoot": 0.42, "dribble": 0.58},
    "CM":  {"pass": 0.87, "intercept": 0.38, "shoot": 0.52, "dribble": 0.64},
    "CAM": {"pass": 0.88, "intercept": 0.26, "shoot": 0.62, "dribble": 0.72},
    "RM":  {"pass": 0.83, "intercept": 0.28, "shoot": 0.56, "dribble": 0.70},
    "LM":  {"pass": 0.83, "intercept": 0.28, "shoot": 0.56, "dribble": 0.70},
    "RW":  {"pass": 0.82, "intercept": 0.22, "shoot": 0.62, "dribble": 0.74},
    "LW":  {"pass": 0.82, "intercept": 0.22, "shoot": 0.62, "dribble": 0.74},
    "CF":  {"pass": 0.78, "intercept": 0.18, "shoot": 0.72, "dribble": 0.68},
}

_DEFAULT_BASE = {"pass": 0.82, "intercept": 0.30, "shoot": 0.50, "dribble": 0.62}

# Keywords that boost overall quality
_ELITE_PHRASES = [
    "world-class", "best in the world", "generational", "ballon d'or",
    "elite", "extraordinary", "legendary", "greatest", "peerless",
]
_GOOD_PHRASES = [
    "exceptional", "outstanding", "clinical", "precise", "technical",
    "intelligent", "creative", "lethal", "prolific", "accomplished",
    "composed", "commanding",
]
_WEAK_PHRASES = [
    "inconsistent", "raw talent", "still developing", "erratic",
    "struggles under pressure", "tendency to lose",
]

# Interception-specific keywords
_INTERCEPT_PHRASES = [
    "reading the game", "anticipat", "intercept", "press relentlessly",
    "aggressive press", "hunting the ball", "snapping into tackles",
    "tenacious", "combative", "breaking up play", "win the ball back",
    "positional awareness", "reads passing lanes",
]

# Passing-specific keywords
_PASS_PHRASES = [
    "pinpoint delivery", "vision", "through ball", "precise passing",
    "distribution", "range of passing", "pick a pass", "inch-perfect",
    "dictates tempo", "orchestrates", "creative passing",
]

# Shooting-specific keywords
_SHOOT_PHRASES = [
    "lethal finisher", "clinical in front of goal", "deadly",
    "powerful shot", "long-range", "strikes", "composure in front of goal",
    "goalscoring instinct", "natural finisher", "eye for goal",
]

# Dribbling-specific keywords
_DRIBBLE_PHRASES = [
    "silky dribbler", "beats defenders", "explosive pace",
    "close control", "electrifying", "accelerate past", "burst of speed",
    "change of direction", "elusive", "nimble", "quicksilver",
]


# Role-based athletic baselines (pace, physical, stamina) for CSV-miss fallback.
_ROLE_ATHLETIC: dict[str, dict[str, float]] = {
    "GK":  {"pace": 0.55, "physical": 0.78, "stamina": 0.68},
    "CB":  {"pace": 0.62, "physical": 0.84, "stamina": 0.74},
    "RB":  {"pace": 0.80, "physical": 0.70, "stamina": 0.85},
    "LB":  {"pace": 0.80, "physical": 0.70, "stamina": 0.85},
    "RWB": {"pace": 0.83, "physical": 0.70, "stamina": 0.87},
    "LWB": {"pace": 0.83, "physical": 0.70, "stamina": 0.87},
    "CDM": {"pace": 0.66, "physical": 0.80, "stamina": 0.83},
    "CM":  {"pace": 0.70, "physical": 0.72, "stamina": 0.86},
    "CAM": {"pace": 0.74, "physical": 0.62, "stamina": 0.78},
    "RM":  {"pace": 0.80, "physical": 0.64, "stamina": 0.84},
    "LM":  {"pace": 0.80, "physical": 0.64, "stamina": 0.84},
    "RW":  {"pace": 0.86, "physical": 0.60, "stamina": 0.80},
    "LW":  {"pace": 0.86, "physical": 0.60, "stamina": 0.80},
    "CF":  {"pace": 0.78, "physical": 0.78, "stamina": 0.76},
    "ST":  {"pace": 0.80, "physical": 0.78, "stamina": 0.76},
}

_DEFAULT_ATHLETIC = {"pace": 0.72, "physical": 0.70, "stamina": 0.80}


def extract_athleticism(role: str, name: str = "") -> dict[str, float]:
    """
    Return pace/physical/stamina (0.0-1.0) for a player.
    Priority: CSV stats database → role-based baseline.
    """
    if name:
        from .stats_loader import get_player_stats
        csv_stats = get_player_stats(name)
        if csv_stats:
            return {
                "pace":     csv_stats["pace"],
                "physical": csv_stats["physical"],
                "stamina":  csv_stats["stamina"],
            }
    return _ROLE_ATHLETIC.get(role, _DEFAULT_ATHLETIC).copy()


def extract_skills(prompt: str, role: str, name: str = "") -> dict[str, float]:
    """
    Return a skill dict for a player.
    Priority: CSV stats database → keyword extraction from prompt.
    Keys: "pass", "intercept", "shoot", "dribble" — all 0.0 to 1.0.
    """
    # ── 1. Try real stats from CSV first ──────────────────────────────────
    if name:
        from .stats_loader import get_player_stats
        csv_stats = get_player_stats(name)
        if csv_stats:
            return {
                "pass":      csv_stats["pass_skill"],
                "intercept": csv_stats["intercept_skill"],
                "shoot":     csv_stats["shoot_skill"],
                "dribble":   csv_stats["dribble_skill"],
            }

    # ── 2. Fallback: keyword extraction from prompt ────────────────────────
    base = _ROLE_BASES.get(role, _DEFAULT_BASE).copy()
    text = prompt.lower()

    # ── Overall quality bonus ──────────────────────────────────────────────
    quality = 0.0
    quality += sum(0.04 for p in _ELITE_PHRASES if p in text)
    quality += sum(0.02 for p in _GOOD_PHRASES if p in text)
    quality -= sum(0.03 for p in _WEAK_PHRASES if p in text)
    quality = max(-0.08, min(0.12, quality))   # cap at ±12%

    # ── Skill-specific bonuses ─────────────────────────────────────────────
    intercept_bonus = sum(0.04 for p in _INTERCEPT_PHRASES if p in text)
    intercept_bonus = min(0.18, intercept_bonus)

    pass_bonus = sum(0.03 for p in _PASS_PHRASES if p in text)
    pass_bonus = min(0.12, pass_bonus)

    shoot_bonus = sum(0.03 for p in _SHOOT_PHRASES if p in text)
    shoot_bonus = min(0.15, shoot_bonus)

    dribble_bonus = sum(0.03 for p in _DRIBBLE_PHRASES if p in text)
    dribble_bonus = min(0.15, dribble_bonus)

    return {
        "pass":      round(min(0.97, base["pass"]      + quality + pass_bonus),    3),
        "intercept": round(min(0.72, base["intercept"] + intercept_bonus),          3),
        "shoot":     round(min(0.92, base["shoot"]     + quality + shoot_bonus),    3),
        "dribble":   round(min(0.92, base["dribble"]   + quality + dribble_bonus),  3),
    }
