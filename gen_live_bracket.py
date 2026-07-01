"""Generate WORLD_CUP_2026_LIVE_BRACKET.md — the deterministic most-likely
group->knockout projection conditioned on played results (--live).

Reuses tournament.py's official bracket templates (_R32/_TREE/_THIRD_SLOTS/
_assign_thirds) and pairwise win probs. Group winner = most-likely (P finish 1st);
2nd/3rd ordered by P(qualify); 8 best thirds = highest-P(qualify) thirds fit to
FIFA's slot table; knockout = favourite (higher P(win)) advances, decisive played
knockout results locked. Prints the full markdown to stdout.
"""
import datetime
import sys
import tournament as T
from gen_ko_preds import ROUNDS as _KO_ROUNDS
from match_engine.ratings import canonical_name

# Knockout shootout advancers — the game is logged as a draw (correct for Elo), so the
# pens winner can't be inferred from results.csv and the coin-flip would otherwise pick the
# model favourite. Force the real advancer through. Keyed by frozenset of the two teams.
SHOOTOUT_ADV = {
    frozenset(("Germany", "Paraguay")): "Paraguay",       # R32: 1-1, Paraguay 4-3 pen
    frozenset(("Netherlands", "Morocco")): "Morocco",     # R32: 1-1, Morocco 3-2 pen
}


def nid(prefix, name):
    return prefix + name.replace(" ", "_").replace("'", "")


def _actual_r32_fixtures():
    """The real Round-of-32 pairings (canonicalised) from gen_ko_preds, or [] if the round
    isn't seeded yet. These are the source of truth for who actually meets whom."""
    fixtures = _KO_ROUNDS.get("r32", (None, None, []))[2]
    return [(canonical_name(h), canonical_name(a)) for h, a, *_ in fixtures]


def _actual_group_order(played, groups):
    """Real per-group ranking [1st,2nd,3rd,4th] from played results (points, then goal
    difference, then goals for — the same key tournament.simulate ranks by). Returns None for
    a group whose six games aren't all in yet, so the caller falls back to the MC projection."""
    order = {}
    for g, teams in groups.items():
        st = {t: [0, 0, 0] for t in teams}                 # [points, goal-diff, goals-for]
        complete = True
        for i in range(len(teams)):
            for j in range(i + 1, len(teams)):
                res = played.get(frozenset((teams[i], teams[j])))
                if res is None:
                    complete = False
                    continue
                a, b = teams[i], teams[j]
                ga, gb = res[a], res[b]
                st[a][1] += ga - gb; st[a][2] += ga
                st[b][1] += gb - ga; st[b][2] += gb
                if ga > gb:
                    st[a][0] += 3
                elif ga < gb:
                    st[b][0] += 3
                else:
                    st[a][0] += 1; st[b][0] += 1
        order[g] = (None if not complete
                    else sorted(teams, key=lambda t: tuple(st[t]), reverse=True))
    return order


