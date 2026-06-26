"""
Monte-Carlo World Cup forecast — driven by the strength predictor.

Simulates the 2026 format many times: 12 groups of 4 (round-robin) → top 2 of each
group plus the 8 best third-placed teams → 32-team single-elimination knockout.
Every match is driven by predict.py's validated win/draw/loss probabilities.
Outputs each team's probability of winning its group, qualifying, reaching each
knockout round, and winning the tournament (with Monte-Carlo 95% intervals).

Modelling choices:
  * Match OUTCOMES use the validated strength W/D/L. Scoreline MARGINS (group
    goal-difference tiebreaks) come from the Poisson model, conditioned on the outcome.
  * Host nations (USA / Mexico / Canada) get home advantage in their group games;
    other group games and all knockouts are treated as neutral.
  * Knockout ties resolve as a coin-flip shootout: P(advance) = p_win + 0.5·p_draw.
  * The knockout bracket is strength-seeded (an approximation of the official template).
  * Match probabilities are precomputed once (ratings fixed during a forecast).

Usage:
    python tournament.py                 # 10000 sims, full 48-team table
    python tournament.py -n 50000
    python tournament.py --group B       # show only Group B teams
"""
from __future__ import annotations
import sys
import os
import math
import random
import argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from predict import predict, _verdict
from match_engine.agents.scheduler_agent import GROUPS_2026
from match_engine.elo import default_model

KNOCKOUT_ROUNDS = ["R16", "QF", "SF", "final", "win"]
HOSTS = {"USA", "Mexico", "Canada"}


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _norm(lst):
    tot = sum(w for _, w in lst) or 1.0
    return [(s, w / tot) for s, w in lst]


def _entry(a: str, b: str, neutral: bool) -> dict:
    """W/D/L (a's perspective), conditional scoreline samplers, and advance prob."""
    out = predict(a, b, neutral=neutral)
    ph, pdr, pa = out["p_home"], out["p_draw"], out["p_away"]
    la, lb = out["xg_home"], out["xg_away"]
    home, draw, away = [], [], []
    for i in range(7):
        for j in range(7):
            w = _poisson_pmf(i, la) * _poisson_pmf(j, lb)
            (home if i > j else draw if i == j else away).append(((i, j), w))
    return {"wdl": (ph, pdr, pa), "adv": ph + 0.5 * pdr,
            "home": _norm(home), "draw": _norm(draw), "away": _norm(away)}


def precompute(teams: list[str]) -> tuple:
    """Neutral probs for all ordered pairs + host-home probs for host group games."""
    pair = {}
    for a in teams:
        for b in teams:
            if a != b:
                pair[(a, b)] = _entry(a, b, neutral=True)
    host_home = {}
    for h in HOSTS:
        for o in teams:
            if o != h:
                host_home[(h, o)] = _entry(h, o, neutral=False)
    return pair, host_home


def _sample(dist, rng) -> tuple:
    r = rng.random()
    c = 0.0
    for s, w in dist:
        c += w
        if r <= c:
            return s
    return dist[-1][0]


def _play(d, rng) -> tuple:
    """(home_goals, away_goals) from d's perspective."""
    ph, pdr, _ = d["wdl"]
    r = rng.random()
    bucket = "home" if r < ph else "draw" if r < ph + pdr else "away"
    return _sample(d[bucket], rng)


def _group_result(pair, host_home, a, b, rng) -> tuple:
    """(goals_a, goals_b), applying host advantage when exactly one team is a host."""
    if a in HOSTS and b not in HOSTS:
        return _play(host_home[(a, b)], rng)
    if b in HOSTS and a not in HOSTS:
        gb, ga = _play(host_home[(b, a)], rng)   # d is b's perspective
        return ga, gb
    return _play(pair[(a, b)], rng)


def _seed_order(n: int) -> list[int]:
    order = [0, 1]
    while len(order) < n:
        m = len(order) * 2
        order = [x for s in order for x in (s, m - 1 - s)]
    return order


