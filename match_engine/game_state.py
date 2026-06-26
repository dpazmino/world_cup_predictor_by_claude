"""Core data models for the match simulation."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import math


@dataclass
class Position:
    x: float  # 0-105: home attacks toward x=105, away toward x=0
    y: float  # 0-68: standard pitch width

    def distance_to(self, other: "Position") -> float:
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)

    def to_dict(self) -> dict:
        return {"x": round(self.x, 1), "y": round(self.y, 1)}

    def __repr__(self) -> str:
        return f"({self.x:.1f}, {self.y:.1f})"


@dataclass
class PlayerState:
    name: str
    team: str           # "home" or "away"
    role: str           # GK, CB, RB, LB, CDM, CM, CAM, RW, LW, CF
    position: Position
    fatigue: float = 0.0       # 0-100, increases during match
    is_on_pitch: bool = True
    shirt_number: int = 0
    yellow_cards: int = 0
    goals: int = 0
    assists: int = 0
    # Individual skill profiles — populated from prompt text at match setup
    pass_skill: float = 0.82
    intercept_skill: float = 0.30
    shoot_skill: float = 0.55
    dribble_skill: float = 0.65
    # Athletic attributes (0.0-1.0): foot races, recovery, aerial duels, stamina.
    pace: float = 0.70
    physical: float = 0.70
    stamina: float = 0.70
    # Mental / emotional state (p2.txt §7) — composure & base risk_tolerance are traits
    # set at setup; confidence & frustration evolve during the match and modulate the
    # on-ball decision (player_mind.py). Defaults are neutral so untouched code is unchanged.
    composure: float = 0.5          # 0-1 trait: resistance to pressure (set at setup)
    risk_tolerance: float = 0.5     # 0-1 trait: safe vs adventurous (set at setup)
    confidence: float = 50.0        # 0-100, rises after success, falls after mistakes
    frustration: float = 0.0        # 0-100, rises after fouls/misses/conceding

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "team": self.team,
            "role": self.role,
            "position": self.position.to_dict(),
            "fatigue": round(self.fatigue, 0),
        }


@dataclass
class MatchEvent:
    minute: int
    second: int
    event_type: str    # "goal", "yellow_card", "red_card", "substitution",
                       # "foul", "offside", "shot_saved", "shot_blocked",
                       # "pass", "tackle", "corner", "free_kick", "penalty"
    description: str
    player: Optional[str] = None
    team: Optional[str] = None
    position: Optional[Position] = None

    def time_str(self) -> str:
        return f"{self.minute}'{self.second:02d}\""


@dataclass
class Substitution:
    minute: int
    team: str
    player_off: str
    player_on: str
    reason: str  # "fatigue", "tactical", "injury"


@dataclass
class GameState:
    home_team: str
    away_team: str
    home_formation: str   # e.g., "4-3-3"
    away_formation: str
    players: dict[str, PlayerState]          # name -> state
    ball_position: Position
    ball_holder: Optional[str]               # player name; None = loose ball
    last_ball_holder: Optional[str] = None  # who passed last (anti-pingpong)
    score: dict = field(default_factory=lambda: {"home": 0, "away": 0})
    minute: int = 0
    second: int = 0
    half: int = 1
    phase: str = "kickoff"                   # kickoff | open_play | free_kick |
                                              # corner | penalty | throw_in |
                                              # goal_kick | half_time | full_time
    set_piece_team: Optional[str] = None     # which team takes the set piece
    events: list[MatchEvent] = field(default_factory=list)
    substitutions: list[Substitution] = field(default_factory=list)
    play_count: int = 0
    home_subs_used: int = 0
    away_subs_used: int = 0
    possession_home: int = 0                 # count of touches
    possession_away: int = 0
    possession_secs_home: float = 0.0        # time-weighted possession (seconds)
    possession_secs_away: float = 0.0
    # Fast-break state: the team that just won the ball gets a brief edge while
    # the opponent is out of shape. transition_ttl counts plays remaining.
    transition_team: Optional[str] = None
    transition_ttl: int = 0
    red_cards_home: int = 0
    red_cards_away: int = 0
    shots_home: int = 0
    shots_away: int = 0
    corners_home: int = 0
    corners_away: int = 0
    fouls_home: int = 0
    fouls_away: int = 0
    # Morale: 0.7 (low) to 1.3 (high). Affects shot urgency and press intensity.
    morale_home: float = 1.0
    morale_away: float = 1.0

    def get_team_players(self, team: str) -> list[PlayerState]:
        return [p for p in self.players.values() if p.team == team and p.is_on_pitch]

    def get_ball_holder_state(self) -> Optional[PlayerState]:
        return self.players.get(self.ball_holder) if self.ball_holder else None

    def get_attacking_team(self) -> Optional[str]:
        if self.ball_holder:
            return self.players[self.ball_holder].team
        return None

    def time_str(self) -> str:
        return f"{self.minute}:{self.second:02d}"

    def score_str(self) -> str:
        return f"{self.home_team} {self.score['home']}-{self.score['away']} {self.away_team}"

    def team_attacks_right(self, team: str) -> bool:
        """Home team attacks toward x=105 (right). Away attacks toward x=0 (left)."""
        return team == "home"

    def attacking_goal_x(self, team: str) -> float:
        return 105.0 if team == "home" else 0.0

    def to_context_dict(self) -> dict:
        """Compact state for LLM context — keeps tokens low."""
        return {
            "score": self.score_str(),
            "minute": self.minute,
            "half": self.half,
            "phase": self.phase,
            "ball": {
                "position": self.ball_position.to_dict(),
                "holder": self.ball_holder,
            },
            "home_players": [p.to_dict() for p in self.get_team_players("home")],
            "away_players": [p.to_dict() for p in self.get_team_players("away")],
        }

    def add_event(self, event_type: str, description: str,
                  player: str = None, team: str = None,
                  position: Position = None) -> None:
        self.events.append(MatchEvent(
            minute=self.minute,
            second=self.second,
            event_type=event_type,
            description=description,
            player=player,
            team=team,
            position=position or self.ball_position,
        ))

    def stats_summary(self) -> dict:
        # Time-weighted possession when available; fall back to touch counts.
        home_secs = self.possession_secs_home
        away_secs = self.possession_secs_away
        if home_secs + away_secs > 0:
            home_share, away_share = home_secs, away_secs
        else:
            home_share, away_share = self.possession_home, self.possession_away
        total = home_share + away_share or 1
        return {
            "possession": {
                "home": round(100 * home_share / total),
                "away": round(100 * away_share / total),
            },
            "shots": {"home": self.shots_home, "away": self.shots_away},
            "corners": {"home": self.corners_home, "away": self.corners_away},
            "fouls": {"home": self.fouls_home, "away": self.fouls_away},
        }
