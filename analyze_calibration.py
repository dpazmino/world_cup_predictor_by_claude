"""Calibration / reliability of the model's predictions on the 2026 WC games.

Walk-forward (predict each game before updating Elo — no leakage), then ask the
honest question: when the model said X%, did it happen X% of the time? Writes
docs/MODEL_CALIBRATION.md with Brier/log-loss/accuracy/ECE, a 3-way reliability
curve, and the favourite-confidence view (the betting-relevant one).
"""
import math
import datetime
from match_engine.elo import EloModel
from match_engine.ratings import combine_probs, canonical_name
from backtest import load_results
import tournament as T

FIELD = {t for ts in T.GROUPS_2026.values() for t in ts}


def collect():
    rows = sorted(load_results(), key=lambda r: r["date"])
    elo = EloModel()
    recs = []  # (ph, pd, pa, outcome 0/1/2)
    for r in rows:
        d, h, a, neu = r["date"], r["home"], r["away"], r["neutral"]
        ch, ca = canonical_name(h), canonical_name(a)
        if d >= "2026-06-11" and ch in FIELD and ca in FIELD:
            sp = elo.strength_probs(h, a, neutral=neu, as_of=d)
            ph, pd, pa = combine_probs(sp, sp, blend=0.0, shrink=0.25)
            out = 0 if r["hg"] > r["ag"] else (2 if r["ag"] > r["hg"] else 1)
            recs.append((ph, pd, pa, out))
        elo.update(h, a, r["hg"], r["ag"], neutral=neu, date=d)
    return recs


def main():
    recs = collect()
    n = len(recs)
    brier = sum((p[0]-(o == 0))**2 + (p[1]-(o == 1))**2 + (p[2]-(o == 2))**2
                for *p, o in [(r[0], r[1], r[2], r[3]) for r in recs]) / n
    ll = -sum(math.log(max([r[0], r[1], r[2]][r[3]], 1e-9)) for r in recs) / n
    # favourite (home vs away) accuracy = the tracker's "top-pick"
    hits = sum(1 for ph, pd, pa, o in recs
               if (o == 0 and ph >= pa) or (o == 2 and pa > ph))
    acc = hits / n

    # 3-way reliability: every (prob, did-it-happen) pair across the 3 outcomes
    pairs = []
    for ph, pd, pa, o in recs:
        pairs += [(ph, o == 0), (pd, o == 1), (pa, o == 2)]
    bins = [(i/10, (i+1)/10) for i in range(10)]
    ece = 0.0
    rel = []
    for lo, hi in bins:
        sel = [(p, h) for p, h in pairs if lo <= p < hi or (hi == 1.0 and p == 1.0)]
        if not sel:
            rel.append((lo, hi, 0, None, None)); continue
        mp = sum(p for p, _ in sel)/len(sel)
        ma = sum(1 for _, h in sel if h)/len(sel)
        ece += len(sel)/len(pairs) * abs(mp-ma)
        rel.append((lo, hi, len(sel), mp, ma))

    # favourite-confidence view (betting-relevant)
    fav = []
    for ph, pd, pa, o in recs:
        fp = max(ph, pa)
        won = (o == 0 and ph >= pa) or (o == 2 and pa > ph)
        fav.append((fp, won))
    fbuckets = [(0.40, 0.45), (0.45, 0.50), (0.50, 0.55), (0.55, 0.60),
                (0.60, 0.70), (0.70, 1.01)]

    o = []
    today = datetime.date.today().isoformat()
    o.append("# Model Calibration — 2026 World Cup (walk-forward)\n")
    o.append(f"*Generated {today}. {n} tournament games, each predicted with ratings as of its "
             "own date (no leakage). Calibration asks: when the model said X%, did it happen "
             "X%? Brier/log-loss are 3-way (lower better); ECE = expected calibration error "
             "(0 = perfect).*\n")
    o.append("## Headline\n")
    o.append(f"- **Top-pick accuracy:** {hits}/{n} = **{100*acc:.0f}%**")
    o.append(f"- **Brier (3-way):** {brier:.3f}  (naive 1/3 baseline = 0.667)")
    o.append(f"- **Log-loss:** {ll:.3f}")
    o.append(f"- **ECE:** {ece:.3f}  ({'well calibrated' if ece < 0.05 else 'some miscalibration'})\n")

    o.append("## Reliability curve (all outcomes)\n")
    o.append("*Each row: predictions whose probability fell in that band, vs how often the "
             "outcome actually happened. `pred ≈ actual` = calibrated.*\n")
    o.append("| Prob band | n | Mean pred | Actual | Bar (pred=│ actual=█) |")
    o.append("|---|--:|--:|--:|---|")
    for lo, hi, cnt, mp, ma in rel:
        if cnt == 0:
            continue
        bar = ""
        a_blocks = int(round(ma*20))
        bar = "█"*a_blocks + "·"*(20-a_blocks)
        marker = int(round(mp*20))
        bar = bar[:marker] + "│" + bar[marker+1:] if 0 <= marker < 20 else bar
        o.append(f"| {lo*100:.0f}–{hi*100:.0f}% | {cnt} | {mp*100:.0f}% | {ma*100:.0f}% | `{bar}` |")

    o.append("\n## Favourite-confidence view (the betting-relevant one)\n")
    o.append("*Bucket games by how confident the model was in its pick, then its actual win "
             "rate. If actual ≥ pred, the model is well-calibrated-or-underconfident — good for "
             "value betting.*\n")
    o.append("| Model said | Games | Won | vs claimed |")
    o.append("|---|--:|--:|---|")
    for lo, hi in fbuckets:
        sel = [w for fp, w in fav if lo <= fp < hi]
        if not sel:
            continue
        rate = 100*sum(sel)/len(sel)
        claimed = 100*sum(fp for fp, _ in fav if lo <= fp < hi)/len(sel)
        gap = rate - claimed
        o.append(f"| {lo*100:.0f}–{hi*100:.0f}% | {len(sel)} | {rate:.0f}% | "
                 f"{'+' if gap>=0 else ''}{gap:.0f} pts |")

    o.append("\n## Read")
    under = sum(1 for fp, w in fav) and \
        (100*sum(w for _, w in fav)/len(fav)) - (100*sum(fp for fp, _ in fav)/len(fav))
    o.append(f"- The model's picks won **{100*sum(w for _,w in fav)/len(fav):.0f}%** overall "
             f"while claiming **{100*sum(fp for fp,_ in fav)/len(fav):.0f}%** on average — a "
             f"**{under:+.0f} pt** gap, i.e. the model is **{'under' if under>0 else 'over'}-"
             "confident** in its favourites.")
    o.append("- Under-confidence is the *good* direction for betting: the model's favourites "
             "win more than its numbers say, so a fair-priced bet on them carries value.")
    o.append("- Caveat: 55 games is a small sample and these are all **group-stage** games, "
             "where the draw blind spot inflates Brier; knockout (binary advance) calibration "
             "will be measured separately.")
    o.append(f"\n*Reproduce: `python analyze_calibration.py` (walk-forward over data/results.csv).*")

    import os
    with open(os.path.join("docs", "MODEL_CALIBRATION.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(o) + "\n")
    print(f"Wrote docs/MODEL_CALIBRATION.md — {n} games, acc {100*acc:.0f}%, "
          f"Brier {brier:.3f}, ECE {ece:.3f}")


if __name__ == "__main__":
    main()