# --- Official 2026 knockout bracket template (group-position based) ---------------
# Third-place slots restrict which groups may supply them (FIFA's allocation table).
_THIRD_SLOTS = {
    "M74": "ABCDF", "M77": "CDFGH", "M79": "CEFHI", "M80": "EHIJK",
    "M81": "BEFIJ", "M82": "AEHIJ", "M85": "EFGIJ", "M87": "DEIJL",
}
# Round of 32: match -> (specA, specB); spec = ("W"|"RU", group) or ("3", slot).
_R32 = {
    73: (("RU", "A"), ("RU", "B")), 74: (("W", "E"), ("3", "M74")), 75: (("W", "F"), ("RU", "C")),
    76: (("W", "C"), ("RU", "F")),  77: (("W", "I"), ("3", "M77")), 78: (("RU", "E"), ("RU", "I")),
    79: (("W", "A"), ("3", "M79")), 80: (("W", "L"), ("3", "M80")), 81: (("W", "D"), ("3", "M81")),
    82: (("W", "G"), ("3", "M82")), 83: (("RU", "K"), ("RU", "L")), 84: (("W", "H"), ("RU", "J")),
    85: (("W", "B"), ("3", "M85")), 86: (("W", "J"), ("RU", "H")),  87: (("W", "K"), ("3", "M87")),
    88: (("RU", "D"), ("RU", "G")),
}
# Later rounds: match -> (feeder_a, feeder_b). 103 (third place) omitted — irrelevant to odds.
_TREE = {89: (74, 77), 90: (73, 75), 91: (76, 78), 92: (79, 80), 93: (83, 84), 94: (81, 82),
         95: (86, 88), 96: (85, 87), 97: (89, 90), 98: (93, 94), 99: (91, 92), 100: (95, 96),
         101: (97, 98), 102: (99, 100), 104: (101, 102)}
_ROUND_OF = {**{m: "R16" for m in range(73, 89)}, **{m: "QF" for m in range(89, 97)},
             **{m: "SF" for m in range(97, 101)}, 101: "final", 102: "final", 104: "win"}


def _assign_thirds(third_group: dict):
    """Match the 8 qualifying thirds (group->team) to slots honouring _THIRD_SLOTS.
    Deterministic backtracking; returns slot->team, or None (caller falls back)."""
    slots = sorted(_THIRD_SLOTS, key=lambda s: sum(g in _THIRD_SLOTS[s] for g in third_group))
    groups = sorted(third_group)
    res, used = {}, set()

    def bt(i):
        if i == len(slots):
            return True
        s = slots[i]
        for g in groups:
            if g not in used and g in _THIRD_SLOTS[s]:
                used.add(g); res[s] = third_group[g]
                if bt(i + 1):
                    return True
                used.discard(g); res.pop(s, None)
        return False

    return res if bt(0) else None


