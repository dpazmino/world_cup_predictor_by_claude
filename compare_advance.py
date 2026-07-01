"""Compare DraftKings 2-way "To Qualify" (to-advance) knockout odds to the model's
P(advance), and flag the edge candidates — disagreements on who advances.

Knockouts can't draw over the tie, so the relevant market is the **2-way advance**
price, not the 3-way 90-min moneyline (`compare_odds.py`). The model's knockout metric
is P(advance) = P(win) + ½·P(draw) (a level game is a coin-flip shootout), with host
nations (USA/Mexico/Canada) getting Elo home-field advantage — identical to
`gen_ko_preds.py`, whose `host_side`/`oriented` helpers are reused here so the two stay
consistent. The model is left UN-anchored (pure model vs market) so any edge is real.

Input: a raw DK "To Qualify" paste in ~/Downloads/advance.txt — blocks separated by
`More Bets`, each with two `<team>-logo` lines and two American odds (home, away).

    python compare_advance.py        # -> docs/MODEL_VS_DK_R32_ADVANCE.md

Re-run per round: replace advance.txt with the next round's qualify market.
"""
import datetime
import os
import re

from gen_ko_preds import host_side, oriented
from match_engine.ratings import canonical_name

ADV = os.path.join(os.path.expanduser("~"), "Downloads", "advance.txt")
OUT = os.path.join("docs", "MODEL_VS_DK_R32_ADVANCE.md")
NAME = {"Bosnia": "Bosnia and Herzegovina", "Czech Republic": "Czechia"}


def canon(n):
    return canonical_name(NAME.get(n.strip(), n.strip()))


def am2dec(a):
    a = int(a.replace("−", "-"))
    return 1 + a / 100 if a > 0 else 1 + 100 / (-a)


def parse(path):
    """[(home, away, american_home, american_away, kickoff), ...] from the DK paste."""
    out = []
    for b in open(path, encoding="utf-8").read().split("More Bets"):
        logos = re.findall(r"^(.+)-logo\s*$", b, re.M)
        odds = re.findall(r"^\s*([+−-]\d+)\s*$", b, re.M)
        kick = re.findall(r"^\s*(.*(?:AM|PM)\s*)$", b, re.M)
        when = kick[-1].strip() if kick else "?"
        if len(logos) >= 2 and len(odds) >= 2:
            out.append((canon(logos[0]), canon(logos[1]), odds[0], odds[1], when))
    return out


def devig_2way(dec_h, dec_a):
    """De-vig a 2-way market into a proper (P_home_adv, P_away_adv)."""
    ih, ia = 1.0 / dec_h, 1.0 / dec_a
    s = ih + ia
    return ih / s, ia / s


