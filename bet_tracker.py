"""Prospective betting tracker — forward test of the '-127 or longer' rule.

Locks each model pick + DK price BEFORE kickoff (persisted to data/bets.csv, so
re-runs never recompute a pre-game number = no leakage), then grades them from
data/results.csv as games finish. Flat 1-unit stakes. Reports three strategies:

  all       — bet the model's pick every game (baseline)
  rule      — bet only when the DK price on the pick is -127 or longer (BE <= 56%)
  model_ev  — bet only when model prob > the price's break-even (true +EV)

Run any time: it adds new fixtures from odds.txt, grades played ones, rewrites
docs/BET_TRACKER.md. Drop fresh lines in downloads/odds.txt + log results to keep
it current.
"""
import csv
import os
import datetime
from compare_odds import parse, model_probs, am2dec, ODDS
from match_engine.ratings import canonical_name
import tournament as T

LEDGER = os.path.join("data", "bets.csv")
BE_RULE = 0.56                       # price -127 -> break-even 55.95%
FIELD = {t for ts in T.GROUPS_2026.values() for t in ts}
COLS = ["home", "away", "kickoff", "pick", "american", "decimal", "breakeven",
        "model_prob", "ev", "meets_rule", "result", "pnl"]


def load_ledger():
    if not os.path.exists(LEDGER):
        return {}
    out = {}
    for r in csv.DictReader(open(LEDGER, encoding="utf-8")):
        out[frozenset((r["home"], r["away"]))] = r
    return out


def results_map():
    m = {}
    path = os.path.join("data", "results.csv")
    for r in csv.reader(open(path, encoding="utf-8")):
        if not r or r[0].startswith("#") or len(r) < 5 or r[0] < "2026-06-11":
            continue
        h, a = canonical_name(r[1]), canonical_name(r[2])
        try:
            m[frozenset((h, a))] = (h, int(r[3]), a, int(r[4]))
        except ValueError:
            pass
    return m


def add_new(ledger):
    for h, a, hm, dm, am, when in parse(ODDS):
        key = frozenset((h, a))
        if key in ledger or h not in FIELD or a not in FIELD:
            continue
        ph, pd, pa = model_probs(h, a)
        pick = h if ph >= pa else a
        am_pick = hm if pick == h else am  # American odds on the picked side
        dec = am2dec(am_pick)
        be = 1.0 / dec
        mp = max(ph, pa)
        ledger[key] = {
            "home": h, "away": a, "kickoff": when, "pick": pick,
            "american": am_pick.replace("−", "-"), "decimal": f"{dec:.3f}",
            "breakeven": f"{be:.4f}", "model_prob": f"{mp:.4f}",
            "ev": f"{mp*dec-1:.4f}", "meets_rule": str(be <= BE_RULE),
            "result": "pending", "pnl": "0",
        }


def grade(ledger):
    res = results_map()
    for key, b in ledger.items():
        if b["result"] != "pending":
            continue
        if key not in res:
            continue
        h, hg, a, ag = res[key]
        winner = h if hg > ag else (a if ag > hg else "draw")
        if b["pick"] == winner:
            b["result"] = "win"
            b["pnl"] = f"{float(b['decimal']) - 1:.3f}"
        else:
            b["result"] = "loss"
            b["pnl"] = "-1"


