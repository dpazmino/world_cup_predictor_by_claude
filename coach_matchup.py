"""Coach-vs-coach tactical matchup analysis (descriptive, NOT predictive).

The 59 team tactical profiles (`team_tactics.py`) and coach prompts
(`coach_prompts.py`) encode *how each side plays* — press height, build-up
directness, width, counter focus, set-piece threat. This scores those profiles on
a few tactical axes (transparent keyword counts) and, for any two teams, surfaces
where their styles **clash** — e.g. a high press against slow build-up, a low
block + counter against a possession side, set-piece threat against an aerially
passive defence.

IMPORTANT — this is a tactical *lens for the narrative simulation*, not a forecast.
The full match sim models all of this and adds ~0 predictive value over scalar team
strength (docs/GAP_ANALYSIS.md), and `player_match/` found unit style-matchups have
no out-of-sample edge. Use this to understand *how* a game might be fought, not *who
wins* — that's `predict.py`.

    python coach_matchup.py France Brazil      # one pairing
    python coach_matchup.py                     # the R32 slate -> docs/COACH_MATCHUPS_R32.md
"""
import os
import sys
from match_engine.team_tactics import TACTICAL_STYLES
from match_engine.coach_prompts import COACH_PROMPTS
from match_engine.ratings import canonical_name

# Each axis: (positive-pole label, negative-pole label, +keywords, -keywords).
AXES = {
    "press": ("high press", "low block", [
        "high press", "press immediately", "press high", "aggressive press",
        "win the ball in the opponent", "flood the ball-side", "press the ball-carrier",
        "press from the front", "high line", "win it back", "counter-press", "press as a unit",
    ], [
        "mid-block", "low block", "mid/low block", "sit", "compact block", "drop deep",
        "deep block", "soak up", "drop deeper", "defend deep", "defensive solidity",
        "hard to beat", "stay compact", "sit in",
    ]),
    "direct": ("direct / vertical", "possession / patient", [
        "direct", "vertical", "over the top", "quick transition", "break quickly",
        "long ball", "early cross", "in behind", "release", "quick vertical", "directly",
    ], [
        "possession", "patient", "short", "build-up", "keep the ball", "sideways",
        "short, sharp", "combinations", "build from the back", "slow",
    ]),
    "width": ("wide / crossing", "central", [
        "wing", "flank", "wide", "cross", "overlap", "byline", "channel", "full-back",
        "deliver into the box", "whipped cross",
    ], [
        "through the middle", "central", "half-space", "cut inside", "narrow",
    ]),
    "counter": ("counter-attacking", "front-foot", [
        "counter", "transition", "break", "spring", "in behind", "explode forward",
        "hurt teams on the counter", "hit them on the break",
    ], []),
    "setpiece": ("set-piece threat", "open-play", [
        "set piece", "set-piece", "inswinging", "aerial", "header", "back post",
        "near post", "corner", "tall", "second runner", "delivery",
    ], []),
}


def _text(team):
    # Score the team's *default* identity (team_tactics), NOT the coach prompt — the
    # latter is full of situational game-management ("when leading, drop deeper") that
    # pollutes the press axis for nearly every side. Coach prompt is used only for names.
    s = TACTICAL_STYLES.get(team, {})
    return " ".join(s.get(k, "") for k in
                    ("philosophy", "shoot_rule", "pass_rule", "press_rule",
                     "width_rule", "set_pieces")).lower()


def score(team):
    """Per-axis net score in roughly [-1, 1] (positive = the positive pole)."""
    txt = _text(team)
    out = {}
    for axis, (_, _, pos, neg) in AXES.items():
        p = sum(txt.count(k) for k in pos)
        n = sum(txt.count(k) for k in neg)
        tot = p + n
        out[axis] = (p - n) / tot if tot else 0.0
    return out


def _pole(axis, v):
    label_pos, label_neg, _, _ = AXES[axis]
    if abs(v) < 0.2:
        return f"balanced {axis}"
    return label_pos if v > 0 else label_neg


