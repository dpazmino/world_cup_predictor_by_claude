"""Knockout-round prediction slates (pure strength model, no market).

One generator for every knockout round (R32 → Final). Knockout games can't draw
over the tournament, so the headline metric is P(advance) = P(win) + 0.5*P(draw)
(a level game goes to a coin-flip shootout). Host nations (USA, Mexico, Canada)
get Elo home-field advantage whenever they play; every other tie is neutral.

Fill in each round's fixtures below as the bracket resolves, then run:

    python gen_ko_preds.py r32      # one round  -> docs/predictions_r32.md
    python gen_ko_preds.py          # every round that has fixtures
"""
import os
import sys
from predict import predict
from apply_odds import HIST
from compare_odds import am2dec
from match_engine.ratings import canonical_name

# Host nations get Elo home-field advantage when they play (in any round).
HOSTS = {"USA", "Mexico", "Canada"}

# DraftKings closing decimal odds per fixture (from apply_odds.HIST), for anchoring.
_ODDS = {frozenset((canonical_name(h), canonical_name(a))):
         (canonical_name(h), am2dec(str(amh)), am2dec(str(amd)), am2dec(str(ama)))
         for (h, a), (amh, amd, ama) in HIST.items()}


def odds_for(home, away):
    """DK decimal odds (home, draw, away) in display orientation, or None."""
    e = _ODDS.get(frozenset((canonical_name(home), canonical_name(away))))
    if not e:
        return None
    h0, dh, dd, da = e
    return (dh, dd, da) if canonical_name(home) == h0 else (da, dd, dh)

# Each round: key -> (title, "advance verb", [(home, away, date/time ET), ...]).
# Add fixtures as the bracket fills; empty rounds are skipped.
ROUNDS = {
    "r32": ("Round of 32", "to advance", [
        ("South Africa", "Canada", "Sat Jun 28, 3:00 PM"),
        ("Brazil", "Japan", "Mon Jun 29, 1:00 PM"),
        ("Germany", "Paraguay", "Mon Jun 29, 4:30 PM"),
        ("Netherlands", "Morocco", "Mon Jun 29, 9:00 PM"),
        ("Ivory Coast", "Norway", "Tue Jun 30, 1:00 PM"),
        ("France", "Sweden", "Tue Jun 30, 5:00 PM"),
        ("USA", "Bosnia and Herzegovina", "Wed Jul 1, 8:00 PM"),
        ("Australia", "Egypt", "Fri Jul 3, 2:00 PM"),
        ("Argentina", "Cape Verde", "Fri Jul 3, 6:00 PM"),
    ]),
    "r16": ("Round of 16", "to advance", []),
    "qf": ("Quarter-finals", "to advance", []),
    "sf": ("Semi-finals", "to reach the final", []),
    "final": ("Final", "to lift the trophy", []),
}


def host_side(home, away):
    """Which side gets home advantage: the host, unless both/neither are hosts."""
    h, a = home in HOSTS, away in HOSTS
    if h and not a:
        return home
    if a and not h:
        return away
    return None                                         # neutral (both or neither host)


def oriented(home, away, host, odds=None):
    """W/D/L + xG from the display `home`'s view, applying HFA to the host side and
    (when `odds` given, in display home/draw/away orientation) anchoring 50/50 to the
    de-vigged market."""
    if host == away:                                    # away team plays at home
        swo = (odds[2], odds[1], odds[0]) if odds else None   # re-orient to (away,draw,home)
        o = predict(away, home, neutral=False, odds=swo)
        return (o["p_away"], o["p_draw"], o["p_home"], o["xg_away"], o["xg_home"],
                o["unknown"])
    o = predict(home, away, neutral=(host != home), odds=odds)  # home host -> HFA, else neutral
    return (o["p_home"], o["p_draw"], o["p_away"], o["xg_home"], o["xg_away"],
            o["unknown"])


def ko_verdict(fav, adv):
    if adv < 0.55:
        return "Toss-up"
    if adv < 0.65:
        return f"Slight edge {fav}"
    if adv < 0.75:
        return f"{fav} favoured"
    return f"{fav} strong"


