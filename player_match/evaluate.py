"""
Walk-forward evaluation: does the style-matchup augmentation beat the baseline?

Honest by construction — reuses backtest.build_rows() (leak-free walk-forward baseline)
and the same Brier/RPS/log-loss scorers, and applies the matchup shift on top.

Subsets (player_stats.csv is *current* squads, so older games are anachronistic):
  wc     — 2026 World Cup games (date >= 2026-06-11)   ← the honest test
  recent — date >= 2024-01-01                          ← still roughly valid
  full   — everything                                  ← low-confidence context only

    python -m player_match.evaluate            # wc subset + sweep + CV
    python -m player_match.evaluate --all      # also recent + full
"""
from __future__ import annotations
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
import backtest as _bt                                     # read-only

from .matchup import net_advantage
from .augment import _shift_probs, MATCHUP_LOGIT_K

SHRINK = 0.25
SUBSETS = {"wc": "2026-06-11", "recent": "2024-01-01", "full": "0000"}


def _rows(subset: str):
    cut = SUBSETS[subset]
    out = []
    for r in _bt.build_rows():
        if r["date"] < cut:
            continue
        net = net_advantage(r["home"], r["away"])
        if net is None:
            continue
        out.append((_bt.pred_probs(r, SHRINK), net, r["actual"]))
    return out


def _score(data, k):
    b = rp = ll = bh = ah = 0.0
    n = len(data)
    for base_p, net, act in data:
        aug_p = _shift_probs(base_p, k * net)
        b += _bt.brier(aug_p, act); rp += _bt.rps(aug_p, act); ll += _bt.logloss(aug_p, act)
        bh += (max(range(3), key=lambda i: base_p[i]) == act)
        ah += (max(range(3), key=lambda i: aug_p[i]) == act)
    return {"brier": b/n, "rps": rp/n, "ll": ll/n, "base_acc": bh/n, "acc": ah/n, "n": n}


def evaluate(subset="wc", k=MATCHUP_LOGIT_K, verbose=True):
    data = _rows(subset)
    if not data:
        print(f"No rated fixtures in subset '{subset}'."); return None
    base, aug = _score(data, 0.0), _score(data, k)
    if verbose:
        print(f"Style-matchup vs baseline — subset '{subset}', {aug['n']} fixtures "
              f"(K={k:.2f}, walk-forward):\n")
        print(f"  {'metric':<9}{'baseline':>10}{'augmented':>11}{'delta':>9}")
        print("  " + "-" * 39)
        for lab, key in (("RPS", "rps"), ("Brier", "brier"), ("LogLoss", "ll")):
            d = aug[key] - base[key]
            mark = "  ✓" if d < -1e-6 else ("  ✗" if d > 1e-6 else "")
            print(f"  {lab:<9}{base[key]:>10.4f}{aug[key]:>11.4f}{d:>+9.4f}{mark}")
        print(f"  {'Acc%':<9}{base['acc']*100:>10.1f}{aug['acc']*100:>11.1f}"
              f"{(aug['acc']-base['acc'])*100:>+9.1f}")
    return {"base": base, "aug": aug}


def sweep_k(subset="wc", ks=tuple(i/10 for i in range(0, 21, 2))):
    data = _rows(subset)
    base = _score(data, 0.0)
    print(f"\n  K sweep (subset '{subset}', baseline RPS {base['rps']:.4f}):")
    print(f"   {'K':>6}{'RPS':>9}{'Brier':>9}{'Acc%':>7}")
    for k in ks:
        s = _score(data, k)
        mark = "  ✓" if s["rps"] < base["rps"] - 1e-4 else ""
        print(f"   {k:>6.2f}{s['rps']:>9.4f}{s['brier']:>9.4f}{s['acc']*100:>6.0f}%{mark}")


def cross_validate_k(subset="wc", folds=5, ks=tuple(i/20 for i in range(0, 41)), seed=0):
    import random
    data = _rows(subset)
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
    inbest = min(ks, key=lambda k: rps_at(idx, k))
    print(f"\n  {folds}-fold CV of K (subset '{subset}', n={len(data)}):")
    print(f"    baseline (K=0)       RPS {base/n:.4f}")
    print(f"    CV / out-of-sample   RPS {cv/n:.4f}   (honest)")
    print(f"    in-sample best K={inbest:.2f} RPS {rps_at(idx, inbest):.4f}")
    print(f"    optimism (in→CV)     {cv/n - rps_at(idx, inbest):+.4f}")
    print(f"    → {'real (small) edge' if cv/n < base/n - 1e-4 else 'NO out-of-sample edge (in-sample gain is optimism)'}")


if __name__ == "__main__":
    evaluate("wc")
    sweep_k("wc")
    cross_validate_k("wc")
    if "--all" in sys.argv:
        for s in ("recent", "full"):
            print("\n" + "=" * 50)
            evaluate(s); cross_validate_k(s)