def clashes(a, b, sa, sb):
    """Narrative tactical clashes between team a (scores sa) and b (scores sb)."""
    out = []
    # High press vs slow build-up (either direction).
    for x, sx, y, sy in ((a, sa, b, sb), (b, sb, a, sa)):
        if sx["press"] > 0.3 and sy["press"] < -0.2:
            out.append(f"**{x}'s high press vs {y}'s low block** — {x} owns territory and "
                       f"pins {y} back; {y} looks to spring out. Whether {x} can break a "
                       f"packed block is the question.")
        if sx["press"] > 0.3 and sy["direct"] < -0.2:
            out.append(f"**{x}'s high press vs {y}'s patient build-up** — {x} wants to "
                       f"suffocate {y} high up; {y} must play through the press or get punished.")
        if sx["direct"] > 0.3 and sy["press"] > 0.3:
            out.append(f"**{x} plays direct into {y}'s high line** — balls in behind can "
                       f"turn {y}'s aggression into space for {x} to attack.")
        if sx["counter"] > 0.3 and sy["press"] < -0.2:
            out.append(f"**{x}'s counter vs {y}'s low block** — {y} sits and absorbs; "
                       f"{x} needs the transition moment, or it's a patience game.")
        if sx["setpiece"] > 0.4 and sy["setpiece"] < 0.1:
            out.append(f"**{x}'s set-piece threat** could be the separator — a clear "
                       f"dead-ball edge over {y}.")
    # Symmetric texture.
    if sa["press"] > 0.3 and sb["press"] > 0.3:
        out.append("**Two high presses** — expect a chaotic, transition-heavy, stretched game.")
    if sa["press"] < -0.2 and sb["press"] < -0.2:
        out.append("**Two low blocks** — likely cagey and low-scoring; whoever blinks "
                   "first (or wins a set piece) may decide it.")
    if sa["direct"] < -0.3 and sb["direct"] < -0.3:
        out.append("**Two possession sides** — a midfield control battle; the press "
                   "triggers and second balls matter.")
    # De-dup while preserving order.
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c); uniq.append(c)
    return uniq


def coach_name(team):
    """Best-effort coach name from the first line of the prompt ('You are X, ...')."""
    p = COACH_PROMPTS.get(team, "")
    for sent in p.replace("\n", " ").split("."):
        s = sent.strip()
        if s.lower().startswith("you are "):
            name = s[8:].split(",")[0].split(" is ")[0].strip()
            return name
    return "—"


def render(a, b):
    a, b = canonical_name(a), canonical_name(b)
    sa, sb = score(a), score(b)
    lines = [f"### {a} vs {b}",
             f"- **{a}** ({coach_name(a)}): "
             + ", ".join(_pole(ax, sa[ax]) for ax in AXES),
             f"- **{b}** ({coach_name(b)}): "
             + ", ".join(_pole(ax, sb[ax]) for ax in AXES)]
    cl = clashes(a, b, sa, sb)
    if cl:
        lines.append("- **Tactical clash:**")
        lines += [f"  - {c}" for c in cl]
    else:
        lines.append("- **Tactical clash:** styles are broadly compatible — no sharp "
                     "stylistic mismatch; likely decided on quality, not shape.")
    return "\n".join(lines)


# Round of 32 pairings (mirror gen_ko_preds.py).
R32 = [
    ("South Africa", "Canada"), ("Brazil", "Japan"), ("Germany", "Paraguay"),
    ("Netherlands", "Morocco"), ("Ivory Coast", "Norway"), ("France", "Sweden"),
    ("USA", "Bosnia and Herzegovina"), ("Australia", "Egypt"), ("Argentina", "Cape Verde"),
]


def write_r32():
    o = ["# Coach-vs-Coach Tactical Matchups — Round of 32",
         "",
         "*How each pair of coaches' **styles** clash, scored from the tactical profiles in "
         "`team_tactics.py` / `coach_prompts.py` (transparent keyword counts on five axes: "
         "press height, directness, width, counter focus, set-piece threat).*",
         "",
         "> ⚠ **Descriptive, not predictive — tested and confirmed.** This is a tactical lens "
         "for the narrative simulation: it explains *how* a game might be fought, not who wins. "
         "A walk-forward test (`coach_clash.py`) found a tactical-clash signal adds **no "
         "out-of-sample predictive value** (in-sample best weight = 0; both directions hurt) — "
         "the same null as the full sim (`docs/GAP_ANALYSIS.md`) and unit style-matchups "
         "(`player_match/`). Scalar team strength already absorbs it. For who advances, use "
         "`predict.py` / `docs/predictions_r32.md`.",
         ""]
    for a, b in R32:
        o.append(render(a, b))
        o.append("")
    o.append("*Reproduce: `python coach_matchup.py` (or `python coach_matchup.py TeamA TeamB` "
             "for one pairing).*")
    out = os.path.join("docs", "COACH_MATCHUPS_R32.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(o) + "\n")
    print(f"Wrote {out} — {len(R32)} R32 coach matchups")


def main():
    if len(sys.argv) >= 3:
        print(render(sys.argv[1], sys.argv[2]))
    else:
        write_r32()


if __name__ == "__main__":
    main()
