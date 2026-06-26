"""Compare DraftKings 'Stage of Elimination' props (downloads/elim.txt) to the
model's live elimination distribution, and surface +EV edges.

De-vigs each 7-way market, maps stages 1:1 to the model, and flags bets where the
model's exit probability beats the raw (vigged) market price. Teams that have
already played carry stale lines (the model knows results the pre-game price
didn't) — those are marked [stale]; clean edges come from teams yet to kick off.
"""
import datetime
import os
import re
import math
import tournament as T

N = 100000
SEED = 0
ELIM = os.path.join(os.path.expanduser("~"), "Downloads", "elim.txt")

STAGE_MAP = {
    "Group Stage": "Group", "Last 32": "R32", "Last 16": "R16",
    "Quarter-Finals": "QF", "Semi-Finals": "SF", "Runner-Up": "Final",
    "Outright Winner": "Champion",
}
STAGES = ["Group", "R32", "R16", "QF", "SF", "Final", "Champion"]
NAME = {"Czech Republic": "Czechia", "Bosnia": "Bosnia and Herzegovina"}


def amer_to_decimal(a):
    return 1 + a / 100 if a > 0 else 1 + 100 / (-a)


def parse(path):
    teams, cur, pend = {}, None, None
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if line.startswith("World Cup 2026 -") and line.endswith("Props"):
            name = line[len("World Cup 2026 -"):-len("Props")].strip()
            cur = NAME.get(name, name)
            teams[cur] = {}
            pend = None
        elif line in STAGE_MAP:
            pend = STAGE_MAP[line]
        elif pend and re.match(r"^[+\-−]\d+$", line):
            teams[cur][pend] = int(line.replace("−", "-"))
            pend = None
    return teams


