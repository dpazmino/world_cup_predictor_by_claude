"""Generate predictions for the remaining group-stage fixtures into one markdown file.
Fixtures come from the hard-coded `FIXTURES` list below (no external paste); games already
in data/results.csv are skipped. Pure strength model (shrink 0.25); host nations (USA/Mexico/
Canada) get home advantage in their group games, otherwise neutral venue.
"""
from predict import predict

HOSTS = {"USA", "Mexico", "Canada"}
NAME = {"Bosnia & Herzegovina": "Bosnia and Herzegovina", "Türkiye": "Turkey",
        "Curaçao": "Curacao", "Côte d'Ivoire": "Ivory Coast"}

# (date, group, home_disp, away_disp) — schedule order
FIXTURES = [
    ("Thu Jun 18", "A", "Czechia", "South Africa"),
    ("Thu Jun 18", "B", "Switzerland", "Bosnia & Herzegovina"),
    ("Thu Jun 18", "B", "Canada", "Qatar"),
    ("Thu Jun 18", "A", "Mexico", "South Korea"),
    ("Fri Jun 19", "D", "USA", "Australia"),
    ("Fri Jun 19", "C", "Scotland", "Morocco"),
    ("Fri Jun 19", "C", "Brazil", "Haiti"),
    ("Fri Jun 19", "D", "Türkiye", "Paraguay"),
    ("Sat Jun 20", "F", "Netherlands", "Sweden"),
    ("Sat Jun 20", "E", "Germany", "Ivory Coast"),
    ("Sat Jun 20", "E", "Ecuador", "Curaçao"),
    ("Sun Jun 21", "F", "Tunisia", "Japan"),
    ("Sun Jun 21", "H", "Spain", "Saudi Arabia"),
    ("Sun Jun 21", "G", "Belgium", "Iran"),
    ("Sun Jun 21", "H", "Uruguay", "Cape Verde"),
    ("Sun Jun 21", "G", "New Zealand", "Egypt"),
    ("Mon Jun 22", "J", "Argentina", "Austria"),
    ("Mon Jun 22", "I", "France", "Iraq"),
    ("Mon Jun 22", "I", "Norway", "Senegal"),
    ("Mon Jun 22", "J", "Jordan", "Algeria"),
    ("Tue Jun 23", "K", "Portugal", "Uzbekistan"),
    ("Tue Jun 23", "L", "England", "Ghana"),
    ("Tue Jun 23", "L", "Panama", "Croatia"),
    ("Tue Jun 23", "K", "Colombia", "DR Congo"),
    ("Wed Jun 24", "B", "Switzerland", "Canada"),
    ("Wed Jun 24", "B", "Bosnia & Herzegovina", "Qatar"),
    ("Wed Jun 24", "C", "Scotland", "Brazil"),
    ("Wed Jun 24", "C", "Morocco", "Haiti"),
    ("Wed Jun 24", "A", "Czechia", "Mexico"),
    ("Wed Jun 24", "A", "South Africa", "South Korea"),
    ("Thu Jun 25", "E", "Curaçao", "Ivory Coast"),
    ("Thu Jun 25", "E", "Ecuador", "Germany"),
    ("Thu Jun 25", "F", "Japan", "Sweden"),
    ("Thu Jun 25", "F", "Tunisia", "Netherlands"),
    ("Thu Jun 25", "D", "Türkiye", "USA"),
    ("Thu Jun 25", "D", "Paraguay", "Australia"),
    ("Fri Jun 26", "I", "Norway", "France"),
    ("Fri Jun 26", "I", "Senegal", "Iraq"),
    ("Fri Jun 26", "H", "Cape Verde", "Saudi Arabia"),
    ("Fri Jun 26", "H", "Uruguay", "Spain"),
    ("Fri Jun 26", "G", "Egypt", "Iran"),
    ("Fri Jun 26", "G", "New Zealand", "Belgium"),
    ("Sat Jun 27", "L", "Panama", "England"),
    ("Sat Jun 27", "L", "Croatia", "Ghana"),
    ("Sat Jun 27", "K", "Colombia", "Portugal"),
    ("Sat Jun 27", "K", "DR Congo", "Uzbekistan"),
    ("Sat Jun 27", "J", "Algeria", "Austria"),
    ("Sat Jun 27", "J", "Jordan", "Argentina"),
]