def build_round(key):
    """Render one round to markdown, or return None if it has no fixtures."""
    title, verb, fixtures = ROUNDS[key]
    if not fixtures:
        return None

    rows, details = [], []
    for home, away, when in fixtures:
        host = host_side(home, away)
        odds = odds_for(home, away)
        # Headline = market-anchored when DK odds exist; model-only otherwise.
        ph, pd, pa, xgh, xga, unknown = oriented(home, away, host, odds)
        mph, mpd, mpa = oriented(home, away, host, None)[:3]   # model-only, for contrast
        adv_h, adv_a = ph + 0.5 * pd, pa + 0.5 * pd
        m_adv_h = mph + 0.5 * mpd
        fav, fav_adv = (home, adv_h) if adv_h >= adv_a else (away, adv_a)
        verdict = ko_verdict(fav, fav_adv)
        hosttag = f" *(host {host} at home)*" if host else ""
        flag = "  ⚠ thin/unknown squad" if unknown else (" · *model-only*" if not odds else "")
        wdl = f"{ph*100:.0f} / {pd*100:.0f} / {pa*100:.0f}"
        rows.append(f"| {home} vs {away}{hosttag} | {when} | {wdl} | {xgh:.1f}–{xga:.1f} "
                    f"| **{fav} {fav_adv*100:.0f}%** | {verdict}{flag} |")
        details.append((home, away, when, host, ph, pd, pa, xgh, xga, fav, adv_h, adv_a,
                        unknown, bool(odds), m_adv_h))

    col = "To " + verb[3:]               # "to advance" -> "To advance"
    o = [f"# {title} — Match Predictions",
         "",
         "*Strength model (shrink 0.25) **anchored 50/50 to the de-vigged DraftKings closing "
         "moneyline** where odds are available (rows marked *model-only* have none yet). "
         "Knockout metric is **P(advance) = P(win) + ½·P(draw)** — a level game is treated as a "
         "coin-flip shootout. Host nations (USA, Mexico, Canada) get Elo home-field advantage "
         "when they play; all other ties are neutral. W/D/L = first team's Win / Draw / Loss % "
         "over 90 minutes (post-anchor).*",
         "",
         "## Summary",
         "",
         f"| Match | Date (ET) | W/D/L | xG | {col} | Verdict |",
         "|---|---|---|---|---|---|"]
    o += rows
    o += ["", "## Detail", ""]
    for (home, away, when, host, ph, pd, pa, xgh, xga, fav, adv_h, adv_a,
         unknown, anchored, m_adv_h) in details:
        o.append(f"### {home} vs {away} — {when}"
                 + (f"  *(host {host} at home)*" if host else ""))
        src = "Model+market" if anchored else "Model"
        o.append(f"- {src} **{ph*100:.1f} / {pd*100:.1f} / {pa*100:.1f}** (W/D/L) · "
                 f"xG {xgh:.2f}–{xga:.2f}.")
        adv_line = (f"- **{col}:** {home} {adv_h*100:.0f}% · {away} {adv_a*100:.0f}% "
                    f"→ **{fav}**.")
        if anchored:
            adv_line += (f"  *(model-only had {home} {m_adv_h*100:.0f}%; "
                         f"market {'lifted' if adv_h > m_adv_h else 'trimmed'} it.)*")
        o.append(adv_line)
        if unknown:
            o.append("- ⚠ A squad here has no/thin rating data, so the number is "
                     "low-confidence (≈ base rate), not a firm read.")
        o.append("")
    o.append("*Anchored 50/50 to DraftKings where odds exist (`apply_odds.HIST`). Regenerate: "
             f"`python gen_ko_preds.py {key}`. Log results to `data/results.csv` after "
             "kickoff (knockout: log the score for Elo, track the advancer separately).*")
    return "\n".join(o) + "\n"


def write_round(key):
    md = build_round(key)
    if md is None:
        return None
    out = os.path.join("docs", f"predictions_{key}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    return out


def main():
    args = [a.lower() for a in sys.argv[1:]]
    keys = args or list(ROUNDS)
    wrote = False
    for key in keys:
        if key not in ROUNDS:
            print(f"Unknown round '{key}'. Choices: {', '.join(ROUNDS)}")
            continue
        out = write_round(key)
        if out:
            wrote = True
            n = len(ROUNDS[key][2])
            print(f"Wrote {out} — {n} {key.upper()} fixtures")
        elif args:                       # only warn about explicitly-requested empty rounds
            print(f"Skipped {key.upper()} — no fixtures listed yet")
    if not wrote and not args:
        print("No rounds have fixtures yet. Add them to ROUNDS in gen_ko_preds.py.")


if __name__ == "__main__":
    main()
