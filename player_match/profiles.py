"""
Per-team unit profiles from player_stats.csv (READ-ONLY).

Groups a squad into attack / midfield / defence units by position, takes the likely
starters (top-N by overall in each unit), and averages their FIFA attributes. These
unit-level attributes are what let us express a *matchup* (my fast attack vs your slow
defence) that a single team-overall number structurally cannot.
"""
from __future__ import annotations
import csv
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_STATS = os.path.join(os.path.dirname(_HERE), "player_stats.csv")

UNIT_OF = {
    "CB": "DEF", "RB": "DEF", "LB": "DEF", "RWB": "DEF", "LWB": "DEF",
    "CDM": "MID", "CM": "MID", "CAM": "MID", "LM": "MID", "RM": "MID",
    "ST": "ATT", "RW": "ATT", "LW": "ATT", "CF": "ATT", "FW": "ATT",
}
UNIT_STARTERS = {"DEF": 4, "MID": 4, "ATT": 3}     # approx starting-XI shape (+GK)
ATTRS = ("pace", "shooting", "passing", "dribbling", "defending", "physical")

_cache: dict = {}


def _load() -> dict:
    if _cache:
        return _cache
    teams: dict = {}
    with open(_STATS, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            unit = UNIT_OF.get(row["position"].strip().upper())
            if unit is None:
                continue                                   # skip GK / unknown
            try:
                rec = {"overall": float(row["overall"]),
                       **{a: float(row[a]) / 99.0 for a in ATTRS}}
            except (ValueError, KeyError):
                continue
            teams.setdefault(row["team"].strip(), {}).setdefault(unit, []).append(rec)
    _cache.update(teams)
    return _cache


def profile(team: str) -> dict | None:
    """Unit-mean attribute profile for a team, or None if it has no rated outfielders."""
    units = _load().get(team)
    if not units:
        return None
    out = {}
    for unit, n in UNIT_STARTERS.items():
        players = sorted(units.get(unit, []), key=lambda r: -r["overall"])[:n]
        if not players:
            out[unit] = {a: 0.65 for a in ATTRS}           # neutral fallback
            continue
        out[unit] = {a: sum(p[a] for p in players) / len(players) for a in ATTRS}
    # squad-wide physical mean (duels / aerials / set pieces)
    allp = [p for u in units.values() for p in u]
    out["team_physical"] = sum(p["physical"] for p in allp) / len(allp)
    return out