def save(ledger):
    with open(LEDGER, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for b in ledger.values():
            w.writerow(b)


def strat_stats(bets):
    graded = [b for b in bets if b["result"] in ("win", "loss")]
    wins = sum(1 for b in graded if b["result"] == "win")
    staked = len(graded)
    net = sum(float(b["pnl"]) for b in graded)
    roi = 100 * net / staked if staked else 0.0
    wr = 100 * wins / staked if staked else 0.0
    pend = sum(1 for b in bets if b["result"] == "pending")
    be = sum(float(b["breakeven"]) for b in graded) / staked * 100 if staked else 0
    return wins, staked - wins, staked, net, roi, wr, be, pend


def category_table(o, bets):
    """Bucket graded bets by the model's locked confidence tier and show whether
    higher confidence actually converts to profit. Tiers mirror predict._verdict
    (thresholds on the favourite's win prob)."""
    graded = [b for b in bets if b["result"] in ("win", "loss")]
    tiers = [("Coin flip", "<40%", 0.0, 0.40),
             ("Slight lean", "40–50%", 0.40, 0.50),
             ("Favoured", "50–62%", 0.50, 0.62),
             ("Clear favourite", "62–74%", 0.62, 0.74),
             ("Strong favourite", "≥74%", 0.74, 1.01)]
    o.append("\n## By model confidence tier (graded bets)\n")
    o.append("*Every graded bet bucketed by the model's **locked pre-kickoff** win prob on its "
             "pick (no recompute = no leakage). Backing the favourite flat, draw = loss. The "
             "question: does higher model confidence convert to profit, or just to a higher hit "
             "rate at shorter prices?*\n")
    o.append("| Tier | Fav prob | Bets | W-L | Win% | Net (u) | ROI |")
    o.append("|---|---|--:|:--:|--:|--:|--:|")
    for name, lab, lo, hi in tiers:
        sel = [b for b in graded if lo <= float(b["model_prob"]) < hi]
        if not sel:
            continue
        w = sum(1 for b in sel if b["result"] == "win")
        n = len(sel)
        net = sum(float(b["pnl"]) for b in sel)
        o.append(f"| {name} | {lab} | {n} | {w}-{n-w} | {100*w/n:.0f}% | {net:+.2f} | "
                 f"{100*net/n:+.1f}% |")


def report(ledger):
    bets = list(ledger.values())
    rule = [b for b in bets if b["meets_rule"] == "True"]
    evp = [b for b in bets if float(b["ev"]) > 0]
    today = datetime.date.today().isoformat()
    o = [f"# Prospective Bet Tracker — model picks vs DraftKings\n",
         f"*Updated {today}. Flat 1-unit stakes; draw = loss (3-way moneyline). Picks + "
         "prices locked before kickoff. Three strategies: **all** picks · **rule** (DK price "
         "−127 or longer, BE ≤ 56%) · **model_ev** (model prob > break-even). PnL in units.*\n",
         "## Running record\n",
         "| Strategy | Bets | Record | Net (u) | ROI | Win% | Avg BE% | Pending |",
         "|---|--:|:--:|--:|--:|--:|--:|--:|"]
    for name, sel in (("all", bets), ("rule (≤−127)", rule), ("model_ev", evp)):
        w, l, n, net, roi, wr, be, pend = strat_stats(sel)
        o.append(f"| {name} | {n} | {w}-{l} | {net:+.2f} | {roi:+.1f}% | {wr:.0f}% | "
                 f"{be:.0f}% | {pend} |")

    category_table(o, bets)

    def tbl(title, rowsel):
        o.append(f"\n## {title}\n")
        o.append("| Kickoff | Match | Pick | DK | BE% | Model% | EV | Rule | Result | PnL |")
        o.append("|---|---|---|--:|--:|--:|--:|:--:|:--:|--:|")
        for b in rowsel:
            r = {"win": "✅", "loss": "❌", "pending": "·"}[b["result"]]
            rule_m = "✓" if b["meets_rule"] == "True" else ""
            o.append(f"| {b['kickoff']} | {b['home']} v {b['away']} | {b['pick']} | "
                     f"{b['american']} | {float(b['breakeven'])*100:.0f} | "
                     f"{float(b['model_prob'])*100:.0f} | {float(b['ev'])*100:+.0f}% | "
                     f"{rule_m} | {r} | {b['pnl'] if b['result']!='pending' else ''} |")

    graded = [b for b in bets if b["result"] != "pending"]
    pending = [b for b in bets if b["result"] == "pending"]
    if graded:
        tbl(f"Graded ({len(graded)})", graded)
    tbl(f"Pending ({len(pending)})", pending)
    o.append("\n*Reproduce: `python bet_tracker.py` (reads downloads/odds.txt + "
             "data/results.csv; ledger in data/bets.csv).*")
    out = os.path.join("docs", "BET_TRACKER.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(o) + "\n")
    return out, len(bets), len(graded), len(pending)


def main():
    ledger = load_ledger()
    add_new(ledger)
    grade(ledger)
    save(ledger)
    out, total, g, p = report(ledger)
    print(f"Wrote {out} — {total} picks ledgered ({g} graded, {p} pending)")
    rule_n = sum(1 for b in ledger.values() if b["meets_rule"] == "True")
    print(f"  {rule_n} meet the -127 rule")


if __name__ == "__main__":
    main()