def canon(n):
    return NAME.get(n, n)


def predict_fixture(h_disp, a_disp):
    """Return (ph, pd, pa, xg_h, xg_a, host_note) in *display* order, host gets HFA."""
    h, a = canon(h_disp), canon(a_disp)
    if h in HOSTS and a not in HOSTS:
        r = predict(h, a, neutral=False)
        return r["p_home"], r["p_draw"], r["p_away"], r["xg_home"], r["xg_away"], f"{h_disp} (H)"
    if a in HOSTS and h not in HOSTS:
        r = predict(a, h, neutral=False)            # host as home, then flip to display order
        return r["p_away"], r["p_draw"], r["p_home"], r["xg_away"], r["xg_home"], f"{a_disp} (H)"
    r = predict(h, a, neutral=True)
    return r["p_home"], r["p_draw"], r["p_away"], r["xg_home"], r["xg_away"], None


def verdict(h_disp, a_disp, ph, pd, pa):
    fav, fp = (h_disp, ph) if ph >= pa else (a_disp, pa)
    margin = fp - min(ph, pa)
    if margin < 0.08 or fp < 0.40 or pd >= max(ph, pa):
        return "Coin flip"
    if fp < 0.50:
        return f"Slight lean {fav}"
    if fp < 0.62:
        return f"{fav} favoured"
    return f"{fav} strong"


def played_pairs():
    """Unordered team pairs already in results.csv (since the tournament opener)."""
    import csv
    import os
    from match_engine.ratings import canonical_name
    pairs = set()
    path = os.path.join("data", "results.csv")
    for r in csv.reader(open(path, encoding="utf-8")):
        if not r or r[0].startswith("#") or len(r) < 5 or r[0] < "2026-06-11":
            continue
        pairs.add(frozenset((canonical_name(r[1]), canonical_name(r[2]))))
    return pairs


def main():
    from match_engine.ratings import canonical_name
    done = played_pairs()
    remaining = [(d, g, h, a) for (d, g, h, a) in FIXTURES
                 if frozenset((canonical_name(canon(h)), canonical_name(canon(a)))) not in done]
    skipped = len(FIXTURES) - len(remaining)

    out = ["# Match Predictions — Remaining Group Stage\n",
           "*Strength model (shrink 0.25, DRAW_BASE 0.45). **Host nations (USA/Mexico/Canada) "
           "get home advantage** in their group games — marked **(H)**; all other matches "
           "neutral venue. No DraftKings lines captured, so model-only. W/D/L = home / draw / "
           f"away %. Fixtures already in `data/results.csv` are skipped ({skipped} played so "
           f"far, {len(remaining)} upcoming).*\n"]
    cur = None
    for date, grp, h, a in remaining:
        if date != cur:
            out.append(f"\n## {date}\n")
            out.append("| Grp | Match | W / D / L | xG | Verdict |")
            out.append("|:--:|---|:--:|:--:|---|")
            cur = date
        ph, pd, pa, xh, xa, host = predict_fixture(h, a)
        label = f"{h} vs {a}" + (f"  ·_{host}_" if host else "")
        out.append(f"| {grp} | {label} | {ph*100:.0f} / {pd*100:.0f} / {pa*100:.0f} | "
                   f"{xh:.1f}–{xa:.1f} | {verdict(h, a, ph, pd, pa)} |")
    out.append("\n*Reproduce: `python gen_remaining_preds.py` (fixtures from the hard-coded "
               "`FIXTURES` list; skips games already played). Host advantage per `tournament.py`.*")
    with open("docs/predictions_remaining_group_stage.md", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"Wrote docs/predictions_remaining_group_stage.md "
          f"({len(remaining)} upcoming, {skipped} played skipped)")


if __name__ == "__main__":
    main()
