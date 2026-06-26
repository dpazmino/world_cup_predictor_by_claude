# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Project

```bash
# Default match (Argentina vs Brazil)
python run_match.py

# Custom teams
python run_match.py England Germany

# Fast mode (~20x fewer LLM calls, rule-based repositioning)
python run_match.py England Germany --speed

# Knockout mode (extra time + penalty shootout if level after 90')
python run_match.py England Germany --knockout

# Run a full group stage (6 matches + standings)
python run_match.py --schedule group_A

# 5-play smoke test
python test_mini.py

# Inspect player skill values
python show_skills.py

# Calibration harness — many no-LLM matches vs real benchmarks (no API key needed)
python calibrate.py -n 200
```

## AI / rules: per-surface control (hybrid modes)

The simulator decomposes into five **decision surfaces**, each of which can independently
use the LLM ("AI") or a deterministic rule fallback: `decisions` (on-ball action),
`evaluator`, `repositioning`, `commentary`, `coach`. `MatchSimulation.__init__` resolves
them from presets or an explicit `ai` set, exposing `self.ai_decisions`, `self.ai_evaluator`,
`self.ai_repositioning`, `self.ai_commentary`, `self.ai_coach`:

| invocation | decisions | evaluator | repositioning | commentary | coach | API key |
|---|---|---|---|---|---|---|
| (default) | AI | AI | AI | AI | AI | required |
| `--speed` / `fast_mode=True` | AI | AI | rules | rules | rules | required |
| `--hybrid` / `ai={"decisions"}` | AI | rules | rules | rules | rules | required |
| `--offline` / `offline=True` | rules | rules | rules | rules | rules | **none** |
| `--ai=decisions,coach` / `ai={...}` | per the set you pass | | | | | if set non-empty |

`--hybrid` is the "AI mimics the human decision, rules do the rest" mode: ~1 LLM call/play
instead of ~22. The rule fallback for on-ball decisions is `player_agent.decide_with_ball_rule`;
off-ball positioning falls back to `_rule_pos`. `_shot_pressure` and the LLM evaluator only
run when `ai_decisions`/`ai_evaluator` are set. Coaches pick formation/subs deterministically
unless `ai_coach`. The deterministic outcome engine (`resolution.py`) runs in **all** modes —
AI only ever chooses *intent*.

## Match prediction (strength-based, NO simulation)

**The match simulation is NOT on the prediction path.** Walk-forward cross-validation
(`backtest.py --cv`) showed the optimal simulation weight is ~0 — the sim adds no
predictive value over team strength (see docs/GAP_ANALYSIS.md). `predict.py` is therefore a
pure strength predictor: instant, analytic, no Monte-Carlo, no API key. The simulator
remains the match-*narrative* engine (`run_match.py`, `calibrate.py`).

```bash
python predict.py                            # interactive — prompts for both teams
python predict.py Brazil France              # neutral venue (default)
python predict.py England Germany --home     # give first team home advantage
python predict.py Brazil France --odds 3.2 3.4 2.2   # anchor to closing market odds
python predict.py USA Paraguay --home --odds-american -150 280 650  # DraftKings/moneyline odds
python tournament.py -n 10000                # full 2026 tournament odds (pre-tournament)
python tournament.py --live                  # in-tournament: lock in played results, sim the rest
python tournament.py --group D --fixtures    # one group's odds + per-fixture confidence verdicts
```

`predict.py` is interactive when teams are omitted (type `?` to list teams). It resolves
common name variants via `ratings.canonical_name` (`Korea Republic`→`South Korea`,
`United States`→`USA`, `IR Iran`→`Iran`, …) so they share one rating + history, and it
prints a loud **WARNING** when a team has no rating data (silent fallback to default) or a
note when a squad is thin (<11 rated players) — so an uninformed coin-flip never looks like
a real forecast.

`predict.py` win/draw/loss = dynamic-Elo strength (`elo.strength_probs`, recency-decayed,
regularised toward squad strength), calibrated by `--shrink` (default 0.25, tuned via
`backtest.py --sweep`) toward the base-rate prior, then optionally anchored to the market
with `--odds`. Expected goals and scorelines come from a simple independent-Poisson model
on the rating gap (still no simulation).

```bash
python backtest.py            # score prior/squad/elo/strength/pred (+market) — instant
python backtest.py --sweep    # tune the shrink parameter
python backtest.py --cv       # k-fold cross-validate the tuning (honest out-of-sample)
```