def main():
    fixtures = parse(ADV)
    if not fixtures:
        print(f"No fixtures parsed from {ADV}"); return

    rows, edges, diffuse = [], [], []
    for home, away, amh, ama, when in fixtures:
        host = host_side(home, away)
        ph, pd, pa = oriented(home, away, host, None)[:3]      # model-only W/D/L
        m_adv_h, m_adv_a = ph + 0.5 * pd, pa + 0.5 * pd

        dec_h, dec_a = am2dec(amh), am2dec(ama)
        d_adv_h, d_adv_a = devig_2way(dec_h, dec_a)            # de-vigged market

        m_fav = home if m_adv_h >= m_adv_a else away
        d_fav = home if d_adv_h >= d_adv_a else away
        # EV of backing each side at its RAW DK price (incl. vig), using model prob.
        ev_h = m_adv_h * dec_h - 1.0
        ev_a = m_adv_a * dec_a - 1.0
        best_side, best_ev, best_price = (
            (home, ev_h, amh) if ev_h >= ev_a else (away, ev_a, ama))

        hosttag = f" (host {host})" if host else ""
        disagree = m_fav != d_fav
        # The +EV side is almost always the underdog — the model is more diffuse than the
        # sharp market, so it over-prices longshots' advance equity. That is calibration
        # error, NOT edge; only a favourite disagreement is a genuine signal.
        underdog_ev = best_ev > 0.02 and best_side != d_fav
        flag = "  **⚠ disagree**" if disagree else ""
        rows.append(
            f"| {home} vs {away}{hosttag} | {when} | "
            f"{m_adv_h*100:.0f}/{m_adv_a*100:.0f} | {d_adv_h*100:.0f}/{d_adv_a*100:.0f} | "
            f"{m_fav} / {d_fav} |{flag} |")
        if disagree:
            edges.append((home, away, m_adv_h, m_adv_a, d_adv_h, d_adv_a,
                          best_side, best_ev, best_price))
        elif underdog_ev:
            diffuse.append((home, away, m_adv_h, m_adv_a, d_adv_h, d_adv_a,
                            best_side, best_ev, best_price))

    today = datetime.date.today().isoformat()
    o = [f"# Model vs DraftKings — R32 To-Qualify (2-way advance) · {today}", "",
         "*Pure strength model (shrink 0.25, **un-anchored**) P(advance) = P(win)+½·P(draw) "
         "vs the de-vigged DraftKings 2-way \"To Qualify\" market. Host nations "
         "(USA/Mexico/Canada) get Elo home advantage. EV = model P(advance) × raw DK decimal "
         "price − 1 (the price still carries vig, so +EV must clear the margin). Edge can only "
         "live where the model and market disagree on **who advances** — not in the "
         "across-the-board underdog +EV below, which is the model's diffuseness.*", "",
         "| Match | Kickoff (ET) | Model adv% (H/A) | DK adv% (H/A) | Fav: model / DK |",
         "|---|---|---|---|---|"]
    o += rows

    o += ["", "## Edge candidates (favourite disagreements)", ""]
    if not edges:
        o.append(f"_None — the model and DK agree on the advancer in all "
                 f"{len(fixtures)}/{len(fixtures)} ties. The model mirrors the sharp line, "
                 "so there is **no clean advance-market edge** this round._")
    else:
        for h, a, mh, ma, dh, da, side, ev, price in edges:
            o.append(f"- **{h} vs {a}** — model backs **{side}** to advance; DK backs the other "
                     f"side. Model {h} {mh*100:.0f}% / {a} {ma*100:.0f}% vs DK {h} {dh*100:.0f}% "
                     f"/ {a} {da*100:.0f}%. Bet {side} at {price} (EV {ev*100:+.0f}%).")

    if diffuse:
        o += ["", "## Model–market gap (calibration artifact, NOT edge)", "",
              "The model assigns the **underdog** more advance equity than the sharp market in "
              "every tie below, producing nominal +EV on plus-money longshots. This is the "
              "model's known diffuseness (its knockout distribution treats level ties as "
              "coin-flips and is flatter than the market) — the same miscalibration as "
              "`MODEL_VS_DK_ELIMINATION.md`. Listed for transparency, **not as bets:**", ""]
        for h, a, mh, ma, dh, da, side, ev, price in diffuse:
            o.append(f"- {h} vs {a}: model {h} {mh*100:.0f}% / {a} {ma*100:.0f}% vs DK "
                     f"{h} {dh*100:.0f}% / {a} {da*100:.0f}% → underdog {side} nominal "
                     f"EV {ev*100:+.0f}% at {price}.")

    o += ["", "*Grade on ADVANCEMENT, not 90-minute W/D/L. A favourite that wins on penalties "
          "after a level 90' is a hit.*"]

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(o) + "\n")
    n_dis = sum(1 for e in edges if e[-1])
    print(f"Wrote {OUT} — {len(fixtures)} fixtures, {n_dis} favourite disagreement(s), "
          f"{len(edges)} edge candidate(s)")


if __name__ == "__main__":
    main()
