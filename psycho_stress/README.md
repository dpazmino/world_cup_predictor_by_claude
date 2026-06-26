# psycho_stress — Psychophysical Match Stress Model

A **separate, isolated experiment** that tries to *augment* the existing strength
predictor with environmental + perceptual stress — **without changing any production
prediction code.** Everything here imports `predict.py` / `match_engine` / `backtest.py`
**read-only** and post-processes their output. (Verified: `pytest tests/` and
`backtest.py` are unchanged after adding this package.)

## The idea (from `downloads/pyscho.txt`)

Environment does not predict the winner directly — it predicts **how much each team's
normal performance degrades under stress**, and teams differ in vulnerability:

```
augmented_strength(team) = base_strength(team) − vulnerability·perceived_stress + adaptation
```

The "psychophysics" angle: a raw stimulus (heat, noise, altitude) is felt *non-linearly*
— Weber–Fechner (log, with a just-noticeable-difference threshold), Stevens' power law,
and Yerkes–Dodson (inverted-U arousal). Raw °C/dB/m → *perceived* load → performance hit.

## Pipeline

| module | role | data source |
|---|---|---|
| `stress.py` | psychophysics laws + per-component perceived stress | — (pure) |
| `team_profile.py` | per-team sensitivity per component | `player_stats.csv`, `team_tactics.py` (read-only) |
| `env_data.py` | per-fixture env → per-team stimuli; WBGT estimate | `data/venues_2026.csv`, `data/fixtures_env.csv` |
| `augment.py` | wrap `predict.predict()` → logit shift → augmented W/D/L | imports `predict` read-only |
| `evaluate.py` | walk-forward score vs baseline | reuses `backtest.py` scorers |

```
python -m psycho_stress.evaluate
```

## Status — backtested on the 59 played group games: NO out-of-sample edge

Built, isolated, and validated against real data. Env is now **real**: every fixture uses
its actual 2026 venue (`data/match_venues.csv`, ESPN schedule), with that city's June
climate, plus real altitude-change / travel (haversine) / timezone / rest computed from
the fixture sequence; squad ages and home altitudes are web-sourced.

Result on the 59 played games (`python -m psycho_stress.evaluate --sweep`):

| | RPS |
|---|--:|
| Baseline `pred` (K=0) | 0.1734 |
| In-sample best K=0.80 | 0.1713 *(looks better)* |
| **5-fold CV (honest, out-of-sample)** | **0.1753 — worse than baseline** |
| optimism (in-sample → CV) | +0.0039 |

The smooth in-sample K basin is encouraging (the altitude games at Mexican venues shift
correctly), but cross-validation shows the tuned K **does not generalise** — the whole
in-sample gain is optimism. Per the project ethos (kept only if it beats baseline
**out-of-sample**), **the augmentation is NOT shipped into the prediction path.**

Why so weak: the effect is a small second-order one, only a handful of the 59 games carry
strong stress (altitude at Azteca/Akron/BBVA, heat at Houston/Miami), and 59 games is far
too few to extract it from outcome noise. **Re-run as games accumulate** (knockouts, future
tournaments) — if an out-of-sample edge emerges, revisit. Nothing in production changed
(`pytest tests/` 18/18, `backtest.py` baseline unchanged).

## Data file schemas

- `data/venues_2026.csv` — `city,stadium,country,altitude_m,roof,utc_offset` (seeded with
  the 16 host stadiums; `roof` ∈ open|retractable|fixed — roofed venues are treated as
  climate-controlled, damping heat/weather).
- `data/fixtures_env.csv` — one row per match; see the header docstring in `env_data.py`
  for all columns (weather, per-team travel/timezone/rest/altitude, crowd). Blanks fall
  back to neutral defaults.
- `data/squad_ages.csv` — `team,avg_age` (optional; defaults to 27).
- `data/team_home_altitude.csv` — `team,altitude_m` (optional; drives altitude adaptation).
