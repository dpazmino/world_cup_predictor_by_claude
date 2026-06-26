"""
Match predictor — strength-based, NO simulation.

Win/draw/loss probabilities come from the dynamic Elo strength model (learned
from data/results.csv, recency-decayed, regularised toward squad strength),
calibrated by shrinking toward the base-rate prior, and optionally anchored to
the betting market.

The match SIMULATION is intentionally not on this path: walk-forward
cross-validation (backtest.py) showed the optimal sim weight is ~0 — the
simulation adds no predictive value over team strength (see docs/GAP_ANALYSIS.md).
The simulator remains the match-narrative engine (run_match.py, calibrate.py).

Expected goals and indicative scorelines come from a simple independent-Poisson
model on the rating gap (no simulation).

Usage:
    python predict.py Brazil France
    python predict.py England Germany --home
    python predict.py Brazil France --odds 3.20 3.40 2.20
"""
from __future__ import annotations
import sys
import os
import math
import random
import argparse
import datetime
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from match_engine.ratings import team_rating, combine_probs, canonical_name
from match_engine.elo import default_model, DRAW_BASE, DRAW_TAU
from match_engine.agents.scheduler_agent import GROUPS_2026


def known_teams() -> list[str]:
    """All teams with squad data (the 48 groups + any extra squad files, e.g. Czechia)."""
    import glob
    base = {t for g in GROUPS_2026.values() for t in g}
    seen = {canonical_name(t).lower() for t in base}
    here = os.path.dirname(os.path.abspath(__file__))
    for f in glob.glob(os.path.join(here, "*_prompts.py")):
        stem = os.path.basename(f)[:-len("_prompts.py")]
        if stem == "player":
            continue
        pretty = stem.replace("_", " ").title()
        key = canonical_name(pretty).lower()
        if key in seen:
            continue                      # already covered (handles USA/Usa, South Korea, …)
        if team_rating(pretty)["n"] > 0:
            base.add(pretty)
            seen.add(key)
    return sorted(base)


def _print_team_menu(known: list[str], cols: int = 3) -> None:
    """Print the available teams as a numbered, multi-column menu."""
    print("\nAvailable teams:")
    rows = (len(known) + cols - 1) // cols
    for r in range(rows):
        line = "  "
        for c in range(cols):
            i = c * rows + r
            if i < len(known):
                line += f"{i+1:>3}. {known[i]:<16}"
        print(line.rstrip())
    print()


def _select_team(role: str, known: list[str]) -> str:
    """Ask the user to pick a team by number (or type a name)."""
    while True:
        raw = input(f"  {role} team — enter number 1-{len(known)} (or type a name): ").strip()
        if raw.isdigit():
            i = int(raw)
            if 1 <= i <= len(known):
                return known[i - 1]
            print(f"   number out of range — enter 1-{len(known)}.")
            continue
        if not raw:
            print(f"   enter a number 1-{len(known)}, or a team name.")
            continue
        # typed a name
        if team_rating(canonical_name(raw))["n"] > 0:
            return raw
        ans = input(f"   '{raw}' has no rating data — use anyway (uninformed)? [y/N]: ").strip().lower()
        if ans == "y":
            return raw

GOAL_TOTAL = 2.6        # baseline combined expected goals
SUP_PER_ELO = 0.004     # goal supremacy per Elo point of (decayed) rating gap


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _scorelines(lam_home: float, lam_away: float, maxg: int = 6) -> Counter:
    grid = Counter()
    for i in range(maxg + 1):
        for j in range(maxg + 1):
            grid[(i, j)] = _poisson_pmf(i, lam_home) * _poisson_pmf(j, lam_away)
    return grid


def _elo_probs(diff: float) -> tuple:
    pe = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
    dr = DRAW_BASE * math.exp(-abs(diff) / DRAW_TAU)
    return ((1 - dr) * pe, dr, (1 - dr) * (1 - pe))


def _rating_interval(m, home, away, today, neutral, shrink, samples=400):
    """
    95% interval on home/away win probability from RATING uncertainty: perturb each
    team's Elo by a games-based standard error (fewer games → wider), recompute.
    """
    rh, ra = m.effective_rating(home, today), m.effective_rating(away, today)
    seh = 40.0 + 90.0 * (1 - m.confidence(home))   # ~40 (well-known) .. 130 (unknown)
    sea = 40.0 + 90.0 * (1 - m.confidence(away))
    rng = random.Random(0)
    hs, as_ = [], []
    for _ in range(samples):
        d = rng.gauss(rh, seh) - rng.gauss(ra, sea) + (0.0 if neutral else m.hfa)
        p = combine_probs(_elo_probs(d), _elo_probs(d), blend=0.0, shrink=shrink)
        hs.append(p[0]); as_.append(p[2])
    hs.sort(); as_.sort()
    lo, hi = int(0.05 * samples), int(0.95 * samples) - 1
    return (hs[lo], hs[hi]), (as_[lo], as_[hi])


