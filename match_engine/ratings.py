"""
Team strength ratings derived from the squad's player stats.

Used by the predictor and the strength-calibration step. A team's rating is the
mean of its best XI player overalls (0-99 FIFA scale), with attack/defence splits
for scoreline modelling. Falls back gracefully when players are missing from the CSV.
"""
from __future__ import annotations
import sys
import math
import importlib
from functools import lru_cache
from pathlib import Path

from .stats_loader import get_player_stats

_PROMPTS_DIR = Path(__file__).parent.parent

_ATTACK_ROLES = {"CF", "ST", "RW", "LW", "CAM", "RM", "LM"}
_DEFENCE_ROLES = {"GK", "CB", "RB", "LB", "RWB", "LWB", "CDM"}

# Map common database / FIFA name variants to this repo's squad names, so a query
# like "Korea Republic" resolves to "South Korea" instead of silently falling back
# to a default rating. Keys are lower-cased; unmapped names pass through unchanged.
_ALIASES = {
    "korea republic": "South Korea",
    "republic of korea": "South Korea",
    "south korea": "South Korea",
    "united states": "USA",
    "united states of america": "USA",
    "ir iran": "Iran",
    "iran (islamic republic of)": "Iran",
    "china pr": "China",
    "czech republic": "Czechia",
    "côte d'ivoire": "Ivory Coast",
    "cote d'ivoire": "Ivory Coast",
    "türkiye": "Turkey",
    "turkiye": "Turkey",
    # backfilled 2026 qualifiers + common variants
    "curacao": "Curacao",
    "curaçao": "Curacao",
    "bosnia": "Bosnia and Herzegovina",
    "bosnia & herzegovina": "Bosnia and Herzegovina",
    "bosnia-herzegovina": "Bosnia and Herzegovina",
    "congo dr": "DR Congo",
    "dr congo": "DR Congo",
    "democratic republic of the congo": "DR Congo",
    "dr. congo": "DR Congo",
    "cabo verde": "Cape Verde",
    "cape verde islands": "Cape Verde",
}


def canonical_name(name: str) -> str:
    """Resolve a team-name variant to this repo's canonical squad name."""
    if not name:
        return name
    return _ALIASES.get(name.strip().lower(), name.strip())


def _load_squad(team_name: str) -> list[str]:
    """Return the squad (player names) from <team>_prompts.py, or [] if missing."""
    team_name = canonical_name(team_name)
    module_name = team_name.lower().replace(" ", "_") + "_prompts"
    try:
        if str(_PROMPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_PROMPTS_DIR))
        mod = importlib.import_module(module_name)
        dict_name = team_name.upper().replace(" ", "_") + "_PROMPTS"
        return list(getattr(mod, dict_name, {}).keys())
    except (ModuleNotFoundError, AttributeError):
        return []


@lru_cache(maxsize=128)
def team_rating(team_name: str) -> dict:
    """
    Return {overall, attack, defence, n} on the 0-99 scale.

    overall = mean of the best 11 player overalls
    attack  = mean of the best 4 attacking-role players
    defence = mean of the best 5 defensive-role players (incl. GK)
    """
    squad = _load_squad(team_name)
    rated = []
    for name in squad:
        s = get_player_stats(name)
        if s:
            rated.append((s["raw_overall"], s.get("position", "CM").upper()))

    if not rated:
        return {"overall": 70.0, "attack": 70.0, "defence": 70.0, "n": 0}

    def _mean_top(values: list[float], k: int, default: float) -> float:
        vs = sorted(values, reverse=True)[:k]
        return sum(vs) / len(vs) if vs else default

    overalls = [o for o, _ in rated]
    overall = _mean_top(overalls, 11, 70.0)
    attack = _mean_top([o for o, pos in rated if pos in _ATTACK_ROLES], 4, overall)
    defence = _mean_top([o for o, pos in rated if pos in _DEFENCE_ROLES], 5, overall)

    return {"overall": round(overall, 1), "attack": round(attack, 1),
            "defence": round(defence, 1), "n": len(rated)}


def rating_gap(home: str, away: str) -> float:
    """Overall-rating advantage of `home` over `away` (can be negative)."""
    return team_rating(home)["overall"] - team_rating(away)["overall"]


# Home advantage expressed in rating points (≈ a couple of goals' worth of edge
# over a season maps to a few rating points). 0 for neutral venues.
HOME_ADV_POINTS = 4.0

# Symmetric base-rate prior for international football (home, draw, away).
BASE_PRIOR = (0.37, 0.26, 0.37)


def combine_probs(sim: tuple, ratings: tuple,
                  blend: float = 0.4, shrink: float = 0.0,
                  prior: tuple = BASE_PRIOR) -> tuple:
    """
    Combine simulation and ratings probabilities, then shrink toward the prior.

      blended = (1-blend)*sim + blend*ratings
      final   = (1-shrink)*blended + shrink*prior

    `blend` trades sim vs ratings; `shrink` is a confidence calibration that pulls
    over-confident predictions toward the base rate. Returns a normalised triple.
    """
    w = max(0.0, min(1.0, blend))
    lam = max(0.0, min(1.0, shrink))
    b = [(1 - w) * sim[i] + w * ratings[i] for i in range(3)]
    f = [(1 - lam) * b[i] + lam * prior[i] for i in range(3)]
    s = sum(f) or 1.0
    return tuple(x / s for x in f)


def outcome_probs(home: str, away: str, home_adv: float = 0.0) -> tuple:
    """
    A simple, well-behaved (home, draw, away) model from the rating gap alone.

    Used as a baseline and as the blend partner for the simulator. `home_adv` is
    added to the gap in rating points (pass HOME_ADV_POINTS for a true home game,
    0 for neutral). Tuned so even sides ≈ 30% draw and lopsided ties collapse the
    draw share.
    """
    gap = rating_gap(home, away) + home_adv
    p_home_vs_away = 1.0 / (1.0 + math.exp(-0.18 * gap))
    draw = 0.30 * math.exp(-abs(gap) / 18.0)
    home_p = (1 - draw) * p_home_vs_away
    away_p = (1 - draw) * (1 - p_home_vs_away)
    return (home_p, draw, away_p)


if __name__ == "__main__":
    # Quick sanity dump for a few well-known sides.
    for t in ("Brazil", "France", "Argentina", "England", "Germany",
              "Saudi Arabia", "China", "Iran", "Qatar"):
        r = team_rating(t)
        print(f"  {t:<14} overall {r['overall']:.1f}  atk {r['attack']:.1f}  "
              f"def {r['defence']:.1f}  (n={r['n']})")
