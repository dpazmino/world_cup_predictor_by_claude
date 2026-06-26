"""
Dynamic Elo rating system for international teams.

Unlike the static squad-overall ratings in ratings.py, Elo *learns from results*:
each match nudges both teams' ratings toward what actually happened, weighted by
the goal margin and match importance. Ratings are seeded from squad strength so
teams with few recorded results still start sensibly, then move toward evidence.

This is the World-Football-Elo style:
  expected_home = 1 / (1 + 10^(-(R_home - R_away + HFA) / 400))
  R' = R + K * G * (W - E)
where W ∈ {1, 0.5, 0} (win/draw/loss), G is a goal-difference multiplier, and HFA
is the home-field advantage in Elo points (0 at a neutral venue).

Win/draw/loss probabilities are derived by splitting the Elo expectation with a
draw curve that peaks when teams are evenly matched.

CLI:
    python -m match_engine.elo                 # ratings table from data/results.csv
    python -m match_engine.elo Brazil France    # one matchup's probabilities
"""
from __future__ import annotations
import os
import sys
import datetime
from functools import lru_cache

# ── Tunable constants ────────────────────────────────────────────────────────
HFA = 65.0          # home-field advantage, in Elo points (0 for neutral venues)
DEFAULT_K = 40.0    # base update rate (World Cup uses ~60; 40 is a sane general K)
DRAW_BASE = 0.45    # draw probability when two teams are level. Raised from 0.28 to lift
                    # the predicted draw rate toward the ~27.5% observed (RPS-optimal from
                    # ~0.45 up); kept here so home advantage still helps underdogs — higher
                    # values make level-match draws so likely that the HFA win-bump washes out.
                    # The share decays with the rating gap (DRAW_TAU) so lopsided games draw less.
DRAW_TAU = 200.0    # Elo points over which the draw share decays
GAMES_FULL = 30     # games before a team's Elo is fully trusted over its squad prior
HALF_LIFE_DAYS = 540  # idle time over which a team's learned edge halves toward its prior

_RESULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "results.csv")


def _days_between(d1: str | None, d2: str | None) -> float:
    """Days from ISO date d1 to d2 (0 if either missing or d2<=d1)."""
    if not d1 or not d2:
        return 0.0
    try:
        a = datetime.date.fromisoformat(d1)
        b = datetime.date.fromisoformat(d2)
    except ValueError:
        return 0.0
    return max(0.0, (b - a).days)


def _prior_from_overall(overall: float) -> float:
    """Map a squad's 0-99 overall to a starting Elo (~1470 weak .. ~2040 elite)."""
    return 119.0 + 22.2 * overall


def _gd_multiplier(margin: int) -> float:
    """Goal-difference weighting (bigger wins move ratings more)."""
    if margin <= 1:
        return 1.0
    if margin == 2:
        return 1.5
    return (11.0 + margin) / 8.0