def predict(home: str, away: str, neutral: bool = True, shrink: float = 0.25,
            odds: tuple | None = None, market_weight: float = 0.5) -> dict:
    """
    Predict (home, draw, away) from team strength, reported from `home`'s view.

    `shrink` (0..1) pulls the strength probabilities toward the base-rate prior to
    calibrate over-confidence (default tuned via backtest.py). `odds` =
    (decimal_home, decimal_draw, decimal_away) closing market odds; when given the
    de-vigged market is anchored in with `market_weight`.
    """
    m = default_model()
    today = datetime.date.today().isoformat()
    strength_p = m.strength_probs(home, away, neutral=neutral, as_of=today)

    # Calibrate: shrink toward the base-rate prior.
    final = combine_probs(strength_p, strength_p, blend=0.0, shrink=shrink)

    market_p = None
    if odds is not None:
        from match_engine.odds import implied_probs, anchor
        market_p = implied_probs(*odds)
        final = anchor(final, market_p, market_weight)

    # Expected goals + scorelines from the (decayed) Elo gap — independent Poisson.
    diff = (m.effective_rating(home, today) - m.effective_rating(away, today)
            + (0.0 if neutral else m.hfa))
    sup = diff * SUP_PER_ELO
    lam_home = max(0.15, (GOAL_TOTAL + sup) / 2)
    lam_away = max(0.15, (GOAL_TOTAL - sup) / 2)

    ci_home, ci_away = _rating_interval(m, home, away, today, neutral, shrink)

    # Flag teams with no rating data — they fall back to a default (overall 70),
    # so the prediction is uninformed (≈ base rate), not a real forecast.
    unknown = [t for t in (home, away) if team_rating(t)["n"] == 0]

    return {
        "home": home, "away": away, "neutral": neutral, "shrink": shrink,
        "p_home": final[0], "p_draw": final[1], "p_away": final[2],
        "ci_home": ci_home, "ci_away": ci_away, "unknown": unknown,
        "p_strength": strength_p, "p_market": market_p,
        "market_weight": market_weight if odds is not None else 0.0,
        "xg_home": lam_home, "xg_away": lam_away,
        "scorelines": _scorelines(lam_home, lam_away),
    }


def _verdict(out: dict) -> str:
    """Rule-based one-line confidence read: coin flip vs (clear/strong) favourite.

    Pure thresholding on the model's own numbers — instant, deterministic, no LLM.
    A wide rating CI (thin data) or an unknown squad downgrades it to low confidence.
    """
    home, away = out["home"], out["away"]
    ph, pd, pa = out["p_home"], out["p_draw"], out["p_away"]
    fav, fav_p, fav_ci = (home, ph, out["ci_home"]) if ph >= pa else (away, pa, out["ci_away"])
    margin = fav_p - min(ph, pa)

    if margin < 0.08 or fav_p < 0.40 or pd >= max(ph, pa):
        tier = "Coin flip — too close to call"
    elif fav_p < 0.50:
        tier = f"Slight lean to {fav}"
    elif fav_p < 0.62:
        tier = f"{fav} favoured"
    elif fav_p < 0.74:
        tier = f"{fav} clear favourite"
    else:
        tier = f"{fav} strong favourite"

    # Confidence qualifier from rating uncertainty (wide CI / thin / unknown squads).
    width = fav_ci[1] - fav_ci[0]
    thin = any(0 < team_rating(t)["n"] < 11 for t in (home, away))
    if out["unknown"]:
        tier += "  (UNINFORMED — no rating data)"
    elif thin or width > 0.30:
        tier += "  (low confidence — wide rating uncertainty)"
    return tier


def _american_to_decimal(a: float) -> float:
    """Convert American/moneyline odds to decimal: +250 -> 3.50, -150 -> 1.67."""
    a = float(a)
    if a == 0:
        raise ValueError("American odds cannot be 0")
    return a / 100.0 + 1.0 if a > 0 else 100.0 / abs(a) + 1.0


def _load_env():
    """Load ANTHROPIC_API_KEY (and any KEY=VALUE) from a .env file if present."""
    env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8").read().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, val = line.split("=", 1)
                os.environ.setdefault(k.strip(), val.strip())


