"""Generate WORLD_CUP_2026_LIVE_BRACKET.md — the deterministic most-likely
group->knockout projection conditioned on played results (--live).

Reuses tournament.py's official bracket templates (_R32/_TREE/_THIRD_SLOTS/
_assign_thirds) and pairwise win probs. Group winner = most-likely (P finish 1st);
2nd/3rd ordered by P(qualify); 8 best thirds = highest-P(qualify) thirds fit to
FIFA's slot table; knockout = favourite (higher P(win)) advances, decisive played
knockout results locked. Prints the full markdown to stdout.
"""
import sys
from collections import defaultdict
import tournament as T


def nid(prefix, name):
    return prefix + name.replace(" ", "_").replace("'", "")


def main(n=60000, seed=0):
    played = T._load_played_results(T.GROUPS_2026)
    teams = sorted({t for g in T.GROUPS_2026.values() for t in g})
    pair, host_home = T.precompute(teams)
    elo = T.default_model()

    # --- Monte-Carlo tallies for group_win / qualify (drives the projection) ---
    tally = T.run(n, seed=seed, played=played)
    gw_p = {t: tally[t]["group_win"] / n for t in teams}
    q_p = {t: tally[t]["qualify"] / n for t in teams}

    # --- Project group standings: winner by P(1st); 2nd/3rd/4th by P(qualify) ---
    order = {}            # group -> [1st,2nd,3rd,4th]
    for g, ts in T.GROUPS_2026.items():
        winner = max(ts, key=lambda t: gw_p[t])
        rest = sorted([t for t in ts if t != winner], key=lambda t: q_p[t], reverse=True)
        order[g] = [winner] + rest

    # --- 8 best thirds by P(qualify), fit to FIFA slot table ---
    thirds = sorted(((g, order[g][2]) for g in order), key=lambda x: q_p[x[1]], reverse=True)
    best = thirds[:8]
    third_group = {g: t for g, t in best}
    slot_team = T._assign_thirds(third_group)
    if slot_team is None:
        sys.exit("slot assignment failed — FIFA table couldn't seat these 8 thirds")

    qualifiers = ({order[g][0] for g in order} | {order[g][1] for g in order}
                  | {t for _, t in best})

    # --- Deterministic knockout: favourite advances, lock decisive played games ---
    gwn = {g: order[g][0] for g in order}
    grn = {g: order[g][1] for g in order}

    def team_of(spec):
        kind, key = spec
        return gwn[key] if kind == "W" else grn[key] if kind == "RU" else slot_team[key]

    def play(a, b):
        res = played.get(frozenset((a, b)))
        if res is not None and res[a] != res[b]:
            return a if res[a] > res[b] else b
        return a if pair[(a, b)]["adv"] >= 0.5 else b

    win = {}
    for mid, (sa, sb) in T._R32.items():
        win[mid] = play(team_of(sa), team_of(sb))
    for mid in (89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 104):
        x, y = T._TREE[mid]
        win[mid] = play(win[x], win[y])
    champ = win[104]

    # --- Per-team projected finish (the stage each team is eliminated at) ---------
    # loser of a match is eliminated at the stage that match belongs to.
    field = {t for ts in T.GROUPS_2026.values() for t in ts}
    stage_of = {t: "Group Stage" for t in field if t not in qualifiers}

    def loser(mid, a, b):
        return b if win[mid] == a else a

    for mid, (sa, sb) in T._R32.items():                       # 16 matches → Last 32 exits
        stage_of[loser(mid, team_of(sa), team_of(sb))] = "Last 32"
    round_label = {tuple(range(89, 97)): "Last 16", (97, 98, 99, 100): "Quarter-Finals",
                   (101, 102): "Semi-Finals"}
    for mids, lab in round_label.items():
        for mid in mids:
            x, y = T._TREE[mid]
            stage_of[loser(mid, win[x], win[y])] = lab
    stage_of[loser(104, win[101], win[102])] = "Runner-Up"     # lost the final
    stage_of[champ] = "Winner"

    # --- Emit markdown ---
    n_played = len(played)
    out = []
    out.append("# 2026 FIFA World Cup — Live Group-to-Bracket Projection\n")
    out.append(f"*Generated 2026-06-16. Conditioned on the {n_played} results played so far "
               "(`--live`). Group winner = most-likely group winner (P finish 1st); 2nd/3rd "
               "ordered by P(qualify); the 8 qualifying thirds are the highest-P(qualify) "
               "third-placed teams that fit FIFA's slot table. Knockout = official 2026 "
               "bracket, favourite advances. ✓ = projected to qualify.*\n")
    out.append("```mermaid")
    out.append("flowchart LR")
    out.append("  classDef champ fill:#ffd700,stroke:#b8860b,stroke-width:3px,color:#000;")

    gname = {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E", "F": "F",
             "G": "G", "H": "H", "I": "I", "J": "J", "K": "K", "L": "L"}
    for g in T.GROUPS_2026:
        out.append(f'  subgraph G{g}["Group {gname[g]}"]')
        out.append("    direction TB")
        for rank, t in enumerate(order[g], 1):
            q = "✓" if t in qualifiers else ""
            label = f"{rank}. {t} {q}".rstrip()
            out.append(f'    {nid("g_", t)}["{label}"]')
        out.append("  end")

    # knockout subgraphs, nodes named by projected winners
    r16 = [win[m] for m in sorted(T._R32)]
    qf = [win[m] for m in (89, 90, 91, 92, 93, 94, 95, 96)]
    sf = [win[m] for m in (97, 98, 99, 100)]
    fin = [win[m] for m in (101, 102)]

    def emit_round(title, key, names):
        out.append(f'  subgraph {key}["{title}"]')
        out.append("    direction TB")
        for nm in names:
            out.append(f'    {nid(key.lower()+"_", nm)}["{nm}"]')
        out.append("  end")

    emit_round("Round of 16", "K1", r16)
    emit_round("Quarter-finals", "K2", qf)
    emit_round("Semi-finals", "K3", sf)
    emit_round("Final", "K4", fin)
    out.append('  subgraph KC["Champion"]')
    out.append(f'    champ["🏆 {champ}"]')
    out.append("  end")

    # edges: group teams -> R16 winner node
    for mid, (sa, sb) in T._R32.items():
        a, b = team_of(sa), team_of(sb)
        w = win[mid]
        out.append(f'  {nid("g_", a)} --> {nid("k1_", w)}')
        out.append(f'  {nid("g_", b)} --> {nid("k1_", w)}')
    # R16 -> QF
    pref = {89: "k1_", 90: "k1_", 91: "k1_", 92: "k1_", 93: "k1_", 94: "k1_", 95: "k1_", 96: "k1_"}
    for mid in (89, 90, 91, 92, 93, 94, 95, 96):
        x, y = T._TREE[mid]
        out.append(f'  {nid("k1_", win[x])} --> {nid("k2_", win[mid])}')
        out.append(f'  {nid("k1_", win[y])} --> {nid("k2_", win[mid])}')
    for mid in (97, 98, 99, 100):
        x, y = T._TREE[mid]
        out.append(f'  {nid("k2_", win[x])} --> {nid("k3_", win[mid])}')
        out.append(f'  {nid("k2_", win[y])} --> {nid("k3_", win[mid])}')
    for mid in (101, 102):
        x, y = T._TREE[mid]
        out.append(f'  {nid("k3_", win[x])} --> {nid("k4_", win[mid])}')
        out.append(f'  {nid("k3_", win[y])} --> {nid("k4_", win[mid])}')
    out.append(f'  {nid("k4_", win[101])} --> champ')
    out.append(f'  {nid("k4_", win[102])} --> champ')
    out.append("  class champ champ;")
    out.append("```\n")
    out.append(f"**Projected champion: {champ}.** Single most-likely path (favourite "
               "advances); exact probability is tiny — see the title-odds table for the "
               "real distribution.\n")

    # diagnostics for the note
    missed = thirds[8:]
    out.append("**Best-third cut (by P qualify):** in — " +
               ", ".join(f"{t} {q_p[t]*100:.0f}%" for _, t in best) + ".")
    out.append("  Out — " + ", ".join(f"{t} {q_p[t]*100:.0f}%" for _, t in missed) + ".")

    # --- Projected finish by stage (every team mapped to one of the 7 stages) ------
    out.append("\n## Projected finish by team (most-likely bracket)\n")
    out.append("*Each team mapped to the single stage it is eliminated at in the "
               "favourite-advances bracket above. This is one most-likely scenario (every "
               "favourite wins), not a probability — see WORLD_CUP_2026_ELIMINATION.md for the "
               "full per-stage odds.*\n")
    order_stages = ["Winner", "Runner-Up", "Semi-Finals", "Quarter-Finals",
                    "Last 16", "Last 32", "Group Stage"]
    out.append("| Stage | Teams |")
    out.append("|---|---|")
    for st in order_stages:
        teams_at = sorted(t for t, s in stage_of.items() if s == st)
        out.append(f"| **{st}** ({len(teams_at)}) | {', '.join(teams_at)} |")

    with open("docs/WORLD_CUP_2026_LIVE_BRACKET.md", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"\nWrote docs/WORLD_CUP_2026_LIVE_BRACKET.md  (champion={champ}, played={n_played})")


if __name__ == "__main__":
    main()