def simulate(pair, host_home, groups, elo, rng, played=None) -> tuple:
    played = played or {}
    standings, gw, gr, thirds_pool = {}, {}, {}, []
    for g, teams in groups.items():
        for t in teams:
            standings[t] = [0, 0, 0]
        for i in range(len(teams)):
            for j in range(i + 1, len(teams)):
                a, b = teams[i], teams[j]
                res = played.get(frozenset((a, b)))    # lock in an actual result if played
                if res is not None:
                    ga, gb = res[a], res[b]
                else:
                    ga, gb = _group_result(pair, host_home, a, b, rng)
                standings[a][1] += ga - gb; standings[a][2] += ga
                standings[b][1] += gb - ga; standings[b][2] += gb
                if ga > gb:
                    standings[a][0] += 3
                elif ga < gb:
                    standings[b][0] += 3
                else:
                    standings[a][0] += 1; standings[b][0] += 1
        ranked = sorted(teams, key=lambda t: (standings[t][0], standings[t][1],
                                              standings[t][2], rng.random()), reverse=True)
        gw[g], gr[g] = ranked[0], ranked[1]
        thirds_pool.append((g, ranked[2], standings[ranked[2]]))

    thirds_pool.sort(key=lambda x: (x[2][0], x[2][1], x[2][2], rng.random()), reverse=True)
    best = thirds_pool[:8]
    winners = list(gw.values())
    qualifiers = winners + list(gr.values()) + [t for _, t, _ in best]
    reached = {t: {"qualify": True} for t in qualifiers}

    slot_team = _assign_thirds({g: t for g, t, _ in best})
    if slot_team is None:                          # safety fallback (rare): strength-seed
        tier = {**{t: 0 for t in winners}, **{t: 1 for t in gr.values()},
                **{t: 2 for _, t, _ in best}}
        cur = [sorted(qualifiers, key=lambda t: (tier[t], -elo.rating(t)))[i]
               for i in _seed_order(len(qualifiers))]
        for name in KNOCKOUT_ROUNDS:
            nxt = []
            for k in range(0, len(cur), 2):
                a, b = cur[k], cur[k + 1]
                w = a if rng.random() < pair[(a, b)]["adv"] else b
                reached.setdefault(w, {})[name] = True; nxt.append(w)
            cur = nxt
        return winners, qualifiers, reached

    def team_of(spec):
        kind, key = spec
        return gw[key] if kind == "W" else gr[key] if kind == "RU" else slot_team[key]

    def play(a, b):
        res = played.get(frozenset((a, b)))            # lock a decisive actual knockout result
        if res is not None and res[a] != res[b]:
            return a if res[a] > res[b] else b
        return a if rng.random() < pair[(a, b)]["adv"] else b

    win = {}
    for mid, (sa, sb) in _R32.items():
        win[mid] = play(team_of(sa), team_of(sb))
        reached.setdefault(win[mid], {})[_ROUND_OF[mid]] = True
    for mid in (89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 104):
        x, y = _TREE[mid]
        win[mid] = play(win[x], win[y])
        reached.setdefault(win[mid], {})[_ROUND_OF[mid]] = True
    return winners, qualifiers, reached


def _load_played_results(groups, since: str = "2026-06-11") -> dict:
    """Load actual tournament results among field teams from data/results.csv, keyed by
    frozenset({home,away}) -> {team: goals}. Used by --live to lock in played games."""
    from match_engine.ratings import canonical_name
    import csv
    field = {t for teams in groups.values() for t in teams}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "results.csv")
    played = {}
    if not os.path.exists(path):
        return played
    for r in csv.reader(open(path, encoding="utf-8")):
        if not r or r[0].startswith("#") or len(r) < 5:
            continue
        if r[0] < since:
            continue
        h, a = canonical_name(r[1]), canonical_name(r[2])
        if h not in field or a not in field:
            continue
        try:
            played[frozenset((h, a))] = {h: int(r[3]), a: int(r[4])}
        except ValueError:
            continue
    return played


def run(n: int, seed: int = 0, played=None) -> dict:
    teams = sorted({t for g in GROUPS_2026.values() for t in g})
    print(f"Precomputing match probabilities (hosts: {', '.join(sorted(HOSTS))})...")
    pair, host_home = precompute(teams)
    elo = default_model()
    rng = random.Random(seed)
    tally = defaultdict(lambda: defaultdict(int))
    if played:
        print(f"LIVE mode: {len(played)} actual result(s) locked in; only the rest are simulated.")
    print(f"Simulating {n} tournaments...")
    for _ in range(n):
        winners, qualifiers, reached = simulate(pair, host_home, GROUPS_2026, elo, rng, played)
        for t in winners:
            tally[t]["group_win"] += 1
        for t in qualifiers:
            tally[t]["qualify"] += 1
        for t, d in reached.items():
            for r in KNOCKOUT_ROUNDS:
                if d.get(r):
                    tally[t][r] += 1
    return tally


