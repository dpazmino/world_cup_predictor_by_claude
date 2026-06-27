"""Does a tactical coach-clash signal improve prediction? (walk-forward test)

STATUS (65 WC games, 2026-06-27): NO out-of-sample edge — in fact the in-sample
best K is 0.00, and RPS rises monotonically for K>0 *and* K<0 (so it isn't a sign
error). The signal is non-degenerate (27/65 games flag a clash, mean |adv| 0.15),
it just carries no outcome information. Same null as player_match/psycho_stress —
scalar team strength already absorbs whatever tactics encode. Re-run as games grow.


Builds a single signed 'home tactical advantage' scalar from the coach/team style
scores (coach_matchup.score) encoding three mainstream tactical hypotheses, then
tests — on the SAME leak-free harness as player_match/psycho_stress — whether
shifting the baseline prediction by K·signal adds out-of-sample value. One tunable
(K), swept and 5-fold cross-validated. If the in-sample gain doesn't survive CV,
the signal is noise.

    python coach_clash.py                 # evaluate + sweep + CV on the WC games
    python coach_clash.py France Sweden   # just print the clash signal for a pairing

The three antisymmetric hypotheses (positive = favours home):
  H1  direct/counter teams exploit a high-pressing opponent (balls in behind)
  H2  high pressing smothers a slow, possession-based opponent
  H3  the bigger set-piece threat has an edge
Equal-weighted, so K is the only free parameter (honest single-knob test).
"""
import math
import sys

import backtest as _bt
from coach_matchup import score
from match_engine.team_tactics import TACTICAL_STYLES

SHRINK = 0.25
WC_CUT = "2026-06-11"


def clash_advantage(home: str, away: str):
    """Signed tactical edge to `home` in ~[-1, 1], or None if a side has no profile."""
    if home not in TACTICAL_STYLES or away not in TACTICAL_STYLES:
        return None
    sh, sa = score(home), score(away)

    def pos(x):
        return max(0.0, x)

    # H1: direct/counter vs high press (antisymmetric).
    attack_h = pos(sh["direct"]) + pos(sh["counter"])
    attack_a = pos(sa["direct"]) + pos(sa["counter"])
    h1 = attack_h * pos(sa["press"]) - attack_a * pos(sh["press"])
    # H2: high press vs slow possession (patient = negative 'direct').
    h2 = pos(sh["press"]) * pos(-sa["direct"]) - pos(sa["press"]) * pos(-sh["direct"])
    # H3: set-piece threat differential.
    h3 = sh["setpiece"] - sa["setpiece"]
    return (h1 + h2 + h3) / 3.0


def _shift_probs(p, shift):
    h, d, a = p
    h *= math.exp(shift / 2.0)
    a *= math.exp(-shift / 2.0)
    s = h + d + a
    return (h / s, d / s, a / s)


def _rows():
    out = []
    for r in _bt.build_rows():
        if r["date"] < WC_CUT:
            continue
        adv = clash_advantage(r["home"], r["away"])
        if adv is None:
            continue
        out.append((_bt.pred_probs(r, SHRINK), adv, r["actual"]))
    return out


def _rps_at(data, idx, k):
    return sum(_bt.rps(_shift_probs(data[i][0], k * data[i][1]), data[i][2])
              for i in idx) / len(idx)


def main():
    if len(sys.argv) >= 3:
        h, a = sys.argv[1], sys.argv[2]
        adv = clash_advantage(h, a)
        print(f"{h} vs {a}: tactical clash advantage(home) = "
              f"{'n/a' if adv is None else f'{adv:+.3f}'}")
        return

    data = _rows()
    n = len(data)
    full = list(range(n))
    print(f"Tactical clash vs baseline — {n} WC fixtures (walk-forward):\n")
    base_rps = _rps_at(data, full, 0.0)

    ks = [i / 20 for i in range(0, 41)]            # 0.0 .. 2.0
    print(f"  K sweep (baseline RPS {base_rps:.4f}):")
    print(f"   {'K':>6}{'RPS':>9}{'delta':>9}")
    for k in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.5, 2.0):
        r = _rps_at(data, full, k)
        mark = "  better" if r < base_rps - 1e-4 else ""
        print(f"   {k:>6.2f}{r:>9.4f}{r-base_rps:>+9.4f}{mark}")

    # 5-fold CV of K (honest out-of-sample).
    import random
    idx = list(range(n))
    random.Random(0).shuffle(idx)
    folds = 5
    parts = [idx[i::folds] for i in range(folds)]
    cv = base = 0.0
    for f in range(folds):
        test = parts[f]
        train = [i for j in range(folds) if j != f for i in parts[j]]
        bk = min(ks, key=lambda k: _rps_at(data, train, k))
        cv += _rps_at(data, test, bk) * len(test)
        base += _rps_at(data, test, 0.0) * len(test)
    inbest = min(ks, key=lambda k: _rps_at(data, full, k))
    print(f"\n  5-fold CV of K (n={n}):")
    print(f"    baseline (K=0)       RPS {base/n:.4f}")
    print(f"    CV / out-of-sample   RPS {cv/n:.4f}   (honest)")
    print(f"    in-sample best K={inbest:.2f} RPS {_rps_at(data, full, inbest):.4f}")
    print(f"    optimism (in→CV)     {cv/n - _rps_at(data, full, inbest):+.4f}")
    verdict = ("real (small) edge" if cv/n < base/n - 1e-4
               else "NO out-of-sample edge (in-sample gain is optimism)")
    print(f"    -> {verdict}")


if __name__ == "__main__":
    main()
