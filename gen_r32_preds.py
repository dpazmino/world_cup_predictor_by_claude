"""Round-of-32 prediction slate (pure strength model, no market).

Knockout games can't draw over the tournament, so the headline metric is
P(advance) = P(win) + 0.5 * P(draw) (a level game goes to a coin-flip shootout).
Host nations (USA, Canada here) are given Elo home-field advantage; every other
tie is neutral. Reads nothing external — the fixtures + venues are inlined below.

Run: python gen_r32_preds.py  ->  writes docs/predictions_r32.md
"""
import os
from predict import predict

# Host nations get Elo home-field advantage when they play (in any round).
HOSTS = {"USA", "Mexico", "Canada"}

# (home, away, date/time ET)
FIXTURES = [
    ("South Africa", "Canada", "Sat Jun 28, 3:00 PM"),
    ("Brazil", "Japan", "Mon Jun 29, 1:00 PM"),
    ("Germany", "Paraguay", "Mon Jun 29, 4:30 PM"),
    ("Netherlands", "Morocco", "Mon Jun 29, 9:00 PM"),
    ("Ivory Coast", "Norway", "Tue Jun 30, 1:00 PM"),
    ("France", "Sweden", "Tue Jun 30, 5:00 PM"),
    ("USA", "Bosnia and Herzegovina", "Wed Jul 1, 8:00 PM"),
    ("Australia", "Egypt", "Fri Jul 3, 2:00 PM"),
    ("Argentina", "Cape Verde", "Fri Jul 3, 6:00 PM"),
]


def host_side(home, away):
    """Which side gets home advantage: the host, unless both/neither are hosts."""
    h, a = home in HOSTS, away in HOSTS
    if h and not a:
        return home
    if a and not h:
        return away
    return None                                         # neutral (both or neither host)


def oriented(home, away, host):
    """W/D/L + xG from the display `home`'s view, applying HFA to the host side."""
    if host == away:                                    # away team plays at home
        o = predict(away, home, neutral=False)
        return (o["p_away"], o["p_draw"], o["p_home"], o["xg_away"], o["xg_home"],
                o["unknown"])
    o = predict(home, away, neutral=(host != home))     # home host -> HFA, else neutral
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


def main():
    rows, details = [], []
    for home, away, when in FIXTURES:
        host = host_side(home, away)
        ph, pd, pa, xgh, xga, unknown = oriented(home, away, host)
        adv_h, adv_a = ph + 0.5 * pd, pa + 0.5 * pd
        fav, fav_adv = (home, adv_h) if adv_h >= adv_a else (away, adv_a)
        verdict = ko_verdict(fav, fav_adv)
        hosttag = f" *(host {host} at home)*" if host else ""
        flag = "  ⚠ thin/unknown squad" if unknown else ""
        wdl = f"{ph*100:.0f} / {pd*100:.0f} / {pa*100:.0f}"
        rows.append(f"| {home} vs {away}{hosttag} | {when} | {wdl} | {xgh:.1f}–{xga:.1f} "
                    f"| **{fav} {fav_adv*100:.0f}%** | {verdict}{flag} |")
        details.append((home, away, when, host, ph, pd, pa, xgh, xga, fav, fav_adv, unknown))

    o = ["# Round of 32 — Match Predictions",
         "",
         "*Pure strength model (shrink 0.25), **no market anchoring**. Knockout metric is "
         "**P(advance) = P(win) + ½·P(draw)** — a level game is treated as a coin-flip shootout. "
         "Host nations (USA, Mexico, Canada) are given Elo home-field advantage when they play; "
         "all other ties are neutral. W/D/L = first team's Win / Draw / Loss % over 90 minutes.*",
         "",
         "## Summary",
         "",
         "| Match | Date (ET) | Model (W/D/L) | xG | To advance | Verdict |",
         "|---|---|---|---|---|---|"]
    o += rows
    o += ["", "## Detail", ""]
    for (home, away, when, host, ph, pd, pa, xgh, xga, fav, fav_adv, unknown) in details:
        adv_h, adv_a = ph + 0.5 * pd, pa + 0.5 * pd
        o.append(f"### {home} vs {away} — {when}"
                 + (f"  *(host {host} at home)*" if host else ""))
        o.append(f"- Model **{ph*100:.1f} / {pd*100:.1f} / {pa*100:.1f}** (W/D/L) · "
                 f"xG {xgh:.2f}–{xga:.2f}.")
        o.append(f"- **To advance:** {home} {adv_h*100:.0f}% · {away} {adv_a*100:.0f}% "
                 f"→ **{fav}**.")
        if unknown:
            o.append("- ⚠ A squad here has no/thin rating data, so the number is "
                     "low-confidence (≈ base rate), not a firm read.")
        o.append("")
    o.append("*Pure-model slate (no DraftKings anchoring). Regenerate: "
             "`python gen_r32_preds.py`. Log results to `data/results.csv` after kickoff "
             "(knockout: log the score for Elo, track the advancer separately).*")

    out = os.path.join("docs", "predictions_r32.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(o) + "\n")
    print(f"Wrote {out} — {len(FIXTURES)} R32 fixtures")


if __name__ == "__main__":
    main()
