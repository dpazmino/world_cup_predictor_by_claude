"""
Walk-forward evaluation: does the latent-factor (collaborative-filtering) augmentation beat
the baseline — and does adding latent dimensions (k>0) beat scalar strength (k=0)?

Honest by construction — reuses backtest.build_rows() (leak-free walk-forward production
baseline `pred`) and the same Brier/RPS/log-loss scorers, and applies the interaction shift
on top. The interaction signal itself is fit WALK-FORWARD (signal.py), so nothing leaks.

The headline is `sweep_rank`: for k in {0,1,2,4}, the CV-tuned logit-K out-of-sample RPS.
k=0 is identically the baseline; if no k>0 beats it, latent factors add nothing on this data
(the expected result on sparse international results — see README / GAP_ANALYSIS).

Subsets (older games are valid for results-learned factors, but squads/teams drift):
  wc     — 2026 World Cup games (date >= 2026-06-11)   ← the cleanest recent test
  recent — date >= 2024-01-01
  full   — everything (most data for the factorizer to learn from)  ← default

    python -m latent_factor.evaluate              # rank sweep + K sweep + CV on 'full'
    python -m latent_factor.evaluate --all        # also wc + recent subsets
    python -m latent_factor.evaluate --standalone # also score MF as its own predictor
"""
from __future__ import annotations
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
import backtest as _bt                                     # read-only

from .signal import walk_forward_interactions
from .augment import _shift_probs

SHRINK = 0.25
SUBSETS = {"wc": "2026-06-11", "recent": "2024-01-01", "full": "0000"}
RANKS = (0, 1, 2, 4)

_ROWS_CACHE = None
_SIG_CACHE: dict[int, tuple] = {}


def _all_rows():
    global _ROWS_CACHE
    if _ROWS_CACHE is None:
        _ROWS_CACHE = _bt.build_rows()
    return _ROWS_CACHE


def _signals(rank: int):
    """Walk-forward (interactions, margins) over ALL rows, cached per rank."""
    if rank not in _SIG_CACHE:
        _SIG_CACHE[rank] = walk_forward_interactions(_all_rows(), k=rank)
    return _SIG_CACHE[rank]


def _rows(subset: str, rank: int):
    """List of (base_p, interaction, actual) for rated fixtures in the subset."""
    cut = SUBSETS[subset]
    rows = _all_rows()
    inter, _ = _signals(rank)
    out = []
    for j, r in enumerate(rows):
        if r["date"] < cut or inter[j] is None:
            continue
        out.append((_bt.pred_probs(r, SHRINK), inter[j], r["actual"]))
    return out


def _score(data, logit_k):
    b = rp = ll = ah = bh = 0.0
    n = len(data)
    for base_p, sig, act in data:
        aug_p = _shift_probs(base_p, logit_k * sig)
        b += _bt.brier(aug_p, act); rp += _bt.rps(aug_p, act); ll += _bt.logloss(aug_p, act)
        bh += (max(range(3), key=lambda i: base_p[i]) == act)
        ah += (max(range(3), key=lambda i: aug_p[i]) == act)
    return {"brier": b/n, "rps": rp/n, "ll": ll/n, "base_acc": bh/n, "acc": ah/n, "n": n}


# ── per-rank K analysis (mirrors player_match) ────────────────────────────────
def evaluate(subset="full", rank=2, k=1.0, verbose=True):
    data = _rows(subset, rank)
    if not data:
        print(f"No rated fixtures in subset '{subset}' (rank {rank})."); return None
    base, aug = _score(data, 0.0), _score(data, k)
    if verbose:
        print(f"Latent interaction vs baseline — subset '{subset}', rank k={rank}, "
              f"{aug['n']} fixtures (logit-K={k:.2f}, walk-forward):\n")
        print(f"  {'metric':<9}{'baseline':>10}{'augmented':>11}{'delta':>9}")
        print("  " + "-" * 39)
        for lab, key in (("RPS", "rps"), ("Brier", "brier"), ("LogLoss", "ll")):
            d = aug[key] - base[key]
            mark = "  ✓" if d < -1e-6 else ("  ✗" if d > 1e-6 else "")
            print(f"  {lab:<9}{base[key]:>10.4f}{aug[key]:>11.4f}{d:>+9.4f}{mark}")
        print(f"  {'Acc%':<9}{base['acc']*100:>10.1f}{aug['acc']*100:>11.1f}"
              f"{(aug['acc']-base['acc'])*100:>+9.1f}")
    return {"base": base, "aug": aug}


def sweep_k(subset="full", rank=2, ks=tuple(i/10 for i in range(0, 21, 2))):
    data = _rows(subset, rank)
    if not data:
        print(f"No fixtures for subset '{subset}', rank {rank}."); return
    base = _score(data, 0.0)
    print(f"\n  logit-K sweep (subset '{subset}', rank {rank}, baseline RPS {base['rps']:.4f}):")
    print(f"   {'K':>6}{'RPS':>9}{'Brier':>9}{'Acc%':>7}")
    for k in ks:
        s = _score(data, k)
        mark = "  ✓" if s["rps"] < base["rps"] - 1e-4 else ""
        print(f"   {k:>6.2f}{s['rps']:>9.4f}{s['brier']:>9.4f}{s['acc']*100:>6.0f}%{mark}")


