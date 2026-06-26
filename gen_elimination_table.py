"""Generate WORLD_CUP_2026_ELIMINATION.md — per-team stage-of-elimination
distribution (the "when does each team go out" market view).

Runs the live forecast (played games locked in) and turns each team's cumulative
round-reached odds into an exit-stage distribution:

  P(exit group)  = 1 - qualify
  P(exit R32)    = qualify - R16
  P(exit R16)    = R16 - QF
  P(exit QF)     = QF - SF
  P(exit SF)     = SF - final
  P(runner-up)   = final - win
  P(champion)    = win

Sorted by expected stage reached (deepest run first). Reuses tournament.py.
"""
import math
import tournament as T

N = 100000
SEED = 0

# stage label -> depth index (group=0 ... champion=6), in finishing order
STAGES = ["Group", "R32", "R16", "QF", "SF", "Final", "Champion"]
# readable names for the expected-stage interpolation
LONG = ["group stage", "Last 32", "Last 16", "quarter-finals",
        "semi-finals", "final", "champion"]


def exp_label(e):
    """Describe a fractional expected-exit index e in [0,6] as a readable round span."""
    lo = min(int(e), 5)
    return f"{LONG[lo]}–{LONG[lo + 1]}"


def dist(tally, t):
    """Return [P(exit group), P(R32), P(R16), P(QF), P(SF), P(runner-up), P(champ)] as %."""
    q = tally[t]["qualify"] / N
    r16 = tally[t]["R16"] / N
    qf = tally[t]["QF"] / N
    sf = tally[t]["SF"] / N
    fin = tally[t]["final"] / N
    win = tally[t]["win"] / N
    raw = [1 - q, q - r16, r16 - qf, qf - sf, sf - fin, fin - win, win]
    return [100 * max(p, 0.0) for p in raw]


def main():
    played = T._load_played_results(T.GROUPS_2026)
    teams = sorted({t for g in T.GROUPS_2026.values() for t in g})
    group_of = {t: g for g, ts in T.GROUPS_2026.items() for t in ts}
    live = T.run(N, seed=SEED, played=played)

    rows = []
    for t in teams:
        d = dist(live, t)
        exp = sum(i * p / 100 for i, p in enumerate(d))     # expected depth (0..6)
        modal = STAGES[max(range(7), key=lambda i: d[i])]
        survive = 100 - d[0]                                # P(reach knockout) = qualify
        rows.append((t, d, exp, modal, survive))
    rows.sort(key=lambda r: r[2], reverse=True)

    out = []
    out.append("# 2026 FIFA World Cup — Stage-of-Elimination Distribution\n")
    import datetime
    out.append(f"*Generated {datetime.date.today().isoformat()}. Live forecast "
               f"({len(played)} results locked in, {N:,} Monte-Carlo runs, seed {SEED}). Each "
               "row is the probability a team's run **ends** at that stage (the cells sum to "
               "~100%). `Survive grp` = reach the knockout = `100 − Group`. Sorted by expected "
               "stage reached (deepest first). Use this against bookmaker 'stage of "
               "elimination' / 'to reach round X' props: de-vig the market and compare to the "
               "cumulative implied here.*\n")
    out.append("| # | Team | Grp | Group | R32 | R16 | QF | SF | Final | Champ | "
               "Modal exit | Survive grp |")
    out.append("|--:|------|:--:|--:|--:|--:|--:|--:|--:|--:|:--:|--:|")
    for i, (t, d, exp, modal, survive) in enumerate(rows, 1):
        cells = " | ".join(f"{p:.0f}%" for p in d)
        out.append(f"| {i} | {t} | {group_of[t]} | {cells} | {modal} | {survive:.0f}% |")

    # expected (probability-weighted) exit stage — the "how far do they really go" view
    out.append("\n## Expected exit stage (probability-weighted)")
    out.append("*Unlike the modal 'Exit' above, this averages the whole distribution, so it "
               "ranks deep-run favourites correctly. Index 0–6: 0 = out in group, 1 = Last 32, "
               "2 = Last 16, 3 = QF, 4 = SF, 5 = final, 6 = champion. The label is the round "
               "span the average falls in.*\n")
    out.append("| # | Team | Grp | Exp. index | Expected to be eliminated around |")
    out.append("|--:|------|:--:|--:|---|")
    for i, (t, d, exp, modal, survive) in enumerate(rows, 1):
        out.append(f"| {i} | {t} | {group_of[t]} | {exp:.2f} | {exp_label(exp)} |")

    # quick leaderboards for the two cleanest markets
    by_group_exit = sorted(rows, key=lambda r: r[1][0], reverse=True)
    out.append("\n## Most likely eliminated in the group stage")
    out.append("*(highest `Group` exit — the 'to be eliminated in group stage' prop)*\n")
    for t, d, *_ in by_group_exit[:10]:
        out.append(f"- {t}: **{d[0]:.0f}%**")

    out.append("\n## Best group-survival value candidates (under-covered, model-bullish)")
    out.append("*Strong reach-knockout odds on non-marquee sides — compare to the market's "
               "'to qualify' price; the model tends to over-rate these vs a US book.*\n")
    watch = {"Ivory Coast", "Egypt", "Morocco", "Japan", "Sweden", "Senegal",
             "Algeria", "Ghana", "Norway", "Australia", "South Korea"}
    for t, d, exp, modal, survive in sorted(rows, key=lambda r: r[4], reverse=True):
        if t in watch:
            out.append(f"- {t} ({group_of[t]}): **{survive:.0f}%** to reach the knockout")

    out.append("\n## Caveats")
    out.append("- Cumulative props ('reach round X', 'eliminated by round X') are cleaner than "
               "'exact stage' — the modal exit for most strong teams is R32 (the first cull), "
               "so exact-stage bets bunch up.")
    out.append("- Knockout ties are coin-flips and ~11 backfilled squads carry estimated Elo, "
               "so deep-run (SF/Final/Champ) cells understate true uncertainty.")
    out.append(f"\n*Reproduce: `python gen_elimination_table.py` (live, n={N}).*")

    with open("docs/WORLD_CUP_2026_ELIMINATION.md", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    deepest = rows[0]
    print(f"Wrote docs/WORLD_CUP_2026_ELIMINATION.md  (deepest expected run: {deepest[0]} "
          f"exp-stage {deepest[2]:.2f})")


if __name__ == "__main__":
    main()
