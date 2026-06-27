"""Backtest the 'turtle' betting filter — asymmetric-payoff underdog value.

Turtle thesis (see docs/MODEL_PERFORMANCE.md §5/§7): the only place this model can
pay is plus-money underdogs it rates higher than the market — where one winner
covers two losers — and NOT minus-money favourites (win often, lose money). The
trap is the model's diffuseness: it likes almost every longshot, so we must filter
to genuinely-strong-but-under-covered sides, not thin-data minnows.

This scores that rule honestly: leak-free walk-forward over every past fixture that
has closing odds in data/results.csv. For each game we may back ONE side — the
plus-money side with the biggest (model - market) edge that clears the filters —
and grade it at the real decimal price (draw = loss; you bet a team to win in 90').

    python backtest_turtle.py            # variant table + the qualifying bets

No API key, no simulation. Compares against flat-favourite and flat-underdog baselines.
"""
import sys
from backtest import load_results, outcome_index
from match_engine.elo import EloModel
from match_engine.ratings import combine_probs, team_rating
from match_engine.odds import implied_probs

PLUS_MONEY = 2.0          # decimal >= 2.0  == American +100 or longer
THIN = 11                 # < 11 rated players in a squad => low-confidence rating


def walk_forward():
    """Leak-free per-game model probs + market probs for fixtures that have odds."""
    rows = [{**fx, "actual": outcome_index(fx["hg"], fx["ag"])} for fx in load_results()]
    elo = EloModel()
    out = []
    for i in sorted(range(len(rows)), key=lambda i: rows[i]["date"]):
        r = rows[i]
        d = r["date"]
        sp = elo.strength_probs(r["home"], r["away"], neutral=r["neutral"], as_of=d)
        model = combine_probs(sp, sp, blend=0.0, shrink=0.25)   # production shrink
        if r.get("odds"):
            out.append({
                "home": r["home"], "away": r["away"], "date": d,
                "actual": r["actual"], "dec": r["odds"], "model": model,
                "market": implied_probs(*r["odds"]),
            })
        elo.update(r["home"], r["away"], r["hg"], r["ag"], neutral=r["neutral"], date=d)
    return out


def thin(team):
    n = team_rating(team)["n"]
    return n < THIN          # 0 (unknown) counts as thin too


def pick_turtle_bet(g, min_dec, edge_min, prob_floor, skip_thin, model_fav):
    """Return (side_idx, decimal, edge) for the best qualifying plus-money value
    side in game g, or None. side_idx: 0 = home, 2 = away."""
    model_pick = max(range(3), key=lambda k: g["model"][k])   # 0/1/2 argmax
    best = None
    for side, team in ((0, g["home"]), (2, g["away"])):
        dec = g["dec"][side]
        m_model = g["model"][side]
        m_mkt = g["market"][side]
        edge = m_model - m_mkt
        if dec < min_dec:                       # not plus-money enough
            continue
        if edge <= edge_min:                    # model not bullish enough vs market
            continue
        if m_model < prob_floor:                # diffuse longshot, not a live dog
            continue
        if model_fav and side != model_pick:    # model must outright favour this side
            continue
        if skip_thin and thin(team):            # thin-data minnow, model is noisy
            continue
        if best is None or edge > best[2]:
            best = (side, dec, edge)
    return best


def run_variant(games, label, min_dec=PLUS_MONEY, edge_min=0.0, prob_floor=0.0,
                skip_thin=False, model_fav=False, collect=False):
    w = l = 0
    net = 0.0
    bets = []
    for g in games:
        sel = pick_turtle_bet(g, min_dec, edge_min, prob_floor, skip_thin, model_fav)
        if not sel:
            continue
        side, dec, edge = sel
        won = (g["actual"] == side)
        net += (dec - 1.0) if won else -1.0
        w += won
        l += (not won)
        if collect:
            team = g["home"] if side == 0 else g["away"]
            bets.append((g["date"], g["home"], g["away"], team, dec, edge, won))
    n = w + l
    roi = 100 * net / n if n else 0.0
    print(f"  {label:<46} {n:>3} bets  {w}-{l}  net {net:>+6.2f}u  ROI {roi:>+6.1f}%")
    return bets


def baseline(games, fav=True):
    """Flat 1u on the market favourite (fav=True) or underdog among home/away."""
    w = l = 0
    net = 0.0
    for g in games:
        side = 0 if g["market"][0] >= g["market"][2] else 2
        if not fav:
            side = 2 if side == 0 else 0
        won = (g["actual"] == side)
        net += (g["dec"][side] - 1.0) if won else -1.0
        w += won
        l += (not won)
    n = w + l
    print(f"  {('market favourite' if fav else 'market underdog'):<46} "
          f"{n:>3} bets  {w}-{l}  net {net:>+6.2f}u  ROI {100*net/n:>+6.1f}%")


def main():
    games = walk_forward()
    print(f"Turtle backtest — {len(games)} past fixtures with closing odds "
          f"(leak-free walk-forward, draw = loss)\n")

    print("Baselines (bet every game):")
    baseline(games, fav=True)
    baseline(games, fav=False)

    print("\nTurtle variants (bet ≤1 plus-money value side per game):")
    run_variant(games, "plus-money + model>market (any side)")
    run_variant(games, "+ not thin", skip_thin=True)
    run_variant(games, "+ not thin + model≥30% (live dog)",
                skip_thin=True, prob_floor=0.30)
    run_variant(games, "+ not thin + edge≥5pts", skip_thin=True, edge_min=0.05)
    run_variant(games, "MODEL-FAV plus-money dog + not thin",
                skip_thin=True, model_fav=True)

    print("\nQualifying bets — 'MODEL-FAV plus-money dog + not thin' variant:")
    bets = run_variant(games, "(detail below)", skip_thin=True, model_fav=True,
                       collect=True)
    for date, h, a, team, dec, edge, won in bets:
        print(f"    {date}  {h} v {a:<24} back {team:<16} @ {dec:>5.2f}  "
              f"edge {edge*100:>+4.0f}pts  {'WON' if won else 'lost'}")


if __name__ == "__main__":
    main()