def main():
    _load_env()
    ap = argparse.ArgumentParser(description="Predict a football match (strength model).")
    ap.add_argument("home", nargs="?", help="home team (prompted if omitted)")
    ap.add_argument("away", nargs="?", help="away team (prompted if omitted)")
    ap.add_argument("--home", dest="home_adv", action="store_true",
                    help="give the first team home advantage (default: neutral venue)")
    ap.add_argument("--shrink", type=float, default=0.25,
                    help="shrink toward the base-rate prior (0=none, 1=pure prior; tuned via backtest --sweep)")
    ap.add_argument("--odds", type=float, nargs=3, metavar=("HOME", "DRAW", "AWAY"),
                    help="closing decimal odds to anchor to (e.g. --odds 1.80 3.6 4.5)")
    ap.add_argument("--odds-american", type=float, nargs=3, metavar=("HOME", "DRAW", "AWAY"),
                    help="American/moneyline odds (e.g. DraftKings: --odds-american +115 +240 +250)")
    ap.add_argument("--market-weight", type=float, default=0.5,
                    help="how much to trust the market when --odds given (0..1)")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the AI prediction-verification step")
    args = ap.parse_args()

    # Interactive: show a numbered menu and let the user pick any team not given.
    home, away = args.home, args.away
    if home is None or away is None:
        known = known_teams()
        _print_team_menu(known)
        if home is None:
            home = _select_team("Home", known)
        if away is None:
            away = _select_team("Away", known)

    rh, ra = team_rating(home), team_rating(away)
    odds = tuple(args.odds) if args.odds else None
    if odds is None and args.odds_american:
        odds = tuple(_american_to_decimal(x) for x in args.odds_american)
        print(f"  (American odds {args.odds_american} -> decimal "
              f"{tuple(round(o, 2) for o in odds)})")
    out = predict(home, away, neutral=not args.home_adv,
                  shrink=args.shrink, odds=odds, market_weight=args.market_weight)

    venue = "home advantage to " + home if args.home_adv else "neutral venue"
    m = default_model()

    # Note any alias resolution so the displayed name vs rating is transparent.
    for raw in (home, away):
        c = canonical_name(raw)
        if c != raw and team_rating(c)["n"] > 0:
            print(f"  (resolved '{raw}' -> '{c}')")

    # Loud warning if either team has no rating data (silent fallback otherwise).
    if out["unknown"]:
        names = " and ".join(out["unknown"])
        print(f"\n  !!! WARNING: no rating data for {names} — using a default (overall 70).")
        print( "      This prediction is UNINFORMED (~ base rate), NOT a real forecast.")
        print( "      Check spelling (e.g. 'South Korea' not 'Korea Republic'), or add the")
        print( "      squad to player_stats.csv / <team>_prompts.py.")
    else:
        thin = [t for t in (home, away) if 0 < team_rating(t)["n"] < 11]
        if thin:
            print(f"\n  note: thin squad data for {' and '.join(thin)} (<11 rated players) "
                  "— rating is noisy.")

    print(f"\n{home} vs {away}  —  strength model ({venue}, shrink={out['shrink']:.2f})")
    print(f"  squad overall: {home} {rh['overall']:.0f} | {away} {ra['overall']:.0f}    "
          f"Elo: {m.rating(home):.0f} | {m.rating(away):.0f}")
    st = out["p_strength"]
    line = f"  (strength {100*st[0]:.0f}/{100*st[1]:.0f}/{100*st[2]:.0f}"
    if out["p_market"]:
        mk = out["p_market"]
        line += (f"  |  market {100*mk[0]:.0f}/{100*mk[1]:.0f}/{100*mk[2]:.0f} "
                 f"@w={out['market_weight']:.2f}")
    print(line + ")")
    ch, ca = out["ci_home"], out["ci_away"]
    print(f"\n  {home} win : {100*out['p_home']:5.1f}%   (95% rating CI {100*ch[0]:.0f}-{100*ch[1]:.0f}%)")
    print(f"  draw      : {100*out['p_draw']:5.1f}%")
    print(f"  {away} win : {100*out['p_away']:5.1f}%   (95% rating CI {100*ca[0]:.0f}-{100*ca[1]:.0f}%)")
    print(f"\n  VERDICT: {_verdict(out)}")
    print(f"\n  expected goals (Poisson): {home} {out['xg_home']:.2f} - "
          f"{out['xg_away']:.2f} {away}")
    print("  most likely scorelines:")
    for (h, a), c in out["scorelines"].most_common(6):
        print(f"    {h}-{a}   {100*c:.1f}%")
    print()

    # AI verification step — an LLM audits the finished forecast for problems.
    if not args.no_verify:
        from match_engine.agents.prediction_verifier import verify
        v = verify(out)
        verdict = v.get("verdict", "unavailable")
        if verdict in ("skipped", "unavailable"):
            print(f"  AI verification: {v.get('summary', verdict)}\n")
        else:
            label = {"sound": "SOUND", "minor_issues": "MINOR ISSUES",
                     "warning": "WARNING"}.get(verdict, verdict.upper())
            print(f"  --- AI verification: {label} ---")
            print(f"  {v.get('summary', '')}")
            for it in v.get("issues", []):
                sev = str(it.get("severity", "")).upper()
                print(f"   [{sev}] {it.get('issue', '')} — {it.get('detail', '')}")
            print()


if __name__ == "__main__":
    main()