class EloModel:
    def __init__(self, hfa: float = HFA, half_life: float = HALF_LIFE_DAYS):
        self.hfa = hfa
        self.half_life = half_life
        self.r: dict[str, float] = {}
        self.prior: dict[str, float] = {}
        self.games: dict[str, int] = {}
        self.last_date: dict[str, str] = {}

    # -- ratings -------------------------------------------------------------
    def rating(self, team: str) -> float:
        from .ratings import canonical_name
        team = canonical_name(team)
        if team not in self.r:
            from .ratings import team_rating
            p = _prior_from_overall(team_rating(team)["overall"])
            self.r[team] = p
            self.prior[team] = p
            self.games[team] = 0
        return self.r[team]

    def effective_rating(self, team: str, as_of: str | None = None) -> float:
        """
        Rating decayed toward the squad prior by time idle since the last match.
        Recent form is kept; stale form fades back to the structural baseline.
        Non-mutating — used for prediction.
        """
        from .ratings import canonical_name
        team = canonical_name(team)
        r = self.rating(team)
        if as_of is None or self.half_life <= 0:
            return r
        days = _days_between(self.last_date.get(team), as_of)
        if days <= 0:
            return r
        factor = 0.5 ** (days / self.half_life)
        return self.prior[team] + (r - self.prior[team]) * factor

    def _diff(self, home: str, away: str, neutral: bool, as_of: str | None) -> float:
        return (self.effective_rating(home, as_of) - self.effective_rating(away, as_of)
                + (0.0 if neutral else self.hfa))

    def expected_home(self, home: str, away: str, neutral: bool = True,
                      as_of: str | None = None) -> float:
        """Elo expected score for the home side (win + 0.5·draw), in [0, 1]."""
        return 1.0 / (1.0 + 10.0 ** (-self._diff(home, away, neutral, as_of) / 400.0))

    def win_probabilities(self, home: str, away: str, neutral: bool = True,
                          as_of: str | None = None) -> tuple:
        """(P_home, P_draw, P_away) from the rating gap, with a draw curve."""
        import math
        diff = self._diff(home, away, neutral, as_of)
        p_home_excl = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
        draw = DRAW_BASE * math.exp(-abs(diff) / DRAW_TAU)
        return ((1 - draw) * p_home_excl, draw, (1 - draw) * (1 - p_home_excl))

    def confidence(self, team: str) -> float:
        """How much to trust Elo over the squad prior (0..1), grows with games played."""
        from .ratings import canonical_name
        return min(self.games.get(canonical_name(team), 0), GAMES_FULL) / GAMES_FULL

    def strength_probs(self, home: str, away: str, neutral: bool = True,
                       as_of: str | None = None) -> tuple:
        """
        Production strength model: regularised, recency-decayed Elo. Blends the
        static squad logistic with Elo, weighted by how many games the
        *less-experienced* side has played — so thin-data teams lean on squad
        strength and Elo only takes over once it has earned trust. As results.csv
        grows this shifts to Elo. `as_of` applies recency decay to that date.
        """
        from .ratings import outcome_probs, HOME_ADV_POINTS
        c = min(self.confidence(home), self.confidence(away))
        elo_p = self.win_probabilities(home, away, neutral, as_of=as_of)
        squad_p = outcome_probs(home, away, home_adv=0.0 if neutral else HOME_ADV_POINTS)
        return tuple((1 - c) * squad_p[i] + c * elo_p[i] for i in range(3))

    # -- updates -------------------------------------------------------------
    def update(self, home: str, away: str, hg: int, ag: int,
               neutral: bool = True, k: float = DEFAULT_K,
               date: str | None = None) -> None:
        from .ratings import canonical_name
        home, away = canonical_name(home), canonical_name(away)
        # Apply recency decay to the match date, then bake it into stored ratings.
        for t in (home, away):
            self.r[t] = self.effective_rating(t, date)
            if date:
                self.last_date[t] = date
        e_home = self.expected_home(home, away, neutral)   # as_of=None: already decayed
        w_home = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        delta = k * _gd_multiplier(abs(hg - ag)) * (w_home - e_home)
        self.r[home] += delta
        self.r[away] -= delta
        self.games[home] = self.games.get(home, 0) + 1
        self.games[away] = self.games.get(away, 0) + 1

    def process(self, matches: list[dict], k: float = DEFAULT_K) -> None:
        """Replay matches in the given order (caller should pass them chronologically)."""
        for m in matches:
            self.update(m["home"], m["away"], m["hg"], m["ag"],
                        neutral=m.get("neutral", True), k=k, date=m.get("date"))

    def table(self) -> list[tuple]:
        """Return [(team, rating, games)] sorted strongest-first (rated teams only)."""
        return sorted(((t, self.r[t], self.games.get(t, 0)) for t in self.r),
                      key=lambda x: -x[1])


def load_matches(path: str = _RESULTS) -> list[dict]:
    """Read results.csv into chronologically-sorted match dicts."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split(",")
            if len(p) < 5:
                continue
            neutral = int(p[5]) if len(p) > 5 and p[5].strip() else 1
            rows.append({"date": p[0], "home": p[1], "away": p[2],
                         "hg": int(p[3]), "ag": int(p[4]), "neutral": bool(neutral)})
    rows.sort(key=lambda m: m["date"])
    return rows


@lru_cache(maxsize=1)
def default_model() -> EloModel:
    """Elo model built from the full results history (cached). For live prediction."""
    m = EloModel()
    m.process(load_matches())
    return m


def _main():
    args = sys.argv[1:]
    if len(args) >= 2:
        m = default_model()
        home, away = args[0], args[1]
        today = datetime.date.today().isoformat()
        for neutral in (True, False):
            ph, pd, pa = m.win_probabilities(home, away, neutral=neutral, as_of=today)
            tag = "neutral" if neutral else f"{home} home"
            print(f"  {tag:<14} {home} {100*ph:4.0f}%  draw {100*pd:4.0f}%  "
                  f"{away} {100*pa:4.0f}%   (Elo {m.effective_rating(home, today):.0f} "
                  f"vs {m.effective_rating(away, today):.0f})")
        return
    m = default_model()
    print(f"\nElo ratings from {len(load_matches())} matches "
          f"(seeded from squad strength, K={DEFAULT_K:.0f}, HFA={HFA:.0f}):\n")
    print(f"  {'#':>2}  {'team':<16}{'Elo':>6}{'games':>7}")
    for i, (team, rating, games) in enumerate(m.table(), 1):
        print(f"  {i:>2}  {team:<16}{rating:>6.0f}{games:>7}")


if __name__ == "__main__":
    _main()
