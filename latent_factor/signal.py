"""
Walk-forward, leak-free latent signals.

The latent model is learned FROM results, so — unlike the static-squad signals in
player_match / psycho_stress — it must be fit walk-forward: at each fixture the model has
seen only strictly-earlier games. We process fixtures in date order, predict the current
fixture from the model-so-far (warm-started), THEN fold the fixture in and take a few GD
steps. Cold (never-seen) teams yield a None signal (no shift), matching the baseline.

`walk_forward_interactions` returns a list aligned to the INPUT row order (the caller passes
backtest.build_rows(), which is not date-sorted), so evaluate.py can zip it against the rows.
The full predicted margin is returned too (for the optional standalone-MF diagnostic).

Computing the signal once here (per rank/reg) and sweeping the cheap logit-scale K afterward
keeps the expensive fitting out of the K-sweep / CV loops.
"""
from __future__ import annotations

from .factorize import LatentModel

# Defaults (validated on synthetic latent data — the estimator recovers known interaction
# structure with corr≈0.95 at these settings; see the package README / factorize.py). reg is
# tuned to the edge where latent factors survive on this sparse data: reg≈0.05 already crushes
# them to ~0, reg≈0.01 over-fits, so 0.02 gives the extra dimensions a fair chance. A long
# recency half-life mirrors elo.py's regression-to-prior idea.
DEFAULTS = dict(reg=0.02, lr=0.3, momentum=0.9, margin_clip=4.0,
                half_life_days=900.0, seed=0)

WARMUP_EPOCHS = 350          # initial (cold) fit once enough games exist
STEP_EPOCHS = 60             # warm-started passes after each new fixture
MIN_TRAIN = 25               # need this many prior games before emitting a signal


def walk_forward_interactions(rows, k: int = 2, min_train: int = MIN_TRAIN,
                              warmup_epochs: int = WARMUP_EPOCHS,
                              step_epochs: int = STEP_EPOCHS, **fit_kw):
    """Leak-free interaction (and full-margin) per row, in the input row order.

    Returns (interactions, margins): two lists where entry j corresponds to rows[j];
    a value is None when either team is unseen or fewer than `min_train` prior games exist.
    """
    kw = {**DEFAULTS, **fit_kw}
    order = sorted(range(len(rows)), key=lambda i: rows[i]["date"])
    model = LatentModel(k=k, **kw)
    interactions = [None] * len(rows)
    margins = [None] * len(rows)
    seen = []                                          # accumulated prior games (date order)
    warmed = False

    for pos, i in enumerate(order):
        r = rows[i]
        # Predict from the model fit on strictly-earlier games.
        if len(seen) >= min_train and model.knows(r["home"]) and model.knows(r["away"]):
            interactions[i] = model.interaction(r["home"], r["away"])
            margins[i] = model.margin(r["home"], r["away"], neutral=r["neutral"])
        # Fold this fixture in and update (warm) for the next one.
        seen.append(r)
        if not warmed and len(seen) >= min_train:
            model.partial_fit(seen, epochs=warmup_epochs, as_of=r["date"])
            warmed = True
        elif warmed:
            model.partial_fit(seen, epochs=step_epochs, as_of=r["date"])
    return interactions, margins
