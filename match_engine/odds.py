"""
Market-odds utilities.

Bookmaker closing odds are the single strongest public predictor of football
results, so the model can be *anchored* to them when they're available, and the
market is the right *benchmark* to beat in the backtest.

Decimal odds carry an overround (the book's margin), so raw 1/odds sum to > 1.
`implied_probs` removes it to recover a proper (home, draw, away) distribution.

    >>> implied_probs(1.80, 3.60, 4.50)        # de-vigged probabilities
    >>> overround(1.80, 3.60, 4.50)            # book margin (e.g. 0.06 = 6%)
"""
from __future__ import annotations


def overround(odds_home: float, odds_draw: float, odds_away: float) -> float:
    """Bookmaker margin: sum of raw implied probabilities minus 1."""
    return (1.0 / odds_home + 1.0 / odds_draw + 1.0 / odds_away) - 1.0


def implied_probs(odds_home: float, odds_draw: float, odds_away: float,
                  method: str = "proportional") -> tuple:
    """
    Convert decimal odds to de-vigged (P_home, P_draw, P_away).

    method:
      "proportional" — normalise 1/odds so they sum to 1 (standard, fast).
      "power"        — solve p_i = (1/odds_i)^k with sum 1 (reduces favourite-
                       longshot bias; usually a small correction).
    """
    inv = [1.0 / odds_home, 1.0 / odds_draw, 1.0 / odds_away]
    if any(x <= 0 for x in inv):
        raise ValueError("odds must be > 1.0")

    if method == "power":
        # Find k so that sum((1/odds)^k) == 1 by bisection.
        lo, hi = 0.5, 2.0
        for _ in range(60):
            k = (lo + hi) / 2
            s = sum(x ** k for x in inv)
            if s > 1.0:
                lo = k
            else:
                hi = k
        k = (lo + hi) / 2
        p = [x ** k for x in inv]
        s = sum(p)
        return tuple(v / s for v in p)

    s = sum(inv)
    return tuple(x / s for x in inv)


def anchor(model_probs: tuple, market_probs: tuple, weight: float) -> tuple:
    """
    Blend a model's (H,D,A) toward the market: (1-w)·model + w·market.
    `weight` is how much to trust the market (0 = ignore, 1 = pure market).
    """
    w = max(0.0, min(1.0, weight))
    out = tuple((1 - w) * model_probs[i] + w * market_probs[i] for i in range(3))
    s = sum(out) or 1.0
    return tuple(x / s for x in out)
