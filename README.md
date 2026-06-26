# World Cup 2026 — Predictor & Match Simulator

Two independent engines for the 2026 FIFA World Cup:

1. **A strength-based match predictor** (`predict.py`, `tournament.py`) — instant, analytic
   win/draw/loss and tournament odds. No simulation, no Monte-Carlo on a single match, no API
   key. This is the part that actually forecasts results, and it is validated out-of-sample.
2. **A multi-agent match simulator** (`run_match.py`) — LLM agents play out a match minute by
   minute to produce a believable *narrative* (commentary, events, box score). Great for stories;
   **not** on the prediction path.

> **Why they're separate:** walk-forward cross-validation showed the simulation adds ~zero
> predictive value over plain team strength (see `docs/GAP_ANALYSIS.md`). So prediction is a pure
> strength model, and the simulator is kept purely as a match-narrative engine.

---

## Quick start

```bash
pip install anthropic python-dotenv      # no requirements.txt; pure Python 3.9+
echo "ANTHROPIC_API_KEY=sk-..." > .env   # only needed for the LLM simulator
```

The **predictor** needs no API key:

```bash
C:\Users\david\anaconda3\python.exe predict.py                       # interactive — prompts for both teams (type ? to list)
C:\Users\david\anaconda3\python.exe predict.py Brazil France         # neutral venue
C:\Users\david\anaconda3\python.exe predict.py England Germany --home # give the first team home advantage
C:\Users\david\anaconda3\python.exe predict.py Brazil France --odds 3.2 3.4 2.2          # anchor to closing decimal odds
C:\Users\david\anaconda3\python.exe predict.py USA Paraguay --home --odds-american -150 280 650   # moneyline odds

C:\Users\david\anaconda3\python.exe tournament.py -n 10000           # full 2026 tournament odds (pre-tournament)
C:\Users\david\anaconda3\python.exe tournament.py --live             # lock in played results, simulate the rest
C:\Users\david\anaconda3\python.exe tournament.py --group D --fixtures   # one group's odds + per-fixture verdicts
```

The **simulator** needs `ANTHROPIC_API_KEY`:

```bash
C:\Users\david\anaconda3\python.exe run_match.py                      # default match (Argentina vs Brazil)
C:\Users\david\anaconda3\python.exe run_match.py England Germany      # custom teams
C:\Users\david\anaconda3\python.exe run_match.py England Germany --speed     # ~20x fewer LLM calls (rule-based off-ball)
C:\Users\david\anaconda3\python.exe run_match.py England Germany --knockout  # extra time + penalty shootout if level
C:\Users\david\anaconda3\python.exe run_match.py --schedule group_A   # a full group stage (6 matches + standings)

C:\Users\david\anaconda3\python.exe run_match.py England Germany --offline   # zero LLM calls, no API key (rules only)
```

---

## The predictor

`predict.py` is a pure strength predictor — instant, analytic, no simulation.

- **Win/draw/loss** comes from a **dynamic, regularised Elo** (`match_engine/elo.py`) that learns
  from `data/results.csv`, is seeded from squad strength, decays with idle time, and is calibrated
  by shrinking toward the base-rate prior (`--shrink`, default 0.25). With `--odds` it is anchored
  to the betting market (the strongest public predictor).
- **Expected goals & scorelines** come from a simple independent-Poisson model on the rating gap.
- It resolves name variants (`Korea Republic`→`South Korea`, `IR Iran`→`Iran`, …), prints a **95%
  rating-uncertainty interval** on each win probability, and **warns loudly** when a team has no
  rating data or a thin squad — so a coin-flip never masquerades as a real forecast.
- An optional **AI verifier** (Sonnet) audits each forecast for coherence and plausibility when a
  key is present (`--no-verify` to skip; degrades gracefully without a key).

`tournament.py` runs a Monte-Carlo of the full 48-team 2026 format (12 groups → 8 best thirds →
32-team knockout using the official bracket template), driven by `predict.py`'s probabilities, and
reports group-win/qualify/per-round/title odds **with 95% MC intervals**. Host nations get home
advantage in group games. `--live` makes it conditional on results logged so far.

### How good is it?

On a 269-fixture benchmark the production `pred` model (RPS **0.208**, shrink 0.25) beats the
base-rate prior (0.231), and its **cross-validated** RPS (0.209) ≈ in-sample (optimism ~0.001) — so
the edge is real, not overfit. Running scorecards live in `docs/MODEL_PERFORMANCE.md` and
`docs/RESULTS_TRACKER.md`.

```bash
C:\Users\david\anaconda3\python.exe backtest.py            # score prior/squad/elo/strength/pred (+market) — instant
C:\Users\david\anaconda3\python.exe backtest.py --sweep    # tune the shrink parameter
C:\Users\david\anaconda3\python.exe backtest.py --cv       # k-fold cross-validation (honest out-of-sample)
C:\Users\david\anaconda3\python.exe -m pytest tests/ -q    # 18-test suite (odds, Elo, metrics, predict, tournament)
```

