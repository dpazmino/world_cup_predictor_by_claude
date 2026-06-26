"""Does the expected-goals (Poisson) draw signal predict draws better than the
headline strength `p_draw`? Walk-forward over the logged 2026 WC games (predict
each game before updating Elo with its result — no leakage), comparing two draw
probabilities per match:

  p_draw        — strength W/D/L model, shrink 0.25 (the number we log)
  poisson_draw  — sum of diagonal scorelines P(k-k) from the two xG means

Scored as binary draw/not-draw: calibration, Brier, log-loss, discrimination.
"""
import math
from match_engine.elo import EloModel
from match_engine.ratings import combine_probs, canonical_name
from backtest import load_results
import tournament as T

SUP_PER_ELO, GOAL_TOTAL, SHRINK = 0.004, 2.6, 0.25
FIELD = {t for ts in T.GROUPS_2026.values() for t in ts}


def pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def poisson_draw(lh, la, maxg=10):
    return sum(pois(k, lh) * pois(k, la) for k in range(maxg + 1))


def main():
    rows = sorted(load_results(), key=lambda r: r["date"])
    elo = EloModel()
    recs = []
    for r in rows:
        d, h, a, neutral = r["date"], r["home"], r["away"], r["neutral"]
        ch, ca = canonical_name(h), canonical_name(a)
        if d >= "2026-06-11" and ch in FIELD and ca in FIELD:
            sp = elo.strength_probs(h, a, neutral=neutral, as_of=d)
            p_draw = combine_probs(sp, sp, blend=0.0, shrink=SHRINK)[1]
            diff = elo.effective_rating(h, d) - elo.effective_rating(a, d) \
                + (0.0 if neutral else elo.hfa)
            sup = diff * SUP_PER_ELO
            lh, la = max(0.15, (GOAL_TOTAL + sup) / 2), max(0.15, (GOAL_TOTAL - sup) / 2)
            recs.append((d, ch, ca, p_draw, poisson_draw(lh, la),
                         int(r["hg"] == r["ag"]), r["hg"], r["ag"]))
        elo.update(h, a, r["hg"], r["ag"], neutral=neutral, date=d)

    n = len(recs)
    draws = sum(x[5] for x in recs)
    rate = draws / n

    def brier(idx):
        return sum((x[idx] - x[5]) ** 2 for x in recs) / n

    def logloss(idx):
        s = 0.0
        for x in recs:
            p = min(max(x[idx], 1e-6), 1 - 1e-6)
            s += -(x[5] * math.log(p) + (1 - x[5]) * math.log(1 - p))
        return s / n

    def cond_mean(idx, want):
        sel = [x[idx] for x in recs if x[5] == want]
        return sum(sel) / len(sel) if sel else float("nan")

    print(f"Walk-forward over {n} logged 2026 WC games — {draws} draws "
          f"({100*rate:.0f}% actual draw rate)\n")
    print(f"{'signal':<16}{'mean':>7}{'Brier':>8}{'LogLoss':>9}"
          f"{'when DRAW':>11}{'when not':>10}{'gap':>7}")
    print("-" * 68)
    for name, idx in (("p_draw (logged)", 3), ("poisson_draw", 4)):
        md, mn = cond_mean(idx, 1), cond_mean(idx, 0)
        print(f"{name:<16}{sum(x[idx] for x in recs)/n*100:>6.0f}%{brier(idx):>8.3f}"
              f"{logloss(idx):>9.3f}{md*100:>10.0f}%{mn*100:>9.0f}%{(md-mn)*100:>+6.0f}")
    # blended signal (simple average of the two)
    blend = [(x[3] + x[4]) / 2 for x in recs]
    bb = sum((b - x[5]) ** 2 for b, x in zip(blend, recs)) / n
    print(f"{'blend (avg)':<16}{sum(blend)/n*100:>6.0f}%{bb:>8.3f}{'':>9}{'':>11}{'':>10}")

    print("\nPer actual-draw game (did either signal flag it?):")
    print(f"  {'match':<34}{'p_draw':>8}{'poisson':>9}")
    for d, h, a, pdr, pp, isd, hg, ag in recs:
        if isd:
            print(f"  {h+' '+str(hg)+'-'+str(ag)+' '+a:<34}{pdr*100:>7.0f}%{pp*100:>8.0f}%")


if __name__ == "__main__":
    main()
