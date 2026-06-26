"""
Walk-forward evaluation: does the stress augmentation beat the baseline?

Honest by construction — it does NOT call predict.predict() (which would use
today's ratings and leak future results). Instead it reuses backtest.build_rows(),
whose `strength`/`pred` probabilities are already leak-free walk-forward (each game
scored from prior games only), applies the stress shift to that baseline, and scores
both with the SAME Brier/RPS/log-loss as the rest of the project.

Only fixtures present in BOTH data/results.csv and psycho_stress/data/fixtures_env.csv
are scored. Verdict rule (matches the project ethos): keep the augmentation only if it
lowers RPS/Brier out-of-sample; otherwise report no edge.

    python -m psycho_stress.evaluate
"""
from __future__ import annotations
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
import backtest as _bt                                   # read-only

from .env_data import load_fixtures
from .augment import team_load, _shift_probs, STRESS_LOGIT_K

SHRINK = 0.25


def evaluate(k: float = STRESS_LOGIT_K, shrink: float = SHRINK, verbose: bool = True):
    rows = {(r["date"], r["home"], r["away"]): r for r in _bt.build_rows()}
    fixtures = load_fixtures()
    if not fixtures:
        print("No fixtures_env.csv yet — add per-match environment rows to score.")
        return None

    base_b = base_r = base_l = aug_b = aug_r = aug_l = 0.0
    base_hits = aug_hits = n = 0
    detail = []
    for feat in fixtures:
        key = (feat["date"], feat["home"], feat["away"])
        r = rows.get(key)
        if r is None:
            continue
        actual = r["actual"]
        base_p = _bt.pred_probs(r, shrink)
        lh, la = team_load(feat, "home"), team_load(feat, "away")
        shift = k * (la["load"] - lh["load"])
        aug_p = _shift_probs(base_p, shift)

        base_b += _bt.brier(base_p, actual); aug_b += _bt.brier(aug_p, actual)
        base_r += _bt.rps(base_p, actual);   aug_r += _bt.rps(aug_p, actual)
        base_l += _bt.logloss(base_p, actual); aug_l += _bt.logloss(aug_p, actual)
        base_hits += (max(range(3), key=lambda i: base_p[i]) == actual)
        aug_hits += (max(range(3), key=lambda i: aug_p[i]) == actual)
        n += 1
        detail.append((key, shift, lh["load"], la["load"], base_p, aug_p, actual))

    if n == 0:
        print("No fixtures_env rows matched a logged result yet.")
        return None

    res = {"n": n, "k": k,
           "base": {"brier": base_b/n, "rps": base_r/n, "ll": base_l/n, "acc": base_hits/n},
           "aug":  {"brier": aug_b/n,  "rps": aug_r/n,  "ll": aug_l/n,  "acc": aug_hits/n}}
    if verbose:
        _report(res, detail)
    return res


def _report(res, detail):
    print(f"Stress augmentation vs baseline — {res['n']} fixtures (K={res['k']:.2f}, "
          f"walk-forward, leak-free):\n")
    print(f"  {'metric':<10}{'baseline':>10}{'augmented':>11}{'delta':>9}")
    print("  " + "-" * 40)
    for label, key, better_lower in (("RPS", "rps", True), ("Brier", "brier", True),
                                     ("LogLoss", "ll", True), ("Acc%", "acc", False)):
        b, a = res["base"][key], res["aug"][key]
        d = a - b
        scale = 100 if key == "acc" else 1
        mark = ""
        if abs(d) > 1e-6:
            improved = (d < 0) if better_lower else (d > 0)
            mark = "  ✓ better" if improved else "  ✗ worse"
        print(f"  {label:<10}{b*scale:>10.3f}{a*scale:>11.3f}{d*scale:>+9.3f}{mark}")

    verdict = "KEEP — beats baseline" if res["aug"]["rps"] < res["base"]["rps"] - 1e-4 \
        else "NO EDGE — does not beat baseline"
    print(f"\n  Verdict (RPS): {verdict}")

    big = sorted(detail, key=lambda d: -abs(d[1]))[:6]
    print("\n  Largest stress shifts (｜logit｜):")
    print(f"   {'fixture':<28}{'shift':>8}{'Hload':>7}{'Aload':>7}  base→aug (home win)")
    for (date, h, a), shift, lh, la, bp, ap, act in big:
        print(f"   {h+' v '+a:<28}{shift:>+8.2f}{lh:>7.2f}{la:>7.2f}   "
              f"{bp[0]:.2f}→{ap[0]:.2f}")