On the 269-fixture set the production `pred` model (RPS 0.208, Brier 0.618, shrink 0.25)
beats the base-rate prior (0.231/0.662), and — crucially — its **cross-validated** RPS
(0.209) ≈ its in-sample RPS (optimism ~0.001), so the edge is real. Re-run `--cv`/`--sweep`
whenever `data/results.csv` grows; there is no cache to rebuild.

Supporting pieces:
- `match_engine/elo.py` — **dynamic Elo** that learns from `data/results.csv`
  (goal-difference weighted, home-field advantage, seeded from squad strength, with
  **recency decay** — a team's learned edge regresses toward its squad prior with idle
  time, `HALF_LIFE_DAYS≈540`). `default_model()` builds the cached full-history model;
  `strength_probs(..., as_of=today)` is the production strength model — a **regularised
  Elo** that blends the squad prior with Elo weighted by games played, so thin-data teams
  lean on squad strength and Elo takes over as results accumulate. CLI:
  `python -m match_engine.elo` (ratings table) or `... Brazil France` (one matchup).
- `match_engine/ratings.py` — static team strength from the squad's best-XI CSV overalls
  (`team_rating`, `outcome_probs`). Seeds Elo and is the low-data fallback. Thinly-covered
  squads (few players in `player_stats.csv`) give noisy ratings — coverage bounds accuracy.
- `match_engine/odds.py` — market-odds utilities: `implied_probs` de-vigs decimal odds
  (proportional or power method) into a proper (H,D,A) distribution; `anchor` blends a
  model toward the market. The betting market is the strongest public predictor, so when
  odds are available they dominate. `predict.py --odds H D A [--market-weight w]` anchors
  a single prediction; the backtest benchmarks against the market for any fixture with odds.
- `data/results.csv` — ~254 real results (WC 2014/2018/2022, Euro 2016/2020/2024, Copa
  2019/2021/2024, AFCON, Asian Cup, qualifiers, Nations League), 2014–2025. Outcomes are
  reliable; exact scorelines are memory-recalled (verify for serious use). Optional trailing
  columns 7–9 = closing decimal odds (home,draw,away).
- `ingest_results.py` — converts the public Kaggle "International football results" database
  into `results.csv` format, filtered to teams with squads (maps name aliases, de-vigs the
  neutral flag, optional `--since` / `--no-friendlies`). This is the scalable path to
  thousands of matches: drop in the CSV, run `python ingest_results.py <file> --append`,
  then re-run `python backtest.py --cv` (analytic — no cache to rebuild).
- `backtest.py` — analytic (no simulation, no cache, instant). Scores **Brier**,
  **log-loss**, **RPS** (the 1X2 metric respecting home<draw<away order), **ECE**, and
  accuracy for: `prior`, `squad` (static logistic), `elo` (dynamic, **walk-forward**),
  `strength` (regularised Elo, walk-forward), and `pred` (the production model = strength
  shrunk toward the prior). `--sweep` tunes the shrink; `--cv` k-fold cross-validates it
  (reporting the optimism gap); `--reliability` prints ASCII calibration curves; `--dc`
  compares Dixon–Coles out-of-sample; `--cal` compares shrink vs temperature calibration.
- `analyze_draws.py` — walk-forward investigation (reuses `backtest`'s leak-free predict-then-
  `elo.update` loop) comparing two draw signals on the logged 2026 WC games: the headline
  `p_draw` vs the Poisson **`poisson_draw`** (diagonal scorelines from the xG means). Finding so
  far: both under-predict draws (~24% vs ~30% actual — a `DRAW_BASE` calibration gap) and
  **neither discriminates** which games draw (per-outcome gap ≈ 0). Poisson is marginally better
  calibrated. Re-run as results accumulate to see if any discrimination emerges. Prints to
  stdout; no checked-in report.
- `match_engine/ratings.py:combine_probs()` — shared blend+shrink helper (predict uses it
  with blend=0 to apply only the shrink).
- `match_engine/dixoncoles.py` — the standard **Dixon–Coles** model (MLE attack/defence +
  home + low-score `rho` + time-decay, L2-regularised). A reference alternative to Elo;
  evaluated via `backtest.py --dc`. On this sparse data (~5 games/team, no squad prior) it
  is no better than Elo, so Elo stays the default — denser league data would favour DC.
