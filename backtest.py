"""
Backtest the strength predictor against real results — no simulation.

Models scored by Brier, log-loss, RPS (Ranked Probability Score — respects the
home<draw<away ordering), ECE, and accuracy:

  * prior    — fixed symmetric base rate
  * squad    — static squad-overall logistic
  * elo      — dynamic Elo, evaluated WALK-FORWARD (each match predicted from only
               prior matches, then the rating updated) — honest out-of-sample
  * strength — regularised Elo (squad prior → Elo by games played), walk-forward
  * pred     — the production model: strength shrunk toward the base-rate prior
  * market   — de-vigged closing odds, for the fixtures that carry them

Everything is analytic (no Monte-Carlo), so the whole backtest — including
`--sweep` (tune the shrink) and `--cv` (k-fold cross-validate the tuning) — runs
in well under a second. No API key, no cache.

Usage:
    python backtest.py                 # score all models
    python backtest.py --sweep         # tune shrink
    python backtest.py --cv            # cross-validate the shrink tuning
    python backtest.py --reliability   # calibration curves
"""
from __future__ import annotations
import sys
import os
import math
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from match_engine.ratings import BASE_PRIOR, combine_probs, outcome_probs, HOME_ADV_POINTS
from match_engine.elo import EloModel
from match_engine.odds import implied_probs, anchor

_RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "results.csv")
PRIOR = BASE_PRIOR


def load_results(path: str = _RESULTS) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split(",")
            if len(p) < 5:
                continue
            neutral = int(p[5]) if len(p) > 5 and p[5].strip() else 1
            odds = None
            if len(p) >= 9 and all(p[i].strip() for i in (6, 7, 8)):
                try:
                    odds = (float(p[6]), float(p[7]), float(p[8]))
                except ValueError:
                    odds = None
            rows.append({"date": p[0], "home": p[1], "away": p[2],
                         "hg": int(p[3]), "ag": int(p[4]),
                         "neutral": bool(neutral), "odds": odds})
    return rows


def outcome_index(hg: int, ag: int) -> int:
    return 0 if hg > ag else 1 if hg == ag else 2   # home / draw / away


def brier(probs, actual: int) -> float:
    return sum((p - (1.0 if i == actual else 0.0)) ** 2 for i, p in enumerate(probs))


def logloss(probs, actual: int) -> float:
    return -math.log(max(1e-9, min(1.0, probs[actual])))


def rps(probs, actual: int) -> float:
    """Ranked Probability Score for ordered outcomes home<draw<away. [0,1], lower better."""
    cum_p = cum_a = total = 0.0
    for i in range(len(probs) - 1):
        cum_p += probs[i]
        cum_a += 1.0 if i == actual else 0.0
        total += (cum_p - cum_a) ** 2
    return total / (len(probs) - 1)


def score(rows: list[dict], prob_fn) -> tuple:
    """Return (Brier, log-loss, RPS, accuracy%) averaged over rows."""
    bs = lls = rp = 0.0
    hits = 0
    for r in rows:
        p = prob_fn(r)
        a = r["actual"]
        bs += brier(p, a); lls += logloss(p, a); rp += rps(p, a)
        if max(range(3), key=lambda i: p[i]) == a:
            hits += 1
    n = len(rows)
    return bs / n, lls / n, rp / n, 100 * hits / n


# ── calibration ──────────────────────────────────────────────────────────────
def reliability_bins(rows, prob_fn, n_bins: int = 10):
    acc = [[0, 0.0, 0.0] for _ in range(n_bins)]
    for r in rows:
        p = prob_fn(r)
        a = r["actual"]
        for i in range(3):
            b = min(n_bins - 1, int(p[i] * n_bins))
            acc[b][0] += 1; acc[b][1] += p[i]; acc[b][2] += 1.0 if i == a else 0.0
    total = sum(b[0] for b in acc) or 1
    bins, ece = [], 0.0
    for k, (cnt, sp, sh) in enumerate(acc):
        lo, hi = k / n_bins, (k + 1) / n_bins
        if cnt:
            mp, obs = sp / cnt, sh / cnt
            ece += (cnt / total) * abs(mp - obs)
            bins.append((lo, hi, cnt, mp, obs))
        else:
            bins.append((lo, hi, 0, 0.0, 0.0))
    return bins, ece


