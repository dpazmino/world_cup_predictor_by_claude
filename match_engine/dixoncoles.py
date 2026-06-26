"""
Dixon–Coles goal model — the standard statistical football predictor.

Fits per-team attack/defence strengths, a home-advantage term, and a low-score
dependence parameter (rho) by maximum likelihood on data/results.csv, with optional
exponential time-decay so recent matches count more (Dixon & Coles, 1997).

    log λ_home = μ + home·(not neutral) + attack[home] − defence[away]
    log λ_away = μ +                      attack[away] − defence[home]

and the joint score probability is the independent-Poisson product times the
Dixon–Coles τ correction for the 0-0/1-0/0-1/1-1 cells.

Provides win/draw/loss probabilities and a scoreline grid for any pairing — a fully
independent alternative to the Elo strength model, evaluated head-to-head in
backtest.py (`--dc`). Note: with only a few hundred international matches across ~44
teams this is data-sparse, so it is offered as a reference model, not the default.
"""
from __future__ import annotations
import math
import datetime
from functools import lru_cache

import numpy as np
from scipy.optimize import minimize

_MAXG = 8   # scoreline grid cap


def _tau(x, y, lam, mu, rho):
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


class DixonColesModel:
    def __init__(self, half_life_days: float | None = 540.0, reg: float = 3.0):
        self.half_life = half_life_days
        self.reg = reg          # L2 shrinkage on attack/defence (tames sparse data)
        self.teams: list[str] = []
        self.idx: dict[str, int] = {}
        self.attack = self.defence = None
        self.home = 0.0
        self.rho = 0.0
        self.mu = 0.0

    def fit(self, matches: list[dict], as_of: str | None = None) -> "DixonColesModel":
        teams = sorted({m["home"] for m in matches} | {m["away"] for m in matches})
        self.teams = teams
        self.idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)

        as_of = as_of or max(m["date"] for m in matches)
        ref = datetime.date.fromisoformat(as_of)

        def weight(m):
            if not self.half_life:
                return 1.0
            try:
                days = (ref - datetime.date.fromisoformat(m["date"])).days
            except ValueError:
                days = 0
            return 0.5 ** (max(0, days) / self.half_life)

        H = np.array([self.idx[m["home"]] for m in matches])
        A = np.array([self.idx[m["away"]] for m in matches])
        HG = np.array([m["hg"] for m in matches])
        AG = np.array([m["ag"] for m in matches])
        NEU = np.array([1.0 if m.get("neutral", True) else 0.0 for m in matches])
        W = np.array([weight(m) for m in matches])

        # params: attack[n], defence[n], home, rho, mu
        def unpack(p):
            return p[:n], p[n:2 * n], p[2 * n], p[2 * n + 1], p[2 * n + 2]

        def negll(p):
            atk, dfc, home, rho, mu = unpack(p)
            lam = np.exp(mu + home * (1 - NEU) + atk[H] - dfc[A])
            muu = np.exp(mu + atk[A] - dfc[H])
            # base independent-Poisson log-prob (drop constant factorials)
            ll = HG * np.log(lam) - lam + AG * np.log(muu) - muu
            # Dixon-Coles tau correction on low-score cells
            tau = np.ones(len(matches))
            m00 = (HG == 0) & (AG == 0); tau[m00] = 1 - lam[m00] * muu[m00] * rho
            m01 = (HG == 0) & (AG == 1); tau[m01] = 1 + lam[m01] * rho
            m10 = (HG == 1) & (AG == 0); tau[m10] = 1 + muu[m10] * rho
            m11 = (HG == 1) & (AG == 1); tau[m11] = 1 - rho
            tau = np.clip(tau, 1e-9, None)
            ll = ll + np.log(tau)
            # sum-to-zero (identifiability) + L2 shrinkage toward average (sparse data)
            pen = 1e3 * (atk.sum() ** 2 + dfc.sum() ** 2)
            pen += self.reg * (np.sum(atk ** 2) + np.sum(dfc ** 2))
            return -np.sum(W * ll) + pen

        p0 = np.concatenate([np.zeros(n), np.zeros(n), [0.25, 0.0, 0.2]])
        bounds = [(-3, 3)] * (2 * n) + [(-1, 1), (-0.2, 0.2), (-2, 2)]
        res = minimize(negll, p0, method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 500})
        atk, dfc, home, rho, mu = unpack(res.x)
        self.attack, self.defence = atk, dfc
        self.home, self.rho, self.mu = float(home), float(rho), float(mu)
        return self

    def _rates(self, home: str, away: str, neutral: bool) -> tuple:
        if home not in self.idx or away not in self.idx:
            # unseen team → league-average rates
            lam = mu = math.exp(self.mu)
            return lam, mu
        i, j = self.idx[home], self.idx[away]
        h = 0.0 if neutral else self.home
        lam = math.exp(self.mu + h + self.attack[i] - self.defence[j])
        mu = math.exp(self.mu + self.attack[j] - self.defence[i])
        return lam, mu

    def scoreline_grid(self, home: str, away: str, neutral: bool = True) -> dict:
        lam, mu = self._rates(home, away, neutral)
        grid = {}
        tot = 0.0
        for x in range(_MAXG + 1):
            for y in range(_MAXG + 1):
                p = (math.exp(-lam) * lam ** x / math.factorial(x)
                     * math.exp(-mu) * mu ** y / math.factorial(y)
                     * max(0.0, _tau(x, y, lam, mu, self.rho)))
                grid[(x, y)] = p
                tot += p
        return {k: v / tot for k, v in grid.items()} if tot else grid

    def outcome_probs(self, home: str, away: str, neutral: bool = True) -> tuple:
        grid = self.scoreline_grid(home, away, neutral)
        ph = sum(p for (x, y), p in grid.items() if x > y)
        pd = sum(p for (x, y), p in grid.items() if x == y)
        pa = sum(p for (x, y), p in grid.items() if x < y)
        s = ph + pd + pa or 1.0
        return (ph / s, pd / s, pa / s)


@lru_cache(maxsize=1)
def default_model() -> DixonColesModel:
    """Dixon–Coles fit on the full results history (cached)."""
    from .elo import load_matches
    return DixonColesModel().fit(load_matches())


if __name__ == "__main__":
    import sys
    m = default_model()
    print(f"Dixon–Coles fit: {len(m.teams)} teams, home={m.home:.3f}, rho={m.rho:.3f}, "
          f"mu={m.mu:.3f}")
    if len(sys.argv) >= 3:
        h, a = sys.argv[1], sys.argv[2]
        ph, pd, pa = m.outcome_probs(h, a, neutral=True)
        lam, mu = m._rates(h, a, True)
        print(f"  {h} {100*ph:.0f}% / draw {100*pd:.0f}% / {a} {100*pa:.0f}%  "
              f"(xg {lam:.2f}-{mu:.2f})")
    else:
        # show a few teams' attack/defence
        order = sorted(m.teams, key=lambda t: -(m.attack[m.idx[t]] - m.defence[m.idx[t]]))
        print(f"\n  {'team':<15}{'attack':>8}{'defence':>9}")
        for t in order[:12]:
            print(f"  {t:<15}{m.attack[m.idx[t]]:>8.2f}{m.defence[m.idx[t]]:>9.2f}")
