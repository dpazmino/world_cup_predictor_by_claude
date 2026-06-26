"""
Per-team vulnerability profile — derived READ-ONLY from existing repo data.

The environment degrades teams unequally (pyscho.txt §8: "measure team sensitivity
to environment"). We build a sensitivity weight per stress component from data the
repo already has:

  * pressing intensity  — keyword-scored from team_tactics.TACTICAL_STYLES press_rule
  * physical / stamina  — squad means from player_stats.csv (heat & fatigue resilience)
  * squad depth         — quality drop-off starters→bench (absorbs rotation stress)
  * average age         — OPTIONAL, from psycho_stress/data/squad_ages.csv (web-sourced;
                          defaults to a neutral 27 until that file exists)
  * home altitude       — psycho_stress/data/team_home_altitude.csv (altitude adaptation)

Nothing here is written back; player_stats.csv / team_tactics.py are read only.
"""
from __future__ import annotations
import csv
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from match_engine.team_tactics import TACTICAL_STYLES  # read-only import

_STATS = os.path.join(_ROOT, "player_stats.csv")
_AGES = os.path.join(_HERE, "data", "squad_ages.csv")
_HOME_ALT = os.path.join(_HERE, "data", "team_home_altitude.csv")

_HIGH_PRESS = ("high press", "press immediately", "aggressive press", "press as a unit",
               "flood", "win the ball back", "win it back", "press from the front",
               "win the ball in the opponent")
_LOW_PRESS = ("mid-block", "mid block", "compact", "sit", "deep", "low block",
              "absorb", "drop", "stay organised", "pragmatic")


def _press_intensity(team: str) -> float:
    """0..1 pressing intensity from the prose press_rule (keyword scan, no LLM)."""
    style = TACTICAL_STYLES.get(team, {})
    text = (style.get("press_rule", "") + " " + style.get("philosophy", "")).lower()
    if not text.strip():
        return 0.55
    hi = sum(1 for kw in _HIGH_PRESS if kw in text)
    lo = sum(1 for kw in _LOW_PRESS if kw in text)
    score = 0.55 + 0.15 * hi - 0.12 * lo
    return max(0.2, min(1.0, score))


def _squad_stats(team: str) -> dict:
    overalls, physicals = [], []
    with open(_STATS, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["team"].strip() != team:
                continue
            try:
                overalls.append(float(row["overall"]))
                physicals.append(float(row["physical"]))
            except (ValueError, KeyError):
                pass
    if not overalls:
        return {"physical": 0.70, "depth": 0.5, "n": 0}
    overalls.sort(reverse=True)
    top = overalls[:11]
    bench = overalls[11:23]
    starters_mean = sum(top) / len(top)
    if bench:
        depth_ratio = (sum(bench) / len(bench)) / starters_mean      # ~0.85 typical
        depth = max(0.0, min(1.0, (depth_ratio - 0.78) / 0.15))
        depth *= min(1.0, len(overalls) / 20.0)                      # thin roster penalty
    else:
        depth = 0.1
    return {"physical": (sum(physicals) / len(physicals)) / 99.0, "depth": depth,
            "n": len(overalls)}


def _decomment(f):
    """Yield non-comment, non-blank lines so DictReader's header is the real header."""
    for line in f:
        if line.strip() and not line.lstrip().startswith("#"):
            yield line


def _load_lookup(path: str, key: str, val: str) -> dict:
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(_decomment(f)):
                try:
                    out[row[key].strip()] = float(row[val])
                except (ValueError, KeyError):
                    pass
    return out


def build_profile(team: str) -> dict:
    """Return per-component sensitivity weights (≈1.0 = average) + raw traits."""
    press = _press_intensity(team)
    s = _squad_stats(team)
    age = _load_lookup(_AGES, "team", "avg_age").get(team, 27.0)
    home_alt = _load_lookup(_HOME_ALT, "team", "altitude_m").get(team, 100.0)

    phys = s["physical"]          # 0..1
    depth = s["depth"]            # 0..1
    age_f = (age - 27.0) / 5.0    # >0 = older than baseline

    # Sensitivities centred on 1.0. High press + low physical + old → heat-vulnerable.
    sens = {
        "heat": 1.0 + 0.50 * (press - 0.55) + 0.40 * (0.72 - phys) + 0.20 * age_f,
        "altitude": 1.0 + 0.30 * (0.72 - phys),
        "travel": 1.0 + 0.35 * (0.5 - depth) + 0.25 * age_f,
        "circadian": 1.0 + 0.20 * age_f,
        "sensory": 1.0 + 0.20 * (0.5 - depth),
        "recovery": 1.0 + 0.45 * (0.5 - depth) + 0.35 * age_f + 0.25 * (press - 0.55),
        "weather": 1.0,
    }
    sens = {k: max(0.4, v) for k, v in sens.items()}
    return {"team": team, "press": press, "physical": phys, "depth": depth,
            "age": age, "home_altitude": home_alt, "n_rated": s["n"], "sens": sens}