def main(n=60000, seed=0):
    played = T._load_played_results(T.GROUPS_2026)
    teams = sorted({t for g in T.GROUPS_2026.values() for t in g})
    pair, host_home = T.precompute(teams)
    elo = T.default_model()

    # --- Monte-Carlo tallies for group_win / qualify (drives the projection) ---
    tally = T.run(n, seed=seed, played=played)
    gw_p = {t: tally[t]["group_win"] / n for t in teams}
    q_p = {t: tally[t]["qualify"] / n for t in teams}

    # --- Group standings: use the ACTUAL final table where the group is complete, else the
    # MC projection (winner by P(1st); 2nd/3rd/4th by P(qualify)). The MC ordering is unreliable
    # for a finished group — once several teams all qualify with P=1 the 2nd/3rd split is
    # arbitrary, which then mis-identifies runners-up vs thirds and corrupts the R32 seating. ---
    real_order = _actual_group_order(played, T.GROUPS_2026)
    order = {}            # group -> [1st,2nd,3rd,4th]
    for g, ts in T.GROUPS_2026.items():
        if real_order[g] is not None:
            order[g] = real_order[g]
        else:
            winner = max(ts, key=lambda t: gw_p[t])
            rest = sorted([t for t in ts if t != winner], key=lambda t: q_p[t], reverse=True)
            order[g] = [winner] + rest

    # --- Group winners / runners-up (deterministic once the groups are complete) ---
    gwn = {g: order[g][0] for g in order}
    grn = {g: order[g][1] for g in order}

    # --- 8 best thirds by P(qualify), fit to FIFA slot table (projection fallback only) ---
    thirds = sorted(((g, order[g][2]) for g in order), key=lambda x: q_p[x[1]], reverse=True)
    best = thirds[:8]
    slot_team = T._assign_thirds({g: t for g, t in best})

    # --- R32 seating: prefer the ACTUAL bracket fixtures over the re-derived template ------
    # The deterministic third->slot assignment can seat a third-placed team in a valid but
    # DIFFERENT FIFA slot than reality, mis-pairing its R32 tie — and then a locked/upset
    # result (e.g. a shootout) lands on the wrong side of the bracket. When the real R32
    # fixtures are known we seat straight from them: each tie's W/RU anchor is unambiguous and
    # the third is simply its actual opponent, so pairings, locked results and shootouts all
    # match reality. Pre-bracket (no fixtures yet) we fall back to the projected slot table.
    actual = _actual_r32_fixtures()
    partner = {}
    for a, b in actual:
        partner[a], partner[b] = b, a

    def anchor(spec):
        kind, key = spec
        return gwn[key] if kind == "W" else grn[key] if kind == "RU" else None

    def r32_pair(mid):
        sa, sb = T._R32[mid]
        ta, tb = anchor(sa), anchor(sb)
        if partner:                                   # seat the third from the real fixture
            ta = ta if ta is not None else partner.get(tb)
            tb = tb if tb is not None else partner.get(ta)
        elif slot_team is not None:                   # pre-bracket: re-derived FIFA template
            ta = ta if ta is not None else slot_team[sa[1]]
            tb = tb if tb is not None else slot_team[sb[1]]
        return ta, tb

    # Validate the actual-fixture seating (name mismatch / stale gwn would corrupt it); on any
    # inconsistency drop back to the template path rather than emit a broken bracket.
    parts = [t for mid in T._R32 for t in r32_pair(mid)]
    if partner and (None in parts or len(set(parts)) != 32):
        print("WARNING: actual R32 fixtures inconsistent with projected standings — "
              "falling back to the re-derived FIFA slot table.", file=sys.stderr)
        partner = {}
        parts = [t for mid in T._R32 for t in r32_pair(mid)]
    if None in parts or len(set(parts)) != 32:
        sys.exit("R32 seating failed — could not seat 32 distinct teams")
    qualifiers = set(parts)

    # --- Deterministic knockout: favourite advances, lock decisive played games ---
    def play(a, b):
        forced = SHOOTOUT_ADV.get(frozenset((a, b)))
        if forced is not None:
            return forced
        res = played.get(frozenset((a, b)))
        if res is not None and res[a] != res[b]:
            return a if res[a] > res[b] else b
        return a if pair[(a, b)]["adv"] >= 0.5 else b

    win = {}
    for mid in T._R32:
        win[mid] = play(*r32_pair(mid))
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

    for mid in T._R32:                                         # 16 matches → Last 32 exits
        stage_of[loser(mid, *r32_pair(mid))] = "Last 32"
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
    today = datetime.date.today().isoformat()
    seat = ("real R32 fixtures (played ties locked to their actual result)" if partner
            else "the projected FIFA slot table")
    out.append("# 2026 FIFA World Cup — Live Group-to-Bracket Projection\n")
    out.append(f"*Generated {today}. Conditioned on the {n_played} results played so far "
               "(`--live`). Group winner = most-likely group winner (P finish 1st); 2nd/3rd "
               f"ordered by P(qualify). Knockout ties are seated from {seat}; the official "
               "2026 bracket then advances the favourite for unplayed games. ✓ = projected to "
               "qualify.*\n")
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
    for mid in T._R32:
        a, b = r32_pair(mid)
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
