# player_match — Style-Matchup Prediction Augmentation

A **separate, isolated experiment** (sibling to `psycho_stress`) that tests whether
unit-vs-unit **style mismatches** between two squads add predictive value **beyond the
scalar team-strength gap** the base model already uses. Built from the `downloads/p2.txt`
player-characteristics framework, restricted to the attributes we actually have
(`pace/shooting/passing/dribbling/defending/physical` + position). Imports
`predict.py` / `match_engine` / `backtest.py` **read-only**; applies a logit shift on the
baseline. (`pytest tests/` 18/18, `backtest.py` unchanged.)

## Pipeline

| module | role |
|---|---|
| `profiles.py` | per-team attack/midfield/defence unit profiles from `player_stats.csv` (top-N by overall) |
| `matchup.py` | net style advantage: my attack's pace/dribble/finish vs your defence, midfield passing, physicality — the cross-unit terms a single overall can't represent |
| `augment.py` | wrap `predict.predict()` read-only → logit shift |
| `evaluate.py` | walk-forward score vs baseline, K-sweep + cross-validation |

```
python -m player_match.evaluate          # wc subset + sweep + CV
python -m player_match.evaluate --all     # also recent + full history
```

## Status — backtested: NO out-of-sample edge on valid data

Because `player_stats.csv` is *current* squads, the **2026 WC games are the honest test**;
older games are anachronistic.

| subset | n | baseline RPS | CV (out-of-sample) RPS | verdict |
|---|--:|--:|--:|---|
| **WC 2026** (valid) | 59 | 0.1734 | **0.1754** | no edge (optimism +0.0046) |
| recent (2024+) | 116 | 0.1919 | 0.1932 | no edge |
| full history (anachronistic) | 322 | 0.2013 | 0.2007 | marginal +0.0006 — not trusted |

On the valid WC games a smooth in-sample K basin (RPS 0.1734→0.1708) **does not survive
cross-validation** — the gain is optimism, exactly like `psycho_stress`. The faint CV
"edge" on the full set is on the *least* valid (anachronistic) data and is negligible.

**Conclusion:** style-matchup features do **not** add out-of-sample predictive value beyond
scalar team strength on the games where the squad data is valid — consistent with
`docs/GAP_ANALYSIS.md` (the full simulation, which models player characteristics in depth,
also added ~0 over team strength). **Not shipped.** The scalar strength gap already absorbs
most of what unit matchups encode. Re-run as the valid sample grows. The richer p2.txt
traits (mental/stimulus/emotional) are a *simulation* concern, not a prediction one.
