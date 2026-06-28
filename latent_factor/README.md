# latent_factor — Matrix-Factorization / Collaborative-Filtering Augmentation

A **separate, isolated experiment** (sibling to `player_match` / `psycho_stress`) that asks
the recommendation-engine question directly: does a **low-rank latent-factor model** of the
results matrix — teams' k-dimensional attack/defence "taste" vectors, à la collaborative
filtering — capture **non-transitive matchup structure** that the scalar team-strength gap
(Elo) misses? Imports `predict.py` / `match_engine` / `backtest.py` **read-only**; applies a
logit shift on the baseline `pred`. (`pytest tests/` 18/18, `backtest.py` unchanged.)

## Why this is the natural generalization of what we already run

| model | latent dims per team | status |
|---|---|---|
| Elo (production) | **rank-1** — one strength scalar | the baseline |
| Dixon–Coles (`dixoncoles.py`) | **2-factor** — latent attack + defence | benchmarked, **no better than Elo** on this data |
| **this experiment** | **k-factor** (k ∈ {0,1,2,4}) | isolates the bilinear *interaction residual* |

The signal is **only** the interaction cross term `⟨U_home, V_away⟩ − ⟨U_away, V_home⟩` —
the part a single overall (rank-1) cannot represent. At **k=0 it is identically 0**, so the
rank sweep is a clean test of "does adding latent dimensions buy anything?"

## Pipeline

| module | role |
|---|---|
| `factorize.py` | numpy bilinear margin factorizer: `margin = μ + b_h − b_a + ⟨U_h,V_a⟩ − ⟨U_a,V_h⟩`. Momentum GD, L2-reg, optional recency decay. `b` = rank-1 strength, `U/V` = latent style |
| `signal.py` | **leak-free walk-forward** fit: at each fixture the model has seen only strictly-earlier games (warm-started). Emits the interaction residual per fixture |
| `augment.py` | wrap `predict.predict()` read-only → logit shift by `K·interaction` |
| `evaluate.py` | rank sweep (the headline), logit-K sweep, cross-validation, optional standalone-MF diagnostic |

```
python -m latent_factor.evaluate              # rank sweep + K sweep + CV on 'full'
python -m latent_factor.evaluate --all        # also wc + recent subsets
python -m latent_factor.evaluate --standalone # also score MF as its own predictor
```

### Validity guard (important)

Because the signal is **learned from results**, an under-fit factorizer would produce a
fake null. `factorize.py` is therefore validated on **synthetic data with known latent
structure**: at the shipped settings it recovers the true interaction with **corr ≈ 0.95**
(`reg≈0.05` already crushes the factors to ~0; `reg≈0.01` over-fits — `0.02` is the sweet
spot that gives the extra dimensions a fair chance). So the null below reflects the *data*,
not the optimizer.

## Status — backtested: NO out-of-sample edge

The interaction term is **non-zero but anti-predictive noise** — up-weighting it monotonically
*worsens* RPS (e.g. full set 0.2043 → 0.2090 as K → 2.0), and cross-validation tunes the logit
weight straight back to **K=0** on every subset and every rank.

| subset | n | baseline RPS | CV (out-of-sample) RPS | best K | verdict |
|---|--:|--:|--:|--:|---|
| **WC 2026** | 53 | 0.1709 | 0.1709 | 0 | no edge |
| recent (2024+) | 108 | 0.1893 | 0.1893 | 0 | no edge |
| full history | 270 | 0.2043 | 0.2043 | 0 | no edge |

Rank sweep (k = 0,1,2,4): **no k>0 beats k=0** on any subset. The standalone-MF diagnostic
(scoring the latent margin as its own predictor under a fixed link) is **flat-to-worse across
ranks** — adding latent dimensions does not improve the fit either.

**Conclusion:** collaborative-filtering / latent-factor structure adds **no out-of-sample
predictive value beyond scalar team strength** on this sparse international data — the **same
null** as the full simulation, `player_match`, `psycho_stress`, `coach_clash`, and (directly
on point) Dixon–Coles ≈ Elo. With ~5–10 games per team there simply aren't enough observations
to estimate a reliable k-dim vector per team; the strength scalar already absorbs the signal.
**Not shipped** (`augment.LATENT_LOGIT_K = 0.0`).

### When this could pay (the real reason it exists)

Latent-factor models earn their keep on **dense** data (thousands of *club* games, many
observations per team). The intended payoff path is **not** as a standalone international
predictor but as a **better Elo seed**: ingest dense club results (`ingest_results.py`), learn
per-team/per-player latent vectors there, and feed them in as the strength prior — folding this
into the `PREDICTION_IMPROVEMENT_GAP.md` lever #3 ("better player-rating inputs") rather than
fighting it as a sparse-data predictor. Re-run `evaluate.py` as the valid sample grows.

*Minor curiosity worth a fair follow-up: under the rough fixed link, even the k=0 (pure
results-fit strength) MF scores competitively on the full set — a directly-margin-fit strength
model as an alternative baseline. Not a claim until scored with a fitted link; orthogonal to
the k>0 question this experiment answers.*
