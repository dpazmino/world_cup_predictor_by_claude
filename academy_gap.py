"""Academy → Pro gap analysis + development plan.

Ingests an academy player's assessment, maps the 1-10 sub-skills onto the FIFA 6
attributes used in player_stats.csv (pace/shooting/passing/dribbling/defending/
physical), benchmarks against position peers, and emits a prioritised plan.

Two layers of data are treated differently:
  * Technical / Tactical / Physical  -> map to FIFA -> THIS is the skill gap.
  * Mental / Nutrition               -> do NOT map -> the ENABLERS/throttles that
                                        gate how fast a skill gap can close.

Scale bridge (TUNE against known players): academy 1-10 -> FIFA 0-99 is linear,
FIFA = INTERCEPT + SLOPE * score. Defaults put a 3/10 near the low-pro floor.
"""
import csv
import os

INTERCEPT, SLOPE = 40.0, 5.5          # 1/10->45 · 3/10->56 · 7/10->78 · 10/10->95
CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_stats.csv")

# Which academy sub-skills roll up into each FIFA attribute (None entries = "not
# yet assessed" and are skipped, lowering confidence for that attribute).
FIFA_MAP = {
    "pace":      ["speed", "acceleration"],
    "shooting":  ["finishing", "shot_power", "composure"],
    "passing":   ["short_passing", "long_passing", "scanning", "decision_making"],
    "dribbling": ["dribbling", "first_touch", "mobility", "weak_foot"],
    "defending": ["positioning", "anticipation", "heading", "tackling", "marking"],
    "physical":  ["stamina", "recovery", "strength", "jumping"],
}

# Position importance weights (sum≈1) — prioritises which gaps matter for the role.
POSITION_WEIGHTS = {
    "CB":  {"defending": .30, "physical": .25, "pace": .15, "passing": .15, "dribbling": .07, "shooting": .08},
    "FB":  {"pace": .22, "defending": .22, "physical": .18, "passing": .18, "dribbling": .12, "shooting": .08},
    "CDM": {"defending": .26, "passing": .24, "physical": .20, "dribbling": .12, "pace": .10, "shooting": .08},
    "CM":  {"passing": .26, "dribbling": .18, "physical": .16, "defending": .16, "pace": .12, "shooting": .12},
    "ST":  {"shooting": .30, "pace": .22, "dribbling": .18, "physical": .15, "passing": .10, "defending": .05},
    "_default": {k: 1 / 6 for k in FIFA_MAP},
}

PLAN = {
    "defending": "1v1 defending & shadow-marking drills, line-holding and cover/balance shape, "
                 "match-footage reading sessions. (Add tackling + marking to the assessment.)",
    "physical":  "Strength & conditioning block (gym, core, aerial/jumping); progressive overload. "
                 "(Add strength + jumping measurables.)",
    "pace":      "Sprint mechanics & acceleration work — but see the age note; pace has a hard ceiling.",
    "passing":   "Short+long passing under pressure, switch-of-play reps, scanning-before-receiving drills.",
    "dribbling": "First-touch & close-control circuits, weak-foot reps, receiving on the half-turn.",
    "shooting":  "Finishing & shot-power reps from the player's realistic zones.",
}


def avg(player, keys):
    vals = []
    for k in keys:
        v = (player["technical"].get(k) if k in player["technical"] else
             player["tactical"].get(k) if k in player["tactical"] else
             player["physical"].get(k) if k in player["physical"] else
             player.get(k) if k in ("weak_foot",) else
             player["extra"].get(k))
        if v is not None:
            vals.append(v)
    return (sum(vals) / len(vals), len(vals), len(keys)) if vals else (None, 0, len(keys))


def to_fifa(player):
    out = {}
    for attr, keys in FIFA_MAP.items():
        score, got, total = avg(player, keys)
        out[attr] = {
            "fifa": round(INTERCEPT + SLOPE * score) if score is not None else None,
            "coverage": f"{got}/{total}",
            "missing": [k for k in keys if _val(player, k) is None],
        }
    return out


def _val(player, k):
    return (player["technical"].get(k) if k in player["technical"] else
            player["tactical"].get(k) if k in player["tactical"] else
            player["physical"].get(k) if k in player["physical"] else
            player.get(k) if k == "weak_foot" else player["extra"].get(k))