def _fixture_verdict(a: str, b: str) -> tuple[str, str]:
    """(display, verdict) for one group fixture, applying host home advantage so the
    verdict matches the conditions the match is actually played under."""
    if a in HOSTS and b not in HOSTS:
        return f"{a} vs {b} (H)", _verdict(predict(a, b, neutral=False))
    if b in HOSTS and a not in HOSTS:
        return f"{b} vs {a} (H)", _verdict(predict(b, a, neutral=False))
    return f"{a} vs {b}", _verdict(predict(a, b, neutral=True))


def _print_group_fixtures(groups: dict, which: list[str]) -> None:
    """Print every group fixture with its rule-based confidence verdict."""
    print("  Group-stage fixtures — confidence verdict  (H = host plays at home)\n")
    for g in which:
        teams = groups[g]
        print(f"  Group {g}:")
        for i in range(len(teams)):
            for j in range(i + 1, len(teams)):
                disp, verd = _fixture_verdict(teams[i], teams[j])
                print(f"    {disp:<40} {verd}")
        print()


def _load_env():
    env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8").read().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, val = line.split("=", 1)
                os.environ.setdefault(k.strip(), val.strip())


def main():
    _load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=10000, help="number of tournament simulations")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--group", help="show only this group's teams (A..L)")
    ap.add_argument("--fixtures", action="store_true",
                    help="show each group fixture's confidence verdict (auto-on with --group)")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the AI verification step")
    ap.add_argument("--live", action="store_true",
                    help="condition on actual results in data/results.csv: lock in played "
                         "games and simulate only the rest (in-tournament forecast)")
    args = ap.parse_args()

    played = _load_played_results(GROUPS_2026) if args.live else None
    tally = run(args.n, args.seed, played)
    n = args.n
    team_group = {t: g for g, teams in GROUPS_2026.items() for t in teams}

    teams = list(tally)
    if args.group:
        teams = [t for t in teams if team_group.get(t) == args.group.upper()]
    teams.sort(key=lambda t: tally[t]["win"], reverse=True)

    pct = lambda t, k: 100 * tally[t][k] / n
    ci = lambda p: 1.96 * math.sqrt(max(p, 1e-9) * (1 - p) / n) * 100   # 95% MC half-width
    print(f"\n  2026 World Cup forecast — {n} simulations (95% MC interval on WIN)\n")
    print(f"  {'team':<15}{'grp':>4}{'GrpW':>7}{'Qual':>7}{'R16':>7}{'QF':>7}"
          f"{'SF':>7}{'Final':>7}{'WIN':>8}{'±95%':>7}")
    print("  " + "-" * 75)
    for t in teams:
        pw = tally[t]["win"] / n
        print(f"  {t:<15}{team_group.get(t,'?'):>4}"
              f"{pct(t,'group_win'):>6.0f}%{pct(t,'qualify'):>6.0f}%{pct(t,'R16'):>6.0f}%"
              f"{pct(t,'QF'):>6.0f}%{pct(t,'SF'):>6.0f}%{pct(t,'final'):>6.0f}%"
              f"{100*pw:>7.1f}%{ci(pw):>6.1f}")
    print()

    # Per-fixture confidence verdicts for the scheduled group matches.
    if args.fixtures or args.group:
        which = [args.group.upper()] if args.group else sorted(GROUPS_2026)
        _print_group_fixtures(GROUPS_2026, which)

    # AI verification step — an LLM audits the whole forecast for problems.
    if not args.no_verify:
        from match_engine.agents.prediction_verifier import verify_tournament
        v = verify_tournament(tally, n)
        verdict = v.get("verdict", "unavailable")
        if verdict in ("skipped", "unavailable"):
            print(f"  AI verification: {v.get('summary', verdict)}\n")
        else:
            label = {"sound": "SOUND", "minor_issues": "MINOR ISSUES",
                     "warning": "WARNING"}.get(verdict, verdict.upper())
            print(f"  --- AI verification: {label} ---")
            print(f"  {v.get('summary', '')}")
            for it in v.get("issues", []):
                sev = str(it.get("severity", "")).upper()
                print(f"   [{sev}] {it.get('issue', '')} — {it.get('detail', '')}")
            print()


if __name__ == "__main__":
    main()
