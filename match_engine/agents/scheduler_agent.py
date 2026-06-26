"""
Scheduler Agent — manages tournament fixtures, groups, and bracket progression.

Supports:
  - 48-team group stage (8 groups of 6)
  - Round of 32 → Round of 16 → QF → SF → Final
  - Fixture assignment with dates/venues
  - Standings tracking
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field
from typing import Optional
from .base_agent import llm_call, SMART_MODEL

_SCHEDULER_SYSTEM = """You are the FIFA World Cup 2026 tournament scheduler.
Respond ONLY with valid JSON."""

# Real 2026 FIFA World Cup group draw (12 groups of 4). Names use the repo's
# canonical squad keys (South Korea, USA, Turkey, Curacao, ...) so they resolve
# to a matching <team>_prompts.py / tactical profile / rating.
GROUPS_2026 = {
    "A": ["Mexico", "South Africa", "South Korea", "Czechia"],
    "B": ["Canada", "Bosnia and Herzegovina", "Qatar", "Switzerland"],
    "C": ["Brazil", "Morocco", "Haiti", "Scotland"],
    "D": ["USA", "Paraguay", "Australia", "Turkey"],
    "E": ["Germany", "Curacao", "Ivory Coast", "Ecuador"],
    "F": ["Netherlands", "Japan", "Sweden", "Tunisia"],
    "G": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "H": ["Spain", "Cape Verde", "Saudi Arabia", "Uruguay"],
    "I": ["France", "Senegal", "Iraq", "Norway"],
    "J": ["Argentina", "Algeria", "Austria", "Jordan"],
    "K": ["Portugal", "DR Congo", "Uzbekistan", "Colombia"],
    "L": ["England", "Croatia", "Ghana", "Panama"],
}


@dataclass
class MatchFixture:
    fixture_id: str
    home_team: str
    away_team: str
    stage: str          # "group", "r32", "r16", "qf", "sf", "final"
    group: Optional[str] = None
    venue: str = "TBD"
    date: str = "TBD"
    result: Optional[dict] = None   # {"home": int, "away": int}
    winner: Optional[str] = None    # team name (set for knockout ties incl. shootouts)
    played: bool = False


@dataclass
class TeamStanding:
    team: str
    group: str
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0

    @property
    def points(self) -> int:
        return self.won * 3 + self.drawn

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


class SchedulerAgent:
    """Manages the full tournament lifecycle."""

    def __init__(self, teams: list[str] | None = None):
        self.fixtures: list[MatchFixture] = []
        self.standings: dict[str, TeamStanding] = {}
        self.teams = teams or list({t for g in GROUPS_2026.values() for t in g})
        self._fixture_counter = 0
        self._groups = GROUPS_2026

    def create_group_fixtures(self) -> list[MatchFixture]:
        """Generate all group-stage fixtures (round-robin within each group)."""
        self.fixtures = []
        for group_name, teams in self._groups.items():
            for team in teams:
                self.standings[team] = TeamStanding(team=team, group=group_name)
            for i in range(len(teams)):
                for j in range(i + 1, len(teams)):
                    self._fixture_counter += 1
                    fix = MatchFixture(
                        fixture_id=f"GS-{self._fixture_counter:03d}",
                        home_team=teams[i],
                        away_team=teams[j],
                        stage="group",
                        group=group_name,
                        venue=self._assign_venue(),
                        date=f"2026-06-{10 + self._fixture_counter % 20:02d}",
                    )
                    self.fixtures.append(fix)
        return self.fixtures

    def record_result(self, fixture_id: str,
                       home_goals: int, away_goals: int,
                       winner: Optional[str] = None) -> None:
        """
        Update standings after a match result.

        `winner` (a team name) resolves knockout ties decided in extra time or a
        shootout; for group games it is derived from the scoreline.
        """
        fix = next((f for f in self.fixtures if f.fixture_id == fixture_id), None)
        if not fix:
            return
        fix.result = {"home": home_goals, "away": away_goals}
        fix.played = True

        # Knockout fixtures: record who advances (handles draws → ET/shootout).
        if fix.stage != "group":
            if winner:
                fix.winner = winner
            elif home_goals > away_goals:
                fix.winner = fix.home_team
            elif away_goals > home_goals:
                fix.winner = fix.away_team
            return

        home_s = self.standings.get(fix.home_team)
        away_s = self.standings.get(fix.away_team)
        if not home_s or not away_s:
            return

        home_s.played += 1
        away_s.played += 1
        home_s.goals_for += home_goals
        home_s.goals_against += away_goals
        away_s.goals_for += away_goals
        away_s.goals_against += home_goals

        if home_goals > away_goals:
            home_s.won += 1
            away_s.lost += 1
        elif home_goals < away_goals:
            away_s.won += 1
            home_s.lost += 1
        else:
            home_s.drawn += 1
            away_s.drawn += 1

    def get_group_standings(self, group: str) -> list[TeamStanding]:
        """Return sorted standings for a group."""
        teams = [s for s in self.standings.values() if s.group == group]
        return sorted(teams, key=lambda t: (-t.points, -t.goal_difference, -t.goals_for))

    def get_next_unplayed(self) -> Optional[MatchFixture]:
        """Return the next unplayed fixture."""
        return next((f for f in self.fixtures if not f.played), None)

    def create_knockout_fixture(self, home_team: str, away_team: str, stage: str) -> MatchFixture:
        """Create a knockout round fixture."""
        self._fixture_counter += 1
        fix = MatchFixture(
            fixture_id=f"{stage.upper()}-{self._fixture_counter:03d}",
            home_team=home_team,
            away_team=away_team,
            stage=stage,
            venue=self._assign_venue(stage),
            date=f"2026-07-{1 + self._fixture_counter % 14:02d}",
        )
        self.fixtures.append(fix)
        return fix

    def print_group_table(self, group: str) -> None:
        print(f"\n  Group {group}:")
        print(f"  {'Team':<20} {'P':>3} {'W':>3} {'D':>3} {'L':>3} {'GF':>4} {'GA':>4} {'GD':>4} {'Pts':>4}")
        print("  " + "-" * 55)
        for s in self.get_group_standings(group):
            print(f"  {s.team:<20} {s.played:>3} {s.won:>3} {s.drawn:>3} {s.lost:>3} "
                  f"{s.goals_for:>4} {s.goals_against:>4} {s.goal_difference:>4} {s.points:>4}")

    def _assign_venue(self, stage: str = "group") -> str:
        venues = {
            "group": ["MetLife Stadium, NJ", "AT&T Stadium, Dallas",
                      "SoFi Stadium, LA", "Hard Rock Stadium, Miami",
                      "BC Place, Vancouver", "Azteca Stadium, Mexico City",
                      "Rose Bowl, Pasadena"],
            "sf": ["MetLife Stadium, NJ", "AT&T Stadium, Dallas"],
            "final": ["MetLife Stadium, NJ"],
        }
        pool = venues.get(stage, venues["group"])
        return random.choice(pool)
