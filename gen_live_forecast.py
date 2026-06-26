"""Generate WORLD_CUP_2026_FORECAST_live.md — the in-tournament title-odds table.

Runs the pre-tournament forecast (every game simulated) and the live forecast
(played games locked in) at the same N, then writes the table with a
`Qualify (pre -> live)` column. Reuses tournament.py end to end.
"""
import csv
import math
import os
import tournament as T

N = 100000
SEED = 0


def load_locked():
    """(date, home, hg, ag, away) for field-team games since 2026-06-11, in file order."""
    from match_engine.ratings import canonical_name
    field = {t for g in T.GROUPS_2026.values() for t in g}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "results.csv")
    rows = []
    for r in csv.reader(open(path, encoding="utf-8")):
        if not r or r[0].startswith("#") or len(r) < 5 or r[0] < "2026-06-11":
            continue
        h, a = canonical_name(r[1]), canonical_name(r[2])
        if h in field and a in field:
            rows.append((r[0], h, int(r[3]), int(r[4]), a))
    return rows


def main():
    teams = sorted({t for g in T.GROUPS_2026.values() for t in g})
    group_of = {t: g for g, ts in T.GROUPS_2026.items() for t in ts}
    played = T._load_played_results(T.GROUPS_2026)

    pre = T.run(N, seed=SEED, played=None)
    live = T.run(N, seed=SEED, played=played)

    qpre = {t: 100 * pre[t]["qualify"] / N for t in teams}
    pct = lambda t, k: 100 * live[t][k] / N
    ci = lambda p: 1.96 * math.sqrt(max(p, 1e-9) * (1 - p) / N) * 100

    ordered = sorted(teams, key=lambda t: live[t]["win"], reverse=True)
    locked = load_locked()

    out = []
    out.append("# 2026 FIFA World Cup — LIVE Forecast\n")
    out.append(f"*Generated 2026-06-16. In-tournament forecast: **{len(locked)} actual results "
               "locked in**, only the remaining games simulated "
               f"({N:,} Monte-Carlo runs, seed {SEED}, official 2026 bracket, shrink 0.25 / "
               "DRAW_BASE 0.45). `Qualify` shows pre-tournament → live odds.*\n")
    out.append("## Results locked in\n")
    for d, h, hg, ag, a in locked:
        out.append(f"- {d}: **{h} {hg}–{ag} {a}**")
    out.append("\n## 🏆 Title odds — all 48 teams (live)\n")
    out.append("| # | Team | Grp | Win grp | Qualify (pre → live) | R16 | QF | SF | Final | "
               "**Title** | ±95% |")
    out.append("|--:|------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|")
    for i, t in enumerate(ordered, 1):
        pw = live[t]["win"] / N
        ql = pct(t, "qualify")
        qcol = f"{qpre[t]:.0f}% → {ql:.0f}%"
        out.append(f"| {i} | {t} | {group_of[t]} | {pct(t,'group_win'):.0f}% | {qcol} | "
                   f"{pct(t,'R16'):.0f}% | {pct(t,'QF'):.0f}% | {pct(t,'SF'):.0f}% | "
                   f"{pct(t,'final'):.0f}% | **{100*pw:.1f}%** | ±{ci(pw):.1f} |")
    out.append("\n## Caveats")
    out.append("- `--live` locks in played games and simulates only the rest; a knockout draw "
               "(pens) falls back to a coin-flip.")
    out.append("- ~11 backfilled teams use real squads with estimated overalls; Elo "
               "self-corrects as results log.")
    out.append(f"\n*Reproduce: `python tournament.py --live -n {N}`*")

    with open("docs/WORLD_CUP_2026_FORECAST_live.md", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"Wrote docs/WORLD_CUP_2026_FORECAST_live.md  (locked={len(locked)}, leader={ordered[0]} "
          f"{100*live[ordered[0]]['win']/N:.1f}%)")


if __name__ == "__main__":
    main()
