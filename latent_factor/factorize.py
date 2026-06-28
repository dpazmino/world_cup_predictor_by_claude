"""
Low-rank latent-factor (matrix-factorization) model of the results matrix.

Bilinear goal-margin model:

    margin(home, away) = mu + (h0 if not neutral) + b[home] - b[away]
                         + <U[home], V[away]> - <U[away], V[home]>

  * b[t]            scalar latent STRENGTH (the rank-1 part — this alone ≈ Elo)
  * U[t], V[t]      k-dim latent ATTACK / DEFENCE vectors (the extra dimensions)
  * <U[h],V[a]>     how home's attack style exploits away's defence style — the
                    NON-TRANSITIVE matchup term a single overall can't encode

The experiment's signal is the INTERACTION RESIDUAL only:

    interaction(home, away) = <U[home], V[away]> - <U[away], V[home]>

so at k=0 it is identically 0 (degenerates to "use scalar strength, shift nothing").

Fit by warm-startable full-batch gradient descent on clipped goal margins, L2-regularised,
with optional recency (time-decay) weighting. numpy-vectorised. Deterministic given seed.

This is a self-contained estimator; signal.py drives it walk-forward (leak-free) and
augment.py uses it for a forward (live) prediction. Nothing here mutates production code.
"""
from __future__ import annotations
from datetime import date as _date
import numpy as np


def _ordinal(d: str) -> int:
    y, m, day = (int(x) for x in d.split("-"))
    return _date(y, m, day).toordinal()


class LatentModel:
    """Warm-startable bilinear margin factorizer. Teams are added lazily as seen."""

    def __init__(self, k: int = 2, reg: float = 0.02, lr: float = 0.3,
                 momentum: float = 0.9, margin_clip: float = 4.0,
                 half_life_days: float | None = None,
                 init_scale: float = 0.05, seed: int = 0):
        self.k = int(k)
        self.reg = float(reg)
        self.lr = float(lr)
        self.momentum = float(momentum)
        self.margin_clip = float(margin_clip)
        self.half_life_days = half_life_days
        self.init_scale = float(init_scale)
        self.rng = np.random.default_rng(seed)
        self.index: dict[str, int] = {}
        self.mu = 0.0
        self.h0 = 0.3                                   # home-field margin (non-neutral)
        self.b = np.zeros(0, dtype=float)
        self.U = np.zeros((0, self.k), dtype=float)
        self.V = np.zeros((0, self.k), dtype=float)

    # ── team bookkeeping ──────────────────────────────────────────────────────
    def _ensure(self, name: str) -> int:
        i = self.index.get(name)
        if i is not None:
            return i
        i = len(self.index)
        self.index[name] = i
        self.b = np.append(self.b, 0.0)
        if self.k:
            self.U = np.vstack([self.U, self.rng.normal(0, self.init_scale, self.k)])
            self.V = np.vstack([self.V, self.rng.normal(0, self.init_scale, self.k)])
        else:
            self.U = np.zeros((len(self.index), 0))
            self.V = np.zeros((len(self.index), 0))
        return i

    def knows(self, name: str) -> bool:
        return name in self.index

    # ── fitting ───────────────────────────────────────────────────────────────
    def partial_fit(self, games, epochs: int = 40, as_of: str | None = None) -> "LatentModel":
        """Run `epochs` warm-started full-batch GD passes over `games`.

        games: iterable of dicts with home, away, hg, ag, neutral, date.
        Teams are registered first, so callers may compute predictions for any team in
        `games` afterwards. `as_of` (a date) anchors the recency decay; defaults to the
        latest game date.
        """
        games = list(games)
        if not games:
            return self
        hi, ai, y, neu, ages = [], [], [], [], []
        ref = _ordinal(as_of) if as_of else max(_ordinal(g["date"]) for g in games)
        c = self.margin_clip
        for g in games:
            hi.append(self._ensure(g["home"]))
            ai.append(self._ensure(g["away"]))
            y.append(max(-c, min(c, g["hg"] - g["ag"])))
            neu.append(not g["neutral"])               # True where home-field applies
            ages.append(ref - _ordinal(g["date"]))
        hi = np.asarray(hi); ai = np.asarray(ai)
        y = np.asarray(y, dtype=float)
        notneu = np.asarray(neu, dtype=float)
        if self.half_life_days:
            w = 0.5 ** (np.asarray(ages, dtype=float) / self.half_life_days)
        else:
            w = np.ones(len(games))
        n = len(games)
        # Momentum (Polyak) buffers — local to this warm-started call. Plain GD converges
        # painfully slowly here (mean-squared loss → O(1/n) gradients); momentum lets the
        # latent factors actually grow, so a true null reflects the data, not under-fitting.
        m = self.momentum
        vmu = vh0 = 0.0
        vb = np.zeros_like(self.b)
        vU = np.zeros_like(self.U); vV = np.zeros_like(self.V)

        for _ in range(epochs):
            pm = self.mu + self.h0 * notneu + self.b[hi] - self.b[ai]
            if self.k:
                pm = pm + np.einsum("gk,gk->g", self.U[hi], self.V[ai]) \
                        - np.einsum("gk,gk->g", self.U[ai], self.V[hi])
            e = 2.0 * w * (pm - y) / n                  # weighted residual gradient

            vmu = m * vmu + e.sum();          self.mu -= self.lr * vmu
            vh0 = m * vh0 + (e * notneu).sum(); self.h0 -= self.lr * vh0

            db = np.zeros_like(self.b)
            np.add.at(db, hi, e)
            np.add.at(db, ai, -e)
            db += 2.0 * self.reg * self.b
            vb = m * vb + db;                self.b -= self.lr * vb

            if self.k:
                dU = np.zeros_like(self.U); dV = np.zeros_like(self.V)
                np.add.at(dU, hi, e[:, None] * self.V[ai])
                np.add.at(dU, ai, -e[:, None] * self.V[hi])
                np.add.at(dV, ai, e[:, None] * self.U[hi])
                np.add.at(dV, hi, -e[:, None] * self.U[ai])
                dU += 2.0 * self.reg * self.U
                dV += 2.0 * self.reg * self.V
                vU = m * vU + dU;            self.U -= self.lr * vU
                vV = m * vV + dV;            self.V -= self.lr * vV
        return self

    # ── prediction ────────────────────────────────────────────────────────────
    def interaction(self, home: str, away: str) -> float | None:
        """Bilinear residual (>0 favours home), or None if either team is unseen.

        Always 0.0 when k == 0 (the experiment's clean degenerate case)."""
        ih, ia = self.index.get(home), self.index.get(away)
        if ih is None or ia is None:
            return None
        if not self.k:
            return 0.0
        return float(self.U[ih] @ self.V[ia] - self.U[ia] @ self.V[ih])

    def margin(self, home: str, away: str, neutral: bool = True) -> float | None:
        """Full predicted goal margin (strength + interaction), or None if unseen."""
        ih, ia = self.index.get(home), self.index.get(away)
        if ih is None or ia is None:
            return None
        m = self.mu + (0.0 if neutral else self.h0) + self.b[ih] - self.b[ia]
        if self.k:
            m += float(self.U[ih] @ self.V[ia] - self.U[ia] @ self.V[ih])
        return m