def _scored_rows(shrink=SHRINK):
    """Per-fixture (base_p, shift_per_unit_K, actual) for the matched games."""
    rows = {(r["date"], r["home"], r["away"]): r for r in _bt.build_rows()}
    out = []
    for feat in load_fixtures():
        r = rows.get((feat["date"], feat["home"], feat["away"]))
        if r is None:
            continue
        base_p = _bt.pred_probs(r, shrink)
        shift1 = team_load(feat, "away")["load"] - team_load(feat, "home")["load"]
        out.append((base_p, shift1, r["actual"]))
    return out


def cross_validate_k(folds=5, ks=tuple(i / 20 for i in range(0, 31)), seed=0):
    """
    k-fold CV: tune K on the train folds, score on the held-out fold (honest, optimism-
    corrected). Reports CV (out-of-sample) vs in-sample best vs baseline (K=0).
    """
    import random
    data = _scored_rows()
    if not data:
        print("No matched fixtures."); return
    idx = list(range(len(data)))
    random.Random(seed).shuffle(idx)
    parts = [idx[i::folds] for i in range(folds)]

    def rps_at(sub, k):
        return sum(_bt.rps(_shift_probs(data[i][0], k * data[i][1]), data[i][2])
                   for i in sub) / len(sub)

    cv_sum = base_sum = 0.0
    n = 0
    for f in range(folds):
        test = parts[f]
        train = [i for j in range(folds) if j != f for i in parts[j]]
        best_k = min(ks, key=lambda k: rps_at(train, k))
        cv_sum += rps_at(test, best_k) * len(test)
        base_sum += rps_at(test, 0.0) * len(test)
        n += len(test)
    in_best = min(ks, key=lambda k: rps_at(idx, k))
    print(f"\n  {folds}-fold CV of K (n={len(data)}):")
    print(f"    baseline (K=0)        RPS {base_sum/n:.4f}")
    print(f"    CV / out-of-sample    RPS {cv_sum/n:.4f}   (honest)")
    print(f"    in-sample best K={in_best:.2f}  RPS {rps_at(idx, in_best):.4f}")
    print(f"    optimism (in→CV)      {cv_sum/n - rps_at(idx, in_best):+.4f}")
    verdict = "real (small) edge" if cv_sum/n < base_sum/n - 1e-4 else \
        "NO out-of-sample edge (in-sample gain is optimism)"
    print(f"    → {verdict}")


def sweep_k(ks=(0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0)):
    """In-sample K sweep — indicative only (tuning K on the same games it's scored on
    flatters it). A real edge should show a broad, stable basin, not a sharp spike."""
    base = evaluate(k=0.0, verbose=False)
    print(f"\n  K sweep (baseline RPS {base['base']['rps']:.4f} at K=0):")
    print(f"   {'K':>6}{'RPS':>9}{'Brier':>9}{'Acc%':>7}")
    for k in ks:
        r = evaluate(k=k, verbose=False)
        a = r["aug"]
        better = "  ✓" if a["rps"] < base["base"]["rps"] - 1e-4 else ""
        print(f"   {k:>6.2f}{a['rps']:>9.4f}{a['brier']:>9.4f}{a['acc']*100:>6.0f}%{better}")


if __name__ == "__main__":
    import sys
    if "--sweep" in sys.argv:
        evaluate()
        sweep_k()
        cross_validate_k()
    else:
        evaluate()