---

## The simulator

A multi-agent simulator where **LLM agents decide *intent*** and a **deterministic engine decides
*outcomes***. One play cycle (`match_engine/match_simulation.py`):

1. **Ball-holder decision** — LLM chooses shoot / pass / dribble / cross
2. **Validation** — catches hallucinations (CB shooting from 80m, non-existent targets)
3. **Off-ball repositioning** — 20+ players repositioned in parallel (the dominant API cost)
4. **Resolution** (`resolution.py`) — pure-Python physics: pass/shot/save probabilities, no LLM
5. **Referee** — fouls, cards, set pieces
6. **Commentary** — 1–2 sentence broadcast narration
7. **State update** — ball, score, fatigue, event log

The outcome engine runs in **all** modes; the LLM only ever chooses intent.

### Hybrid modes (per-surface AI vs rules)

The simulator decomposes into five decision surfaces — `decisions`, `evaluator`, `repositioning`,
`commentary`, `coach` — each of which can independently use the LLM or a deterministic rule:

| invocation | decisions | evaluator | repositioning | commentary | coach | API key |
|---|---|---|---|---|---|---|
| (default) | AI | AI | AI | AI | AI | required |
| `--speed` | AI | AI | rules | rules | rules | required |
| `--hybrid` | AI | rules | rules | rules | rules | required |
| `--offline` | rules | rules | rules | rules | rules | **none** |
| `--ai=decisions,coach` | per the set you pass | | | | | if non-empty |

`--hybrid` is "AI mimics the human decision, rules do the rest" — ~1 LLM call/play instead of ~22.

### Calibration (realism, not prediction)

`calibrate.py` runs hundreds of **offline** matches (no API key, <1s each) and compares aggregate
output — goals, shots, possession, pass completion, corners, fouls, cards — to real World Cup
benchmarks. This is the source of truth for "realism" when tuning probabilities in `resolution.py`.

```bash
C:\Users\david\anaconda3\python.exe calibrate.py -n 200    # many no-LLM matches vs real benchmarks
C:\Users\david\anaconda3\python.exe test_mini.py           # 5-play smoke test
C:\Users\david\anaconda3\python.exe show_skills.py         # inspect player skill values
```

---

## Data

- `player_stats.csv` — FIFA-scale 0–99 skill ratings for the 48 WC squads (normalized to 0–1
  multipliers for the resolution engine).
- `data/results.csv` — ~254 real international results (2014–2025) with optional closing decimal
  odds in columns 7–9. Outcomes are reliable; exact scorelines are memory-recalled.
- `~59 <team>_prompts.py` files — hand-crafted behavioral system prompts teaching each player *how
  to think and play*. Both the CSV stats and the prompts are required for realistic behavior.

To scale the results set, `ingest_results.py` converts the public Kaggle "International football
results" database into the `results.csv` format.

---

## Project layout

| Path | Responsibility |
|---|---|
| `predict.py` | Strength-based match predictor (no simulation) |
| `tournament.py` | Monte-Carlo 2026 tournament forecast |
| `backtest.py` | Analytic model scoring (Brier / log-loss / RPS / ECE) |
| `run_match.py` | Simulator CLI entry point |
| `calibrate.py` | Offline box-score realism harness |
| `match_engine/elo.py` | Dynamic, regularised Elo (the production strength model) |
| `match_engine/ratings.py` | Static squad-strength ratings (seeds Elo) |
| `match_engine/odds.py` | Market-odds de-vig & anchoring utilities |
| `match_engine/dixoncoles.py` | Reference Dixon–Coles model |
| `match_engine/match_simulation.py` | 90-minute match orchestrator |
| `match_engine/resolution.py` | Deterministic pass/shot/dribble outcome engine |
| `match_engine/agents/` | LLM agents (player, evaluator, referee, commentator, coach) |
| `docs/` | Generated reports & forecasts (regenerate from the tools, don't hand-edit) |
| `CLAUDE.md` | Full architecture & command reference |

There are also two off-path experiments: an **academy player development** tool (`academy_gap.py`)
and a **betting / market-edge workflow** (`bet_tracker.py`, `compare_odds.py`, …). See `CLAUDE.md`
for the complete reference.

---

## Setup

- **Python** 3.9+, no build step. Examples use the full interpreter path
  (`C:\Users\david\anaconda3\python.exe`) since `python`/`py` aren't on PATH here; substitute
  plain `python` if yours is.
- **Dependencies:** `pip install anthropic python-dotenv` (no `requirements.txt`).
- **API key:** set `ANTHROPIC_API_KEY` in `.env` or the environment — required only for the LLM
  simulator and the optional AI verifier. The predictor, backtests, and offline simulation need
  no key.