def _cal_bar(pred: float, obs: float, width: int = 20) -> str:
    cells = ["."] * width
    pp = min(width - 1, max(0, round(pred * (width - 1))))
    op = min(width - 1, max(0, round(obs * (width - 1))))
    cells[pp] = "|"
    cells[op] = "X" if op == pp else "#"
    return "".join(cells)


def reliability_diagram(rows, prob_fn, label: str, n_bins: int = 10) -> None:
    bins, ece = reliability_bins(rows, prob_fn, n_bins)
    total = sum(b[2] for b in bins)
    print(f"\n  Reliability — {label}  (pooled H/D/A, {total} predictions, ECE {ece:.3f})")
    print(f"   {'prob bin':<9}{'n':>5}{'pred':>7}{'obs':>7}   diagram  |=pred  #=obs")
    for lo, hi, cnt, mp, obs in bins:
        if cnt:
            print(f"   {lo:.1f}-{hi:.1f}{cnt:>5}{mp:>7.2f}{obs:>7.2f}   {_cal_bar(mp, obs)}")


# ── models ───────────────────────────────────────────────────────────────────
def pred_probs(r, shrink: float):
    """Production model: regularised-Elo strength shrunk toward the prior."""
    return combine_probs(r["strength"], r["strength"], blend=0.0, shrink=shrink)


def build_rows():
    """Compute walk-forward Elo/strength, static squad, and market probs per fixture."""
    rows = [{**fx, "actual": outcome_index(fx["hg"], fx["ag"])} for fx in load_results()]

    elo = EloModel()
    for i in sorted(range(len(rows)), key=lambda i: rows[i]["date"]):
        r = rows[i]; d = r["date"]
        r["elo"] = elo.win_probabilities(r["home"], r["away"], neutral=r["neutral"], as_of=d)
        r["strength"] = elo.strength_probs(r["home"], r["away"], neutral=r["neutral"], as_of=d)
        elo.update(r["home"], r["away"], r["hg"], r["ag"], neutral=r["neutral"], date=d)

    for r in rows:
        r["squad"] = outcome_probs(r["home"], r["away"],
                                   home_adv=0.0 if r["neutral"] else HOME_ADV_POINTS)
        r["market"] = implied_probs(*r["odds"]) if r.get("odds") else None
    return rows


def print_models(rows, shrink: float) -> None:
    fns = {
        "prior":    lambda r: PRIOR,
        "squad":    lambda r: r["squad"],
        "elo":      lambda r: r["elo"],
        "strength": lambda r: r["strength"],
        "pred":     lambda r: pred_probs(r, shrink),
    }
    print(f"  {'model':<10}{'Brier':>9}{'LogLoss':>9}{'RPS':>8}{'ECE':>7}{'Acc%':>7}")
    print("  " + "-" * 49)
    for name, fn in fns.items():
        b, ll, rp, acc = score(rows, fn)
        _, ece = reliability_bins(rows, fn)
        tag = f"  (shrink={shrink:.2f})" if name == "pred" else ""
        print(f"  {name:<10}{b:>9.3f}{ll:>9.3f}{rp:>8.3f}{ece:>7.3f}{acc:>6.0f}%{tag}")


def market_report(rows, shrink: float, market_weight: float = 0.5) -> None:
    sub = [r for r in rows if r.get("market")]
    if not sub:
        print("\n  Market: no odds in dataset. Add decimal odds as columns 7-9 "
              "(home,draw,away) in results.csv to benchmark against the market.")
        return
    fns = {
        "prior":        lambda r: PRIOR,
        "pred":         lambda r: pred_probs(r, shrink),
        "market":       lambda r: r["market"],
        "pred+market":  lambda r: anchor(pred_probs(r, shrink), r["market"], market_weight),
    }
    print(f"\n  Market comparison over {len(sub)} fixtures with odds "
          f"(anchor weight {market_weight:.2f}):")
    print(f"  {'model':<14}{'Brier':>9}{'LogLoss':>9}{'RPS':>8}")
    print("  " + "-" * 40)
    for name, fn in fns.items():
        b, ll, rp, _ = score(sub, fn)
        print(f"  {name:<14}{b:>9.3f}{ll:>9.3f}{rp:>8.3f}")


