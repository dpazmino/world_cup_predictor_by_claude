"""Parse DraftKings moneylines (~/Downloads/odds.txt), compare to the model, and
flag favourite-disagreements — the edge candidates for MODEL_VS_DRAFTKINGS.md.

Each odds block has two `<team>-logo` lines (home, away) and three American-odds
lines (home, draw, away). We canonicalise names, run the strength model (host
advantage for USA/Mexico/Canada), de-vig the DK line, and compare favourites.
"""
import os
import re
from predict import predict
from match_engine.odds import implied_probs

ODDS = os.path.join(os.path.expanduser("~"), "Downloads", "odds.txt")
HOSTS = {"USA", "Mexico", "Canada"}
NAME = {"Czech Republic": "Czechia", "Bosnia": "Bosnia and Herzegovina",
        "Turkey": "Turkey", "Curacao": "Curacao", "DR Congo": "DR Congo"}


def canon(n):
    return NAME.get(n.strip(), n.strip())


def am2dec(a):
    a = int(a.replace("−", "-"))
    return 1 + a / 100 if a > 0 else 1 + 100 / (-a)


def parse(path):
    blocks = open(path, encoding="utf-8").read().split("More Bets")
    out = []
    for b in blocks:
        logos = re.findall(r'^(.+)-logo\s*$', b, re.M)
        odds = re.findall(r'^\s*([+−-]\d+)\s*$', b, re.M)
        kick = re.findall(r'^\s*(.*(?:AM|PM|Half).*)\s*$', b, re.M)
        when = kick[-1].strip() if kick else "?"
        if len(logos) >= 2 and len(odds) >= 3:
            out.append((canon(logos[0]), canon(logos[1]), odds[0], odds[1], odds[2], when))
    return out


def model_probs(h, a):
    """(ph, pd, pa) in display order; host gets HFA."""
    if h in HOSTS and a not in HOSTS:
        r = predict(h, a, neutral=False)
        return r["p_home"], r["p_draw"], r["p_away"]
    if a in HOSTS and h not in HOSTS:
        r = predict(a, h, neutral=False)
        return r["p_away"], r["p_draw"], r["p_home"]
    r = predict(h, a, neutral=True)
    return r["p_home"], r["p_draw"], r["p_away"]


def main():
    import datetime
    fixtures = parse(ODDS)
    rows, disagree = [], []
    for h, a, hm, dm, am, when in fixtures:
        ph, pd, pa = model_probs(h, a)
        dh, dd, da = implied_probs(am2dec(hm), am2dec(dm), am2dec(am))
        z = ph + pd + pa
        an = [(0.5 * x + 0.5 * y) for x, y in ((ph, dh), (pd, dd), (pa, da))]
        s = sum(an); an = [x / s for x in an]
        mfav = h if ph >= pa else a
        dfav = h if dh >= da else a
        rows.append((when, h, a, (ph, pd, pa), (dh, dd, da), tuple(an), mfav, dfav))
        if mfav != dfav:
            disagree.append((h, a, ph, pa, dh, da, mfav, dfav))

    today = datetime.date.today().isoformat()
    o = [f"# Model vs DraftKings — 3-way (90-min) moneylines\n",
         f"*Generated {today}. DK 3-way moneylines from `~/Downloads/odds.txt` ({len(rows)} "
         "fixtures), de-vigged and compared to the strength model (host advantage for "
         "USA/Mexico/Canada). `Anchored` = 50/50 blend of model and de-vigged market. "
         "W/D/L = home / draw / away %.*\n",
         "| Kickoff | Match | Model | DK (de-vig) | Anchored | Mdl fav | DK fav |",
         "|---|---|:--:|:--:|:--:|---|---|"]
    for when, h, a, m, d, an, mfav, dfav in rows:
        flag = " ⚠️" if mfav != dfav else ""
        o.append(f"| {when} | {h} v {a} | {m[0]*100:.0f}/{m[1]*100:.0f}/{m[2]*100:.0f} | "
                 f"{d[0]*100:.0f}/{d[1]*100:.0f}/{d[2]*100:.0f} | "
                 f"{an[0]*100:.0f}/{an[1]*100:.0f}/{an[2]*100:.0f} | {mfav} | {dfav}{flag} |")
    o.append(f"\n## Favourite disagreements ({len(disagree)}) — the only moneyline edge candidates\n")
    if disagree:
        for h, a, ph, pa, dh, da, mfav, dfav in disagree:
            o.append(f"- **{h} v {a}** — model backs **{mfav}** ({max(ph,pa)*100:.0f}%), "
                     f"DK backs **{dfav}** ({max(dh,da)*100:.0f}%).")
    else:
        o.append("- none.")
    o.append("\n*Model and DK agree on the favourite in "
             f"{len(rows)-len(disagree)}/{len(rows)} fixtures — the model mirrors the sharp "
             "line, so moneyline edge is scarce. See `MODEL_VS_DRAFTKINGS.md` for the scored "
             "disagreement log.*")

    out = os.path.join("docs", f"MODEL_VS_DK_ODDS_{today}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(o) + "\n")
    print(f"Wrote {out} — {len(rows)} fixtures, {len(disagree)} favourite-disagreement(s)")
    for h, a, ph, pa, dh, da, mfav, dfav in disagree:
        print(f"  DISAGREE: {h} v {a} — model {mfav} {max(ph,pa)*100:.0f}% / DK {dfav} {max(dh,da)*100:.0f}%")


if __name__ == "__main__":
    main()
