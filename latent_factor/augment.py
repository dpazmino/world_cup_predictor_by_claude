"""
Augmentation layer — applies the latent interaction residual to the EXISTING prediction.

Strict isolation: wraps predict.predict() read-only and shifts (home, away) in logit space
by ±K·interaction/2, holding draw mass, then renormalises. Mirrors player_match.augment /
psycho_stress.augment.

For a FORWARD (live) prediction the fixture is not in the data, so there is no leak in
fitting the latent model on the whole result history as-of today; that is what
`predict_augmented` does. (Leak-free WALK-FORWARD fitting — needed only for back-scoring —
lives in signal.py and is exercised by evaluate.py.)
"""
from __future__ import annotations
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
import predict as _predict                                 # read-only
import backtest as _bt                                     # read-only (load_results)

from .factorize import LatentModel
from .signal import DEFAULTS, WARMUP_EPOCHS

LATENT_LOGIT_K = 0.0        # net-interaction → logit shift. 0.0 until proven (see README).
LATENT_RANK = 2             # k (latent dimensions) for the forward model


def _shift_probs(p, shift: float):
    h, d, a = p
    h *= math.exp(shift / 2.0)
    a *= math.exp(-shift / 2.0)
    s = h + d + a
    return (h / s, d / s, a / s)


def _full_history_model(k: int = LATENT_RANK) -> LatentModel:
    """Fit one latent model on the entire result history (for forward predictions)."""
    rows = _bt.load_results()
    rows.sort(key=lambda r: r["date"])
    model = LatentModel(k=k, **DEFAULTS)
    model.partial_fit(rows, epochs=WARMUP_EPOCHS)
    return model


def predict_augmented(home: str, away: str, neutral: bool = True, shrink: float = 0.25,
                      k: float = LATENT_LOGIT_K, rank: int = LATENT_RANK,
                      model: LatentModel | None = None) -> dict:
    base = _predict.predict(home, away, neutral=neutral, shrink=shrink)
    base_p = (base["p_home"], base["p_draw"], base["p_away"])
    model = model or _full_history_model(rank)
    inter = model.interaction(home, away)
    shift = 0.0 if inter is None else k * inter
    return {"home": home, "away": away, "p_base": base_p,
            "p_aug": _shift_probs(base_p, shift), "interaction": inter, "shift": shift}