- `tournament.py` — Monte-Carlo World Cup forecast: simulates the 2026 format (12 groups →
  8 best thirds → 32-team knockout) N times, driving every match with `predict.py`'s
  probabilities (precomputed once — ratings are fixed during a forecast). Outputs group-win,
  qualify, per-round, and title odds **with 95% MC intervals**. **Host nations (USA/Mexico/
  Canada) get home advantage** in group games; outcomes use the validated strength W/D/L;
  group tiebreak margins from the conditional Poisson; knockout ties are coin-flip shootouts.
  The knockout uses the **official 2026 bracket template** (group-position R32 pairings + the
  real match tree); the 8 best thirds are matched to their FIFA-allocated slots (constrained
  assignment). **`--live`** turns it into an *in-tournament* forecast: it reads actual results
  from `data/results.csv` (games on/after 2026-06-11 between field teams, via
  `_load_played_results`), **locks them in** (played group games use the real score; played
  knockout games lock the real winner unless the score was a draw → pens, which can't be
  resolved from the score alone and falls back to a coin-flip), and **simulates only the
  unplayed games** — so odds become conditional on what's happened (a qualified team → ~100%,
  an eliminated one → 0%). Without `--live` it's the pre-tournament forecast (every game
  simulated). `--fixtures` (auto-on with `--group`) prints each group fixture's rule-based
  confidence verdict. Also runs the **AI verifier** (`verify_tournament`) on the
  whole odds table — coherence (title odds sum, monotonic rounds), strength plausibility vs
  Elo, host effect, thin-data flags, MC precision — unless `--no-verify`.
- `match_engine/agents/prediction_verifier.py` — **AI verification agent**. After a forecast
  is produced, `predict.py` runs an LLM (Sonnet) that audits it for problems — probability
  coherence, data reliability (games logged, thin/unknown squads, wide CIs), internal
  consistency (favourite-by-prob vs by-xG, scorelines vs xG), market disagreement, and
  football plausibility — returning a verdict (`sound`/`minor_issues`/`warning`) + issues.
  Runs automatically when `ANTHROPIC_API_KEY` is set (loaded from `.env`); `--no-verify`
  disables it; degrades gracefully to "skipped"/"unavailable" without a key or on API failure.
- `predict.py` also reports a **95% rating-uncertainty interval** on each win probability.
- `analyze_calibration.py` — walk-forward calibration/reliability of the live model on the
  logged 2026 WC games; writes `docs/MODEL_CALIBRATION.md` (Brier/log-loss/accuracy/ECE +
  ASCII reliability curve). Re-run as results accumulate.
- `tests/test_prediction.py` — pytest suite (18 tests) for odds, Elo, calibration, scoring
  metrics, predict, Dixon–Coles, and tournament conservation. Run: `python -m pytest tests/ -q`.

**Betting / market-edge workflow** (all read raw DraftKings text dropped into `downloads/`):
- `apply_odds.py` — merges DK closing American moneylines into `data/results.csv` columns 7–9
  (the de-vigged market benchmark the backtest scores against). One-off ingest helper.
- `compare_odds.py` — parses `downloads/odds.txt`, de-vigs, and diffs DK vs the model; writes
  `docs/MODEL_VS_DK_ODDS_<date>.md`.
- `bet_tracker.py` — prospective forward-test of the betting rules: locks each model pick + DK
  price **before kickoff** into `data/bets.csv` (the persisted ledger), grades against
  `data/results.csv`, and writes `docs/BET_TRACKER.md`. Three strategies: `all` / `rule`
  (DK price −127 or longer) / `model_ev` (model prob > break-even). See [[model-vs-draftkings-tracker]].
- `gen_remaining_preds.py` — predicts the remaining group fixtures listed in `downloads/preds.txt`;
  writes `docs/predictions_remaining_group_stage.md`.

**Academy player development** (a separate feature, off the WC-prediction path):
- `academy_gap.py` — ingests an academy player's 1–10 assessment, maps technical/tactical/physical
  sub-skills onto the FIFA-6 attributes in `player_stats.csv`, benchmarks against position peers,
  and emits a prioritised development plan (mental/nutrition treated as enablers, not skill gaps).
  Writes `docs/academy_gap_sample_cb.md`.

