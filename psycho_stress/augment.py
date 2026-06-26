"""
Augmentation layer — applies psychophysical match stress to the EXISTING prediction.

Strict isolation: this calls `predict.predict()` and reads its output. It never
imports for mutation and never edits any production module. The stress adjustment is
applied entirely in probability (logit) space on top of the baseline W/D/L.

    load(side)   = Σ_components  perceived_stress[c] · sensitivity[c]   (− altitude adaptation)
    net_shift    = K · (load_away − load_home)        # +ve favours home
    p_home, p_away are nudged by ±net_shift/2 in logit space; the draw mass is held,
    then the triplet is renormalised.

Kept only if `evaluate.py` shows it beats the baseline out-of-sample.
"""
from __future__ import annotations
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
import predict as _predict                       # read-only

from .stress import perceived_stress, COMPONENTS
from .team_profile import build_profile
from .env_data import team_stimuli

STRESS_LOGIT_K = 0.55        # net-stress-load → logit shift (primary tunable)
ALT_ADAPT_FACTOR = 0.30      # residual altitude stress for an acclimatised team
ALT_ADAPT_MARGIN = 500.0     # metres: home altitude within this of venue → adapted

_profile_cache: dict = {}


def _profile(team: str) -> dict:
    if team not in _profile_cache:
        _profile_cache[team] = build_profile(team)
    return _profile_cache[team]


def team_load(feat: dict, side: str, cfg: dict | None = None) -> dict:
    """Total stress load for one side + the per-component breakdown."""
    team = feat[side]
    prof = _profile(team)
    perceived = perceived_stress(team_stimuli(feat, side), cfg)

    # altitude adaptation: a high-altitude home team barely feels a high venue
    if prof["home_altitude"] >= feat["altitude_m"] - ALT_ADAPT_MARGIN:
        perceived["altitude"] *= ALT_ADAPT_FACTOR

    contrib = {c: perceived[c] * prof["sens"][c] for c in COMPONENTS}
    return {"team": team, "load": sum(contrib.values()), "contrib": contrib,
            "perceived": perceived, "sens": prof["sens"]}


def _shift_probs(p: tuple, shift: float) -> tuple:
    """Nudge (home, draw, away) by ±shift/2 in logit space; renormalise."""
    h, d, a = p
    h *= math.exp(shift / 2.0)
    a *= math.exp(-shift / 2.0)
    s = h + d + a
    return (h / s, d / s, a / s)


def predict_augmented(feat: dict, neutral: bool = True, shrink: float = 0.25,
                      cfg: dict | None = None, k: float = STRESS_LOGIT_K) -> dict:
    """
    Stress-augmented W/D/L for a featured fixture (from env_data.compute_features).
    Returns baseline probs, augmented probs, the net shift, and per-team load detail.
    """
    home, away = feat["home"], feat["away"]
    base = _predict.predict(home, away, neutral=neutral, shrink=shrink)
    base_p = (base["p_home"], base["p_draw"], base["p_away"])

    lh = team_load(feat, "home", cfg)
    la = team_load(feat, "away", cfg)
    net_shift = k * (la["load"] - lh["load"])      # +ve → favours home
    aug_p = _shift_probs(base_p, net_shift)

    return {
        "home": home, "away": away,
        "p_base": base_p, "p_aug": aug_p, "net_shift": net_shift,
        "home_load": lh["load"], "away_load": la["load"],
        "home_contrib": lh["contrib"], "away_contrib": la["contrib"],
        "xg_home": base["xg_home"], "xg_away": base["xg_away"],
    }
