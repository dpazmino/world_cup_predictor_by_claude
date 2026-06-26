"""
Tests for the prediction layer (no API key, no simulation).

Run:  python -m pytest tests/ -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from match_engine import odds, elo, ratings
from match_engine.dixoncoles import default_model as dc_model
import backtest
import predict
import tournament


def _is_dist(p, tol=1e-6):
    return len(p) == 3 and all(0 <= x <= 1 for x in p) and abs(sum(p) - 1) < tol


# ── odds ──────────────────────────────────────────────────────────────────────
def test_implied_probs_normalised_and_devigged():
    p = odds.implied_probs(1.80, 3.60, 4.50)
    assert _is_dist(p)
    assert odds.overround(1.80, 3.60, 4.50) > 0          # book has a margin
    # favourite (lowest odds) has the highest probability
    assert p[0] == max(p)


def test_odds_power_method_normalised():
    assert _is_dist(odds.implied_probs(2.0, 3.3, 3.6, method="power"))


def test_anchor_blends_toward_market():
    model = (0.6, 0.3, 0.1)
    market = (0.2, 0.3, 0.5)
    out = odds.anchor(model, market, 0.5)
    assert _is_dist(out)
    assert out[2] > model[2]                              # pulled toward market's away prob


# ── elo ───────────────────────────────────────────────────────────────────────
def test_elo_win_probs_are_distribution():
    m = elo.default_model()
    assert _is_dist(m.win_probabilities("Brazil", "Qatar", neutral=True))


def test_elo_stronger_team_favoured():
    m = elo.default_model()
    ph, _, pa = m.win_probabilities("France", "Qatar", neutral=True)
    assert ph > pa                                        # France >> Qatar


def test_elo_home_advantage_helps():
    m = elo.default_model()
    neutral = m.win_probabilities("Mexico", "Croatia", neutral=True)[0]
    at_home = m.win_probabilities("Mexico", "Croatia", neutral=False)[0]
    assert at_home > neutral


def test_elo_recency_decay_regresses_to_prior():
    m = elo.default_model()
    t = "France"
    base = m.rating(t)
    prior = m.prior[t]
    # far in the future, the rating should sit closer to its prior than the raw rating
    decayed = m.effective_rating(t, as_of="2035-01-01")
    assert abs(decayed - prior) <= abs(base - prior) + 1e-9


# ── ratings / calibration helper ───────────────────────────────────────────────
def test_combine_probs_shrinks_toward_prior():
    strong = (0.8, 0.1, 0.1)
    out = ratings.combine_probs(strong, strong, blend=0.0, shrink=0.5)
    assert _is_dist(out)
    assert out[0] < strong[0]                             # shrunk toward the flatter prior


# ── scoring metrics ────────────────────────────────────────────────────────────
def test_brier_perfect_and_worst():
    assert backtest.brier((1, 0, 0), 0) == 0.0
    assert abs(backtest.brier((0, 0, 1), 0) - 2.0) < 1e-9


def test_rps_respects_ordering():
    # actual = home. Predicting a draw is "closer" than predicting away.
    rps_draw = backtest.rps((0, 1, 0), 0)
    rps_away = backtest.rps((0, 0, 1), 0)
    assert backtest.rps((1, 0, 0), 0) == 0.0
    assert rps_draw < rps_away


# ── predict ─────────────────────────────────────────────────────────────────────
def test_predict_returns_valid_distribution():
    out = predict.predict("Brazil", "France", neutral=True)
    assert _is_dist((out["p_home"], out["p_draw"], out["p_away"]))


def test_predict_favourite_higher_and_ci_brackets_point():
    out = predict.predict("France", "Qatar", neutral=True)
    assert out["p_home"] > out["p_away"]
    lo, hi = out["ci_home"]
    assert lo <= out["p_home"] <= hi                      # point estimate inside its CI


def test_predict_market_anchor_moves_toward_odds():
    base = predict.predict("Brazil", "France", neutral=True)["p_away"]
    anchored = predict.predict("Brazil", "France", neutral=True,
                               odds=(6.0, 4.0, 1.5), market_weight=0.8)["p_away"]
    assert anchored > base                # odds strongly favour France (away) at 1.5


# ── dixon–coles ─────────────────────────────────────────────────────────────────
def test_dixoncoles_outcome_is_distribution():
    assert _is_dist(dc_model().outcome_probs("Brazil", "France", neutral=True))


# ── prediction verifier (AI agent) ──────────────────────────────────────────────
def test_verifier_skips_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from match_engine.agents.prediction_verifier import verify
    out = predict.predict("Brazil", "France", neutral=True)
    v = verify(out)
    assert v["verdict"] == "skipped"          # graceful, no API call
    assert "issues" in v and "summary" in v


def test_verifier_facts_dont_crash():
    from match_engine.agents.prediction_verifier import _facts
    out = predict.predict("Mexico", "South Africa", neutral=True)
    facts = _facts(out)
    assert "Fixture:" in facts and "Expected goals:" in facts


# ── tournament conservation ─────────────────────────────────────────────────────
def test_tournament_conserves_counts():
    n = 40
    tally = tournament.run(n, seed=1)
    # exactly one champion and 32 qualifiers per simulation
    assert sum(tally[t]["win"] for t in tally) == n
    assert sum(tally[t]["qualify"] for t in tally) == 32 * n
    # round survivors: 16 reach R16, 2 reach the final
    assert sum(tally[t]["R16"] for t in tally) == 16 * n
    assert sum(tally[t]["final"] for t in tally) == 2 * n


def test_tournament_verifier_skips_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from match_engine.agents.prediction_verifier import verify_tournament, _tournament_facts
    tally = tournament.run(20, seed=2)
    assert "Sum of title odds" in _tournament_facts(tally, 20)   # facts build, don't crash
    assert verify_tournament(tally, 20)["verdict"] == "skipped"
