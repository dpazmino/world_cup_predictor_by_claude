"""Merge DraftKings closing moneylines into data/results.csv columns 7-9
(decimal home, draw, away) for played 2026 WC games.

Sources: HIST (American lines the user provided for early games) + ~/Downloads/
odds.txt (the rolling slate). Matches by team pair, fixes home/away orientation
to the row's order, skips rows that already carry odds, leaves history untouched.
Re-run after logging results / refreshing odds.txt to fill newly-played games.
"""
import os
from compare_odds import parse, am2dec, ODDS
from match_engine.ratings import canonical_name

# Early-round DK moneylines (American), keyed by (home, away) as originally listed.
HIST = {
    ("France", "Senegal"): (-215, 360, 600),
    ("Iraq", "Norway"): (1300, 600, -475),
    ("Argentina", "Algeria"): (-225, 350, 650),
    ("Spain", "Cape Verde"): (-1200, 1100, 2500),
    ("Saudi Arabia", "Uruguay"): (650, 340, -215),
    ("Belgium", "Egypt"): (-170, 295, 475),
    ("Iran", "New Zealand"): (-105, 240, 330),
    ("Sweden", "Tunisia"): (-105, 245, 310),
    # Round of 32 (DK closing moneylines, home/draw/away). Auto-fill once played.
    ("South Africa", "Canada"): (425, 255, -135),
    ("Brazil", "Japan"): (-140, 285, 400),
    ("Germany", "Paraguay"): (-265, 400, 750),
    ("Netherlands", "Morocco"): (110, 225, 280),
    ("Ivory Coast", "Norway"): (265, 250, 105),
    ("France", "Sweden"): (-330, 475, 900),
    ("USA", "Bosnia and Herzegovina"): (-255, 390, 750),
    ("Australia", "Egypt"): (225, 200, 150),
    ("Argentina", "Cape Verde"): (-650, 700, 1800),
}


def build_odds():
    """frozenset(canon home, canon away) -> (home_team, dec_h, dec_d, dec_a)."""
    out = {}
    for (h, a), (hm, dm, am) in HIST.items():
        ch, ca = canonical_name(h), canonical_name(a)
        out[frozenset((ch, ca))] = (ch, am2dec(str(hm)), am2dec(str(dm)), am2dec(str(am)))
    for h, a, hm, dm, am, _ in parse(ODDS):
        ch, ca = canonical_name(h), canonical_name(a)
        out.setdefault(frozenset((ch, ca)),
                       (ch, am2dec(hm), am2dec(dm), am2dec(am)))
    return out


def main():
    odds = build_odds()
    path = os.path.join("data", "results.csv")
    lines = open(path, encoding="utf-8").read().splitlines()
    out, filled = [], 0
    for ln in lines:
        p = ln.split(",")
        if not ln or ln.startswith("#") or len(p) < 6 or p[0] < "2026-06-11" \
                or (len(p) >= 9 and p[6].strip()):
            out.append(ln)
            continue
        rh, ra = canonical_name(p[1]), canonical_name(p[2])
        ent = odds.get(frozenset((rh, ra)))
        if not ent:
            out.append(ln)
            continue
        home_team, dh, dd, da = ent
        oh, od, oa = (dh, dd, da) if rh == home_team else (da, dd, dh)  # orient to row
        out.append(",".join(p[:6] + [f"{oh:.2f}", f"{od:.2f}", f"{oa:.2f}"]))
        filled += 1
    open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(f"Filled DK closing odds for {filled} games "
          f"({len(odds)} odds entries available).")


if __name__ == "__main__":
    main()