# ── tuning ───────────────────────────────────────────────────────────────────
_SHRINKS = [i / 20 for i in range(0, 13)]   # 0.00 .. 0.60
_MIDX = {"brier": 0, "ll": 1, "rps": 2}


def _best_shrink(rows, metric: str = "rps") -> float:
    midx = _MIDX[metric]
    best_val, best = float("inf"), 0.30
    for lam in _SHRINKS:
        s = score(rows, lambda r, lam=lam: pred_probs(r, lam))
        if s[midx] < best_val:
            best_val, best = s[midx], lam
    return best


def sweep(rows) -> float:
    rs = [(score(rows, lambda r, lam=lam: pred_probs(r, lam)), lam) for lam in _SHRINKS]
    by = lambda i: min(rs, key=lambda x: x[0][i])
    for label, idx in (("RPS", 2), ("log-loss", 1), ("Brier", 0)):
        s, lam = by(idx)
        print(f"  Best by {label:<8}: shrink={lam:.2f}  "
              f"RPS={s[2]:.3f} LogLoss={s[1]:.3f} Brier={s[0]:.3f}")
    return by(2)[1]


def cross_validate(rows, k: int = 5, metric: str = "rps", seed: int = 0) -> None:
    import random
    order = list(range(len(rows)))
    random.Random(seed).shuffle(order)
    folds = [[rows[order[j]] for j in range(i, len(order), k)] for i in range(k)]
    print(f"  {k}-fold CV — tune shrink on train, score on held-out test (metric={metric}):\n")
    print(f"   {'fold':>4}{'n':>5}{'shrink':>8}{'RPS':>8}{'Brier':>8}{'LogLoss':>9}")
    agg = {"brier": 0.0, "ll": 0.0, "rps": 0.0}
    total = 0
    for f in range(k):
        test = folds[f]
        train = [r for g in range(k) if g != f for r in folds[g]]
        lam = _best_shrink(train, metric)
        b, ll, rp, _ = score(test, lambda r: pred_probs(r, lam))
        nt = len(test)
        agg["brier"] += b * nt; agg["ll"] += ll * nt; agg["rps"] += rp * nt; total += nt
        print(f"   {f+1:>4}{nt:>5}{lam:>8.2f}{rp:>8.3f}{b:>8.3f}{ll:>9.3f}")
    cv = {m: agg[m] / total for m in agg}
    lam0 = _best_shrink(rows, metric)
    ib, ill, irp, _ = score(rows, lambda r: pred_probs(r, lam0))
    print("   " + "-" * 41)
    print(f"   {'CV/oos':>9}{cv['rps']:>16.3f}{cv['brier']:>8.3f}{cv['ll']:>9.3f}")
    print(f"   {'in-sample':>9}  (shrink {lam0:.2f}) {irp:>6.3f}{ib:>8.3f}{ill:>9.3f}")
    print(f"   {'optimism':>9}{cv['rps']-irp:>16.3f}{cv['brier']-ib:>8.3f}{cv['ll']-ill:>9.3f}")
    print("\n  (optimism = how much in-sample tuning flatters the score; "
          "the CV row is the honest estimate)")


def _temp_scale(p, T: float) -> tuple:
    """Temperature scaling: flatten (T>1) or sharpen (T<1) a probability vector."""
    q = [x ** (1.0 / max(0.1, T)) for x in p]
    s = sum(q) or 1.0
    return tuple(x / s for x in q)