def position_benchmark(position, tier_lo=0.0, tier_hi=0.34):
    """Mean FIFA attributes of position peers in a rating band (default: bottom
    third = the realistic 'break into pro' target). Returns (bench dict, n, ovr)."""
    rows = []
    with open(CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["position"] == position:
                rows.append({k: int(r[k]) for k in
                             ("overall", "pace", "shooting", "passing", "dribbling", "defending", "physical")})
    if not rows:
        return None, 0, None
    rows.sort(key=lambda x: x["overall"])
    lo, hi = int(len(rows) * tier_lo), max(1, int(len(rows) * tier_hi))
    band = rows[lo:hi] or rows[:1]
    attrs = ("pace", "shooting", "passing", "dribbling", "defending", "physical")
    bench = {a: sum(x[a] for x in band) / len(band) for a in attrs}
    return bench, len(band), sum(x["overall"] for x in band) / len(band)


def enabler_flags(player):
    flags = []
    m = player["mental"]
    for k in ("resilience", "discipline", "focus", "coachability", "confidence"):
        if m.get(k, 10) <= 4:
            flags.append(f"Mental · {k} = {m[k]}/10 — low; throttles skill transfer.")
    n = player["nutrition"]
    if str(n.get("protein", "")).lower() != "always":
        flags.append(f"Nutrition · protein '{n.get('protein')}' — inconsistent; caps muscle adaptation.")
    if n.get("sleep_hrs", 9) < 8:
        flags.append(f"Nutrition · sleep {n.get('sleep_hrs')}h — under 8h limits recovery & learning.")
    if "1-2" in str(n.get("water", "")) or "1–2" in str(n.get("water", "")):
        flags.append(f"Nutrition · water {n.get('water')} — low hydration hurts training quality.")
    if str(n.get("eats_before_training", "")).lower() != "always":
        flags.append("Nutrition · not always fuelling before training — reduces session output.")
    return flags


def analyse(player, name="Academy player"):
    pos = player["position"]
    weights = POSITION_WEIGHTS.get(pos, POSITION_WEIGHTS["_default"])
    fifa = to_fifa(player)
    bench, n, bovr = position_benchmark(pos)

    out = [f"# Academy Gap Analysis — {name} ({pos}, age {player['age']})\n",
           f"*Benchmark: bottom-third {pos}s in player_stats.csv (n={n}, "
           f"avg overall {bovr:.0f}) — the realistic 'break into pro' bar. Academy 1-10 "
           f"mapped to FIFA via {INTERCEPT:.0f}+{SLOPE:.1f}×score (tune against known players).*\n"]

    out.append("## Skill gaps vs FIFA 6 (position-weighted)\n")
    out.append("| Attribute | You (est.) | Target | Gap | Weight | Priority | Coverage |")
    out.append("|---|--:|--:|--:|--:|--:|:--:|")
    gaps = []
    for a in ("defending", "physical", "pace", "passing", "dribbling", "shooting"):
        you = fifa[a]["fifa"]
        tgt = round(bench[a])
        if you is None:
            out.append(f"| {a} | — | {tgt} | — | {weights.get(a,0):.2f} | — | "
                       f"{fifa[a]['coverage']} ⚠ |")
            continue
        gap = tgt - you
        pri = max(gap, 0) * weights.get(a, 0)
        gaps.append((pri, a, you, tgt, gap))
        cov = fifa[a]["coverage"] + (" ⚠" if fifa[a]["missing"] else "")
        out.append(f"| {a} | {you} | {tgt} | {gap:+d} | {weights.get(a,0):.2f} | "
                   f"{pri:.1f} | {cov} |")

    missing = sorted({m for a in fifa.values() for m in a["missing"]})
    if missing:
        out.append(f"\n**⚠ Not yet assessed (collect these):** {', '.join(missing)}.")

    out.append("\n## Prioritised development plan\n")
    out.append("*Ranked by gap × position weight. Close the biggest weighted gaps first.*\n")
    for i, (pri, a, you, tgt, gap) in enumerate(sorted(gaps, reverse=True), 1):
        if gap <= 0:
            out.append(f"{i}. **{a}** — at/above target ({you} vs {tgt}); maintain.")
        else:
            out.append(f"{i}. **{a}** (gap {gap:+d}, priority {pri:.1f}) — {PLAN[a]}")

    out.append("\n## Enablers to fix first (these gate everything above)\n")
    fl = enabler_flags(player)
    out += [f"- {x}" for x in fl] or ["- No major mental/nutrition red flags."]

    if player["age"] >= 25:
        out.append(f"\n## Age note\nAt {player['age']} the physical ceiling (pace/strength) is "
                   "largely set — weight gains toward **tactical positioning, defending, "
                   "passing and mental**, which keep improving, and treat pace as a "
                   "'compensate, don't chase' area.")

    return "\n".join(out) + "\n"


# ── Demo profile: the pasted Greek CB ────────────────────────────────────────
SAMPLE = {
    "position": "CB", "age": 27, "foot": "Right", "weak_foot": 2, "level": "Academy",
    "card": {"overall": 61, "technical": 58, "tactical": 60, "physical": 60,
             "mental": 66, "nutrition": 60},
    "technical": {"first_touch": 3, "short_passing": 3, "long_passing": 3,
                  "dribbling": 3, "finishing": 3, "heading": 2},
    "tactical": {"positioning": 3, "scanning": 3, "decision_making": 3,
                 "anticipation": 3, "composure": 3},
    "physical": {"speed": 3, "acceleration": 3, "stamina": 3, "mobility": 3, "recovery": 3},
    "mental": {"confidence": 6, "motivation": 7, "resilience": 3, "discipline": 3,
               "focus": 3, "coachability": 4},
    "nutrition": {"meals_per_day": 3, "protein": "sometimes", "water": "1-2L",
                  "sleep_hrs": 6.5, "eats_before_training": "sometimes"},
    "extra": {"tackling": None, "marking": None, "strength": None, "jumping": None,
              "shot_power": None, "aggression": None},
}


def main():
    report = analyse(SAMPLE, name="Sample CB (Greece)")
    out = os.path.join("docs", "academy_gap_sample_cb.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Wrote {out} ({len(report.splitlines())} lines)")


if __name__ == "__main__":
    main()
