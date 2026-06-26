"""
Loads player stats from player_stats.csv and normalizes them to 0-1 skill values
used by the resolution engine.

CSV columns expected:
  name, team, position, overall, pace, shooting, passing, dribbling, defending, physical

Stats are on a 0-99 FIFA-style scale. We normalize to 0.0-1.0 for the engine.
Falls back to None if a player is not in the CSV — player_skills.py handles fallback.
"""
from __future__ import annotations
import csv
from pathlib import Path
from functools import lru_cache

_CSV_PATH = Path(__file__).parent.parent / "player_stats.csv"

# Cache the full CSV in memory — loaded once at first access
@lru_cache(maxsize=1)
def _load_raw() -> dict[str, dict]:
    """Returns dict keyed by lowercase player name -> raw stat row."""
    rows = {}
    if not _CSV_PATH.exists():
        return rows
    with open(_CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = row["name"].strip().lower()
            rows[key] = row
    return rows


def _fifa_to_skill(value: str, default: float) -> float:
    """Convert FIFA 0-99 rating string to 0.0-1.0 skill float."""
    try:
        return round(min(1.0, max(0.0, float(value) / 99.0)), 3)
    except (ValueError, TypeError):
        return default


def get_player_stats(name: str) -> dict[str, float] | None:
    """
    Return a normalized skill dict for a player, or None if not in the CSV.

    Keys returned:
      pass_skill, intercept_skill, shoot_skill, dribble_skill,
      pace, overall          (extras available for prompt injection)
    """
    raw = _load_raw()
    row = raw.get(name.strip().lower())
    if not row:
        return None

    overall   = float(row.get("overall",   75)) / 99.0
    pace      = float(row.get("pace",      70)) / 99.0
    shooting  = float(row.get("shooting",  65)) / 99.0
    passing   = float(row.get("passing",   70)) / 99.0
    dribbling = float(row.get("dribbling", 68)) / 99.0
    defending = float(row.get("defending", 60)) / 99.0
    physical  = float(row.get("physical",  70)) / 99.0
    pos       = row.get("position", "CM").upper()

    # ── Map FIFA attributes → engine skill dimensions ─────────────────────
    # pass_skill: mostly passing, slight overall boost
    pass_skill = round(min(0.97, passing * 0.85 + overall * 0.15), 3)

    # intercept_skill: driven by defending + physical; high for CDM/CB, low for FWD
    pos_intercept_mult = {
        "GK": 0.25, "CB": 0.85, "RB": 0.78, "LB": 0.78,
        "RWB": 0.74, "LWB": 0.74,
        "CDM": 1.00, "CM": 0.75, "CAM": 0.55,
        "RM": 0.58, "LM": 0.58, "RW": 0.50, "LW": 0.50,
        "ST": 0.40, "CF": 0.40,
    }.get(pos, 0.65)
    intercept_skill = round(min(0.72, (defending * 0.65 + physical * 0.35) * pos_intercept_mult), 3)

    # shoot_skill: shooting rating, pace adds a touch (finishing on the run)
    shoot_skill = round(min(0.92, shooting * 0.88 + pace * 0.12), 3)

    # dribble_skill: dribbling + pace weighted
    dribble_skill = round(min(0.92, dribbling * 0.75 + pace * 0.25), 3)

    # stamina: FIFA CSVs rarely carry a stamina column, so derive from physical
    # (with a small overall lift) — a proxy for how well a player resists fatigue.
    stamina = round(min(0.99, physical * 0.85 + overall * 0.15), 3)

    return {
        "pass_skill":      pass_skill,
        "intercept_skill": intercept_skill,
        "shoot_skill":     shoot_skill,
        "dribble_skill":   dribble_skill,
        # athletic attributes — wired into resolution (foot races, aerials, fatigue)
        "pace":            round(pace, 3),
        "physical":        round(physical, 3),
        "stamina":         stamina,
        # extras useful for displaying in field scan
        "overall":         round(overall, 3),
        "position":        pos,
        "raw_overall":     int(float(row.get("overall", 75))),
    }


def all_loaded_players() -> list[str]:
    """Return all player names present in the CSV."""
    return list(_load_raw().keys())