**Generated report files (markdown, checked in):** these are human-readable *outputs* of
the prediction tools, not inputs — regenerate them from the tools rather than hand-editing.
**All of these live in `docs/`** (filenames below are relative to it); the generator scripts
write there. Only `CLAUDE.md` stays in the repo root.
- `WORLD_CUP_2026_FORECAST.md` / `_v2.md` — pre-tournament `tournament.py` runs (100k MC of
  the 48-team draw); `_v2` is the current model (shrink 0.25, DRAW_BASE 0.45, official
  bracket) and supersedes the original.
- `WORLD_CUP_2026_FORECAST_live.md` — `tournament.py --live` output (played results locked
  in, only unplayed games simulated); `WORLD_CUP_2026_LIVE_BRACKET.md` is the matching
  group→knockout projection (Mermaid bracket). `tournament.py` only prints the odds table to
  stdout — these two markdown files are written by `gen_live_forecast.py` (pre+live 100k runs
  → the `pre → live` qualify table) and `gen_live_bracket.py` (the deterministic most-likely
  projection: group winner by P(1st), 2nd/3rd by P(qualify), 8 best thirds fit to FIFA's slot
  table via `tournament._assign_thirds`, favourite advances). Re-run both after logging new
  live results to refresh the files.
- `WORLD_CUP_2026_ELIMINATION.md` — per-team **stage-of-elimination distribution** (Group/R32/
  R16/QF/SF/Final/Champion exit probabilities, summing to ~100%), written by
  `gen_elimination_table.py` from the live run. Derived as consecutive differences of the
  round-reached odds; for reading bookmaker "stage of elimination" / "to reach round X" props
  against the model.
- `predictions_<date>.md` — per-matchday `predict.py` slates (neutral venue, DraftKings
  moneyline de-vigged and anchored 50/50).
- `RESULTS_TRACKER.md` — walk-forward predicted-vs-actual scorecard (W/D/L, pick, Brier) for
  logged 2026 results; the accuracy log behind [[wc2026-result-logging]].
- `MODEL_VS_DRAFTKINGS.md` — disagreement tracker isolating games where the model and DK pick
  different favourites (the only place a moneyline edge can exist), plus an elimination-market
  edge-test section; behind [[model-vs-draftkings-tracker]].
- `MODEL_VS_DK_ELIMINATION.md` — written by `compare_elim.py`, which reads DraftKings 7-way
  "stage of elimination" props from `downloads/elim.txt`, de-vigs them, and screens against the
  model's live elimination distribution. Key caveat surfaced by the tool: the model's exit
  distribution is **more diffuse** than the market, so most nominal +EV is miscalibration, not
  edge — only under-covered-but-strong sides (the Ivory Coast profile) are flagged as real.
- `MODEL_PERFORMANCE.md` — running, honest scorecard of the model's 2026 WC predictions (the
  headline evidence base); hand-curated narrative over the walk-forward results.
- `MODEL_CALIBRATION.md` — written by `analyze_calibration.py` (Brier/log-loss/accuracy/ECE +
  reliability curve).
- `BET_TRACKER.md` / `MODEL_VS_DK_ODDS_<date>.md` — written by `bet_tracker.py` / `compare_odds.py`
  (see the betting workflow above).
- `academy_gap_sample_cb.md` — written by `academy_gap.py` (academy-development feature, not WC prediction).

**Neutral venue:** `predict.py` defaults to a neutral venue (no home advantage). Elo
handles venue directly — `--home` adds `HFA` Elo points to the first team. World Cup
matches are effectively neutral.

**Why box-score realism ≠ prediction:** `calibrate.py` validates the *simulation's*
box-score realism (goals, shots, fouls…), which is necessary for believable narratives but
has no bearing on prediction quality — outcome-probability accuracy is measured only by
`backtest.py`. (This gap is exactly why the simulation was removed from the prediction path.)

## Calibration mode

`offline=True` (zero LLM calls, runs in <1s, no API key) powers `calibrate.py`, which runs
hundreds of matches and compares aggregate output (goals, shots, possession, pass completion,
corners, fouls, cards) to real World Cup benchmarks. When tuning any probability in
`resolution.py` or the rule policy, re-run `calibrate.py` to confirm the aggregates stay in
their realistic bands — that is the source of truth for "realism", not single-match intuition.

**Setup**: Set `ANTHROPIC_API_KEY` in `.env` or as an environment variable. No build step — pure Python 3.9+.

**Dependencies**: `anthropic` SDK (Claude API). No `requirements.txt` exists; install manually with `pip install anthropic python-dotenv`.

## Architecture