def cal_report(rows, k: int = 5, seed: int = 0) -> None:
    """
    Compare two calibrators of the strength model, both cross-validated:
      shrink      — pull toward the base-rate prior (production)
      temperature — flatten/sharpen the strength probabilities
    Shows that shrink-toward-prior is the better-calibrated choice for 1X2.
    """
    import random
    temps = [0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]
    order = list(range(len(rows)))
    random.Random(seed).shuffle(order)
    folds = [[rows[order[j]] for j in range(i, len(order), k)] for i in range(k)]

    def best_temp(tr):
        return min(temps, key=lambda T: score(tr, lambda r, T=T: _temp_scale(r["strength"], T))[2])

    agg = {"shrink": 0.0, "temperature": 0.0}
    tot = 0
    for f in range(k):
        test = folds[f]
        train = [r for g in range(k) if g != f for r in folds[g]]
        lam = _best_shrink(train, "rps")
        T = best_temp(train)
        agg["shrink"] += score(test, lambda r: pred_probs(r, lam))[2] * len(test)
        agg["temperature"] += score(test, lambda r, T=T: _temp_scale(r["strength"], T))[2] * len(test)
        tot += len(test)
    print(f"\n  Calibrator comparison ({k}-fold CV, RPS):")
    print(f"    shrink-to-prior : {agg['shrink']/tot:.3f}   (production)")
    print(f"    temperature     : {agg['temperature']/tot:.3f}")


def dc_report(rows, shrink: float) -> None:
    """
    Honest out-of-sample comparison of Dixon–Coles vs the strength predictor.
    Time-split: fit DC on the older 70% of matches, score on the newer 30% (the
    walk-forward `pred`/`strength` are already out-of-sample on those fixtures).
    """
    from match_engine.dixoncoles import DixonColesModel
    srt = sorted(rows, key=lambda r: r["date"])
    cut = int(len(srt) * 0.7)
    train, test = srt[:cut], srt[cut:]
    dc = DixonColesModel().fit([{k: r[k] for k in ("date", "home", "away", "hg", "ag", "neutral")}
                                for r in train])
    fns = {
        "prior":       lambda r: PRIOR,
        "pred":        lambda r: pred_probs(r, shrink),
        "strength":    lambda r: r["strength"],
        "dixon-coles": lambda r: dc.outcome_probs(r["home"], r["away"], neutral=r["neutral"]),
    }
    print(f"\n  Dixon–Coles vs strength — time-split (train {len(train)} / test {len(test)}):")
    print(f"  {'model':<14}{'Brier':>9}{'LogLoss':>9}{'RPS':>8}")
    print("  " + "-" * 40)
    for name, fn in fns.items():
        b, ll, rp, _ = score(test, fn)
        print(f"  {name:<14}{b:>9.3f}{ll:>9.3f}{rp:>8.3f}")
    print(f"\n  (DC home={dc.home:.2f} rho={dc.rho:.2f}; data-sparse — see docs/GAP_ANALYSIS.md)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true", help="tune the shrink parameter")
    ap.add_argument("--cv", action="store_true", help="k-fold cross-validate the shrink tuning")
    ap.add_argument("--dc", action="store_true", help="out-of-sample Dixon–Coles comparison")
    ap.add_argument("--cal", action="store_true", help="compare shrink vs temperature calibration")
    ap.add_argument("--reliability", action="store_true", help="print calibration curves")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--shrink", type=float, default=0.30)
    args = ap.parse_args()

    rows = build_rows()
    n_neutral = sum(1 for r in rows if r["neutral"])
    print(f"Scored over {len(rows)} fixtures ({n_neutral} neutral; Elo walk-forward, "
          f"no simulation):\n")

    if args.cv:
        cross_validate(rows, k=args.folds)
        print()

    if args.sweep:
        shrink = sweep(rows)
        print()
    else:
        shrink = args.shrink
    print_models(rows, shrink=shrink)
    market_report(rows, shrink=shrink)
    if args.dc:
        dc_report(rows, shrink=shrink)
    if args.cal:
        cal_report(rows)

    if args.reliability:
        reliability_diagram(rows, lambda r: pred_probs(r, shrink), "pred")
        reliability_diagram(rows, lambda r: r["strength"], "strength")
        reliability_diagram(rows, lambda r: r["squad"], "squad")
    elif not (args.sweep or args.cv):
        print("\n  (add --sweep to tune shrink, --cv to cross-validate, "
              "--reliability for calibration curves)")


if __name__ == "__main__":
    main()
