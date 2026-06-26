"""
Augmentation layer — applies the style-matchup advantage to the EXISTING prediction.

Strict isolation: wraps predict.predict() read-only and shifts (home, away) in logit space
by ±K·net/2, holding draw mass, then renormalises. Mirrors psycho_stress.augment.
"""
from __future__ import annotations
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
import predict as _predict                                 # read-only

from .matchup import net_advantage

MATCHUP_LOGIT_K = 0.8        # net-advantage → logit shift (the single tunable)


def _shift_probs(p, shift: float):
    h, d, a = p
    h *= math.exp(shift / 2.0)
    a *= math.exp(-shift / 2.0)
    s = h + d + a
    return (h / s, d / s, a / s)


def predict_augmented(home: str, away: str, neutral: bool = True, shrink: float = 0.25,
                      k: float = MATCHUP_LOGIT_K) -> dict:
    base = _predict.predict(home, away, neutral=neutral, shrink=shrink)
    base_p = (base["p_home"], base["p_draw"], base["p_away"])
    net = net_advantage(home, away)
    shift = 0.0 if net is None else k * net
    return {"home": home, "away": away, "p_base": base_p,
            "p_aug": _shift_probs(base_p, shift), "net": net, "shift": shift}