This is a multi-agent World Cup match simulator. LLM agents decide *what* to do; a deterministic resolution engine decides *what actually happens*.

### One Play Cycle (in `match_engine/match_simulation.py`)

1. **Ball-holder decision** (`player_agent.py:decide_with_ball`) — LLM chooses shoot/pass/dribble/cross
2. **Validation** (`evaluator_agent.py`) — catches hallucinations (CB shooting from 80m, non-existent pass targets)
3. **Off-ball repositioning** (`player_agent.py:reposition_all_parallel`) — 20+ players repositioned via `ThreadPoolExecutor`; fast-mode skips LLM and uses role-based rules
4. **Resolution** (`resolution.py:resolve_action`) — pure-Python physics: pass completion probability, shot on-target rate, keeper save rate; no LLM
5. **Referee** (`referee_agent.py`) — fouls, cards, set pieces
6. **Commentary** (`commentator_agent.py`) — 1–2 sentence broadcast narration
7. **State update** — ball position, score, fatigue, event log

### Key Design Patterns

- **Intent vs. outcome separation**: LLM agents express *intent*; `resolution.py` applies skill-weighted probability to decide actual outcome. Never mix these.
- **Two model tiers**: `claude-haiku-4-5` for high-volume calls (repositioning, commentary); `claude-sonnet-4-6` for complex decisions (player actions, evaluation). Defined in `agents/base_agent.py`.
- **Behavioral prompts + CSV stats**: The ~59 `<team>_prompts.py` files teach agents *how to think and play* (tactical identity, triggers). `player_stats.csv` provides 0–99 FIFA-scale skill ratings normalized to 0.0–1.0 multipliers. Both are required for realistic behavior.
- **Parallel repositioning**: All non-ball-holder players call the LLM concurrently each play. This is the dominant API cost driver — the main reason fast-mode exists.

### Module Responsibilities

| File | Responsibility |
|---|---|
| `run_match.py` | CLI entry point, `.env` loading |
| `match_engine/match_simulation.py` | 90-minute match orchestrator, substitution windows (55/65/75/85 min) |
| `match_engine/game_state.py` | `Position`, `PlayerState`, `GameState`, `MatchEvent` dataclasses |
| `match_engine/resolution.py` | Deterministic pass/shot/dribble outcome engine |
| `match_engine/field.py` | Pitch geometry (105×68m), offside, pass lane blocking, zone helpers |
| `match_engine/formations.py` | 6 formation templates mapping squad to (role, x%, y%) |
| `match_engine/team_tactics.py` | ~51 team tactical profiles (shoot/pass/press/width rules) injected into player prompts |
| `match_engine/stats_loader.py` | CSV loader; normalizes ratings to skill floats |
| `match_engine/player_skills.py` | Role-based skill baselines + keyword extraction from prompts → 0–1 modifiers for `resolution.py` (no LLM) |
| `match_engine/agents/base_agent.py` | `llm_call()` (JSON) and `llm_text()` with retry logic |
| `match_engine/agents/player_agent.py` | Per-player decision-making and parallel repositioning |
| `match_engine/agents/coach_agent.py` | Deterministic starting XI selection and formation assignment |
| `match_engine/agents/scheduler_agent.py` | 48-team 2026 bracket, group standings, knockout progression |
| `<team>_prompts.py` (~59 files) | Hand-crafted behavioral system prompts per player (`player_prompts.py` holds shared lookup + game-state context builder) |
| `fetch_player_stats.py` / `generate_missing_stats.py` | One-off utilities to populate/backfill rows in `player_stats.csv`. `fetch_player_stats.py` downloads the EA Sports FC 25 ratings dataset into `kaggle_data/` (`all_players.csv` / `male_players.csv` / `female_players.csv`) and maps players to the 48 WC squads (needs `pip install kaggle` + Kaggle API creds) |

### Adding a New Team

1. Create `<team>_prompts.py` following the `argentina_prompts.py` pattern — a dict of player name → system prompt string.
2. Add a tactical profile entry to `match_engine/team_tactics.py`.
3. Add player rows to `player_stats.csv` with FIFA-scale 0–99 ratings.
4. Add a coach entry to `match_engine/coach_prompts.py`.

### Adding a New Agent Action Type

1. Add the action keyword to `player_agent.py:decide_with_ball` prompt options.
2. Add handling in `evaluator_agent.py` validation logic.
3. Add resolution logic in `resolution.py:resolve_action`.
