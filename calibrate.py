"""
Offline calibration harness.

Runs many no-LLM (offline) matches between random team pairings and compares
aggregate output to real World Cup statistical benchmarks. Requires NO API key.

Usage:
    python calibrate.py            # 100 matches
    python calibrate.py -n 300     # 300 matches
    python calibrate.py -n 200 --seed 7
"""
from __future__ import annotations
import sys
import os
import io
import random
import argparse
import contextlib
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from match_engine import MatchSimulation
from match_engine.agents.scheduler_agent import GROUPS_2026

TEAMS = sorted({t for g in GROUPS_2026.values() for t in g})

# Real per-team-per-match benchmarks (approx. modern World Cup / top-tier averages).
BENCHMARKS = {
    "goals":      (1.40, "goals"),
    "shots":      (13.0, "shots"),
    "possession": (50.0, "possession %"),
    "pass_cmp":   (82.0, "pass completion %"),
    "corners":    (5.0,  "corners"),
    "fouls":      (12.0, "fouls"),
    "yellows":    (1.9,  "yellow cards"),
}
REAL_GOALS_PER_MATCH = 2.8


def run(n: int, seed: int):
    random.seed(seed)
    agg: dict[str, list[float]] = defaultdict(list)
    total_goals: list[int] = []
    extra_time = 0
    results = Counter()   # home_win / draw / away_win — exposes home/away bias

    for _ in range(n):
        home, away = random.sample(TEAMS, 2)
        sim = MatchSimulation(home, away, verbose=False, offline=True)
        # Coach setup / substitutions use raw print(); silence them per match.
        with contextlib.redirect_stdout(io.StringIO()):
            res = sim.run()
        score, stats = res["score"], res["stats"]
        total_goals.append(score["home"] + score["away"])
        if res["went_to_extra_time"]:
            extra_time += 1
        if score["home"] > score["away"]:
            results["home_win"] += 1
        elif score["away"] > score["home"]:
            results["away_win"] += 1
        else:
            results["draw"] += 1

        for side in ("home", "away"):
            agg["goals"].append(score[side])
            agg["shots"].append(stats["shots"][side])
            agg["possession"].append(stats["possession"][side])
            agg["corners"].append(stats["corners"][side])
            agg["fouls"].append(stats["fouls"][side])

        # Pass completion per side from the decision log.
        att, cmp_ = Counter(), Counter()
        for e in res["decision_log"]:
            if e["action"] == "pass":
                att[e["team"]] += 1
                if e["outcome"] == "pass":
                    cmp_[e["team"]] += 1
        for side in ("home", "away"):
            if att[side]:
                agg["pass_cmp"].append(100.0 * cmp_[side] / att[side])

        # Yellow cards per side.
        yellows = Counter()
        for c in res["cards"]:
            if c.get("card") == "yellow":
                yellows[c["team"]] += 1
        for side in ("home", "away"):
            agg["yellows"].append(yellows[side])

    return agg, total_goals, extra_time, results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=100, help="number of matches")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    agg, total_goals, extra_time, results = run(args.n, args.seed)
    team_matches = len(agg["goals"])
    avg = lambda k: (sum(agg[k]) / len(agg[k])) if agg[k] else 0.0

    print(f"\nCalibration over {args.n} offline matches ({team_matches} team-matches), seed {args.seed}")
    gpm = sum(total_goals) / len(total_goals)
    gflag = "OK" if abs(gpm - REAL_GOALS_PER_MATCH) / REAL_GOALS_PER_MATCH < 0.25 else "OFF"
    print(f"  goals/match (both teams): {gpm:5.2f}   real ~{REAL_GOALS_PER_MATCH}   [{gflag}]")
    print(f"  matches to extra time:    {extra_time}/{args.n}")
    # Home/away balance: with random pairings this should be ~symmetric (a slight
    # home edge is realistic). A large gap signals a directional bug.
    hw = 100 * results["home_win"] / args.n
    dr = 100 * results["draw"] / args.n
    aw = 100 * results["away_win"] / args.n
    bias = "OK " if abs(hw - aw) < 12 else "OFF"
    print(f"  results: home {hw:.0f}% / draw {dr:.0f}% / away {aw:.0f}%   (home-away gap [{bias}])\n")

    print(f"  {'metric':<20}{'sim':>8}{'real':>8}   status")
    print("  " + "-" * 46)
    for key, (bench, label) in BENCHMARKS.items():
        v = avg(key)
        flag = "OK " if abs(v - bench) / bench < 0.25 else "OFF"
        print(f"  {label:<20}{v:>8.1f}{bench:>8.1f}   [{flag}]")
    print()


if __name__ == "__main__":
    main()