def main():
    dk = parse(ELIM)
    played = T._load_played_results(T.GROUPS_2026)
    teams_all = sorted({t for g in T.GROUPS_2026.values() for t in g})
    group_of = {t: g for g, ts in T.GROUPS_2026.items() for t in ts}
    # which teams have already played at least one group game
    has_played = {t for fs in played for t in fs}
    live = T.run(N, seed=SEED, played=played)

    def model_dist(t):
        q = live[t]["qualify"] / N; r16 = live[t]["R16"] / N; qf = live[t]["QF"] / N
        sf = live[t]["SF"] / N; fin = live[t]["final"] / N; win = live[t]["win"] / N
        raw = {"Group": 1 - q, "R32": q - r16, "R16": r16 - qf, "QF": qf - sf,
               "SF": sf - fin, "Final": fin - win, "Champion": win}
        return {k: max(v, 0.0) for k, v in raw.items()}

    edges = []   # (ev, team, stage, model_p, dvg_p, amer, stale, deep)
    qualify = []  # (edge_pp, team, grp, model_q, mkt_q) for clean teams
    for t in sorted(dk):
        if t not in group_of:
            continue
        md = model_dist(t)
        prices = dk[t]
        dvg = {s: 1 / amer_to_decimal(prices[s]) for s in prices}     # vigged implied
        z = sum(dvg.values())
        dvg = {s: p / z for s, p in dvg.items()}                       # de-vigged
        stale = t in has_played
        for s in STAGES:
            if s not in prices:
                continue
            mp = md[s]
            ev = mp * amer_to_decimal(prices[s]) - 1
            deep = s in ("SF", "Final", "Champion")
            edges.append((ev, t, s, mp, dvg[s], prices[s], stale, deep))
        if not stale and "Group" in prices:
            mq = 100 * (1 - md["Group"])
            kq = 100 * (1 - dvg["Group"])
            qualify.append((mq - kq, t, group_of[t], mq, kq))

    clean = sorted([e for e in edges if not e[6] and not e[7] and e[0] > 0.05], reverse=True)
    fade = sorted([e for e in edges if not e[6] and not e[7] and e[0] < -0.20])
    qualify.sort(reverse=True)

    def amer(a):
        return f"+{a}" if a > 0 else str(a)

    today = datetime.date.today().isoformat()
    clean_names = ", ".join(t for _, t, *_ in qualify)
    o = []
    o.append("# Model vs DraftKings — Stage-of-Elimination Props\n")
    o.append(f"*Generated {today}. DraftKings 7-way 'Stage of Elimination' markets "
             "(`downloads/elim.txt`, a fixed pre-tournament snapshot) de-vigged and compared to "
             f"the model's live elimination distribution ({N:,} MC, {len(played)} results "
             "locked). Stages map 1:1 (Last 32→R32, Runner-Up→Final, Outright Winner→Champion). "
             "**Only teams yet to kick off are scored** — once a team plays, its pre-game line "
             "is stale vs a model that knows the result, so it drops out. As the tournament "
             f"runs the clean set shrinks; right now it is **{len(qualify)} teams** "
             f"({clean_names}).*\n")

    if len(qualify) <= 3:
        o.append(f"> ⚠️ **Screen nearly exhausted.** Only {len(qualify)} unplayed team(s) still "
                 f"carry a usable DK line ({clean_names}). The thesis bets this screen surfaced "
                 "— **Ghana** and **DR Congo** — have kicked off (Ghana won 1-0 vs Panama, DR "
                 "Congo drew Portugal 1-1) and are now tracked live in `MODEL_VS_DRAFTKINGS.md`. "
                 "This report adds little until fresh DK lines replace `downloads/elim.txt`.\n")

    o.append("## Headline: the model is more *diffuse* than the market\n")
    o.append("The model pushes probability into the **tails** more than DraftKings does, so it "
             "manufactures nominal '+EV' on longshot exits everywhere — which reflects the model "
             "being **less sharp than a sharp book**, not real value (its known miscalibration: "
             "under-confident on favourites, over-rating minnows; see RESULTS_TRACKER.md). Two "
             "shapes recur across the run:\n")
    o.append("- **Model > market on early exits for favourites** — e.g. while still clean, "
             "Colombia exit-group 28% vs 13%, England exit-R32 31% vs 21%.")
    o.append("- **Model < market on the modal exit for minnows** — e.g. Tunisia group-exit "
             "67% vs 86%.\n")
    o.append("Either way the exact-stage exotics are noise, not edge.\n")

    o.append("## Cleanest signal — model vs market 'to qualify' (reach Last 32+)\n")
    o.append("*Reliable shallow output; +edge = model more bullish on advancing. **A large "
             "+pp on a weak side (e.g. Tunisia) is the model over-rating minnows — a fade, not "
             "a bet.** Genuine edge needs an under-covered but genuinely strong side. Sort sign "
             "alone does not pick a bet.*\n")
    o.append("| Team | Grp | Model qualify | Market (de-vig) | Edge (pp) |")
    o.append("|---|:--:|--:|--:|--:|")
    for e_pp, t, g, mq, kq in qualify:
        o.append(f"| {t} | {g} | {mq:.0f}% | {kq:.0f}% | {e_pp:+.0f} |")

    o.append("\n## Credible edges (CIV thesis — under-covered side advances)\n")
    ref = {"Ghana": ("69%", "~54%"), "DR Congo": ("59%", "~46%")}
    for t in ("Ghana", "DR Congo"):
        mq = 100 * live[t]["qualify"] / N
        pre_m, pre_dk = ref[t]
        if t in has_played:
            o.append(f"- **{t} to qualify** — placed pre-tournament (model {pre_m} vs DK "
                     f"{pre_dk}); has kicked off, now **{mq:.0f}%** model qualify. "
                     "*Realized/live — tracked in `MODEL_VS_DRAFTKINGS.md`.*")
        else:
            o.append(f"- **{t} to qualify** — model **{mq:.0f}%** vs DK {pre_dk}; still "
                     "unplayed, live edge.")
    o.append("\nThe to-qualify market is the clean expression; both are thesis-tests, not locks.\n")

    o.append("## Fades (model miscalibration, not value)\n")
    o.append("- Over-rated minnows **to advance** (e.g. Tunisia, Uzbekistan) — thin/backfilled Elo.")
    o.append("- Favourites **to exit early** — the model's under-confidence, not a real signal.\n")

    o.append("## Full +EV screen (clean teams, Group/R32/R16/QF only)\n")
    o.append("*Listed for completeness — treat as model diffuseness unless it also fits the "
             "under-covered-strong-side read above.*\n")
    o.append("| Team | Exit stage | Model | Market (dv) | Price | EV/$1 |")
    o.append("|---|:--:|--:|--:|---|--:|")
    for ev, t, s, mp, dv, am, stale, deep in clean[:20]:
        o.append(f"| {t} | {s} | {mp*100:.0f}% | {dv*100:.0f}% | {amer(am)} | {ev*100:+.0f}% |")

    o.append("\n## Caveats")
    o.append("- SF/Final/Champion edges excluded (knockout coin-flips + thin squads = noise).")
    o.append("- Teams that have already played are excluded (stale lines).")
    o.append("- 'Credible' edges are the thesis under test (n tiny); the market may simply know "
             "these squads are weaker than their rating.")
    o.append(f"\n*Reproduce: `python compare_elim.py` (reads `downloads/elim.txt`, live n={N}).*")

    with open("docs/MODEL_VS_DK_ELIMINATION.md", "w", encoding="utf-8") as f:
        f.write("\n".join(o) + "\n")
    print(f"Wrote docs/MODEL_VS_DK_ELIMINATION.md  ({len(qualify)} clean teams, "
          f"top qualify edge: {qualify[0][1]} {qualify[0][0]:+.0f}pp)")


if __name__ == "__main__":
    main()