def _cv_rps(data, folds=5, ks=tuple(i/20 for i in range(0, 41)), seed=0):
    """Return (baseline_rps, cv_rps, in_sample_best_rps, best_k_in_sample)."""
    import random
    idx = list(range(len(data)))
    random.Random(seed).shuffle(idx)
    parts = [idx[i::folds] for i in range(folds)]

    def rps_at(sub, k):
        return sum(_bt.rps(_shift_probs(data[i][0], k*data[i][1]), data[i][2])
                   for i in sub) / len(sub)

    cv = base = 0.0; n = 0
    for f in range(folds):
        test = parts[f]; train = [i for j in range(folds) if j != f for i in parts[j]]
        bk = min(ks, key=lambda k: rps_at(train, k))
        cv += rps_at(test, bk) * len(test); base += rps_at(test, 0.0) * len(test); n += len(test)
    inbest_k = min(ks, key=lambda k: rps_at(idx, k))
    return base/n, cv/n, rps_at(idx, inbest_k), inbest_k


def cross_validate_k(subset="full", rank=2, folds=5):
    data = _rows(subset, rank)
    if not data:
        print(f"No fixtures for subset '{subset}', rank {rank}."); return
    base, cv, inbest, ink = _cv_rps(data, folds)
    print(f"\n  {folds}-fold CV of logit-K (subset '{subset}', rank {rank}, n={len(data)}):")
    print(f"    baseline (K=0)       RPS {base:.4f}")
    print(f"    CV / out-of-sample   RPS {cv:.4f}   (honest)")
    print(f"    in-sample best K={ink:.2f} RPS {inbest:.4f}")
    print(f"    optimism (in→CV)     {cv - inbest:+.4f}")
    print(f"    → {'real (small) edge' if cv < base - 1e-4 else 'NO out-of-sample edge (in-sample gain is optimism)'}")


# ── the headline: does latent rank help at all? ───────────────────────────────
def sweep_rank(subset="full", ranks=RANKS, folds=5):
    print(f"\n  RANK SWEEP — does k>0 beat scalar strength? (subset '{subset}', "
          f"{folds}-fold CV-tuned logit-K)")
    print(f"   {'rank k':>7}{'n':>5}{'baseline':>10}{'CV(oos)':>10}{'best K':>8}  verdict")
    print("   " + "-" * 58)
    for k in ranks:
        data = _rows(subset, k)
        if not data:
            print(f"   {k:>7}{'—':>5}   (no rated fixtures)"); continue
        base, cv, _inbest, ink = _cv_rps(data, folds)
        if k == 0:
            verdict = "baseline (no interaction)"
        else:
            verdict = "EDGE" if cv < base - 1e-4 else "no edge"
        print(f"   {k:>7}{len(data):>5}{base:>10.4f}{cv:>10.4f}{ink:>8.2f}  {verdict}")
    print("\n  (k=0 has no interaction term, so baseline==CV by construction.)")


# ── optional: MF as its own standalone predictor (rough, fixed link) ──────────
def _margin_to_probs(m, slope=0.9, draw_band=0.5, floor=0.02):
    import math
    sig = lambda z: 1.0 / (1.0 + math.exp(-z))
    ph = sig(slope * (m - draw_band))
    pa = sig(slope * (-m - draw_band))
    pd = max(floor, 1.0 - ph - pa)
    s = ph + pd + pa
    return (ph/s, pd/s, pa/s)


def standalone_rank_sweep(subset="full", ranks=RANKS):
    """Score the latent margin model AS ITS OWN W/D/L predictor (vs production `pred`).

    Diagnostic only: uses a FIXED, unfitted margin→prob link, so absolute RPS is rough and
    will look worse than the calibrated baseline — read the TREND across ranks, not levels."""
    cut = SUBSETS[subset]
    rows = _all_rows()
    print(f"\n  STANDALONE MF predictor (subset '{subset}', fixed link — read the trend):")
    print(f"   {'rank k':>7}{'n':>5}{'MF RPS':>9}{'pred RPS':>10}")
    print("   " + "-" * 33)
    for k in ranks:
        _inter, margins = _signals(k)
        mf = pr = 0.0; n = 0
        for j, r in enumerate(rows):
            if r["date"] < cut or margins[j] is None:
                continue
            mf += _bt.rps(_margin_to_probs(margins[j]), r["actual"])
            pr += _bt.rps(_bt.pred_probs(r, SHRINK), r["actual"]); n += 1
        if n:
            print(f"   {k:>7}{n:>5}{mf/n:>9.4f}{pr/n:>10.4f}")


if __name__ == "__main__":
    subsets = ["full"] if "--all" not in sys.argv else ["full", "recent", "wc"]
    for s in subsets:
        print("\n" + "=" * 60)
        sweep_rank(s)
        sweep_k(s, rank=2)
        cross_validate_k(s, rank=2)
        if "--standalone" in sys.argv:
            standalone_rank_sweep(s)
