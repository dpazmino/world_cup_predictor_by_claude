"""
Commentator Agent — generates broadcast-quality play-by-play commentary.
Uses the fast model (Haiku) to stay responsive during the simulation.
"""
from __future__ import annotations
from ..game_state import GameState
from .base_agent import llm_text, FAST_MODEL

_COMMENTATOR_SYSTEM = """You are an expert football broadcast commentator for a World Cup match.
Your commentary is vivid, concise, and accurate. One or two sentences maximum.
Capture the excitement, tension, and tactical nuance. No question marks — declarative sentences only."""


def _goal_commentary(state: GameState, scorer: str, scoring_team: str) -> str:
    # Resolve to display names + explicit score so the model can't swap teams.
    team_name = state.home_team if scoring_team == "home" else state.away_team
    other_name = state.away_team if scoring_team == "home" else state.home_team
    hs, as_ = state.score["home"], state.score["away"]
    user = f"""
{team_name} have just SCORED. The goal was scored BY {team_name} — NOT by {other_name}.
Scorer: {scorer}. The score is now {state.home_team} {hs} - {as_} {state.away_team}.
Write ONE electrifying sentence celebrating {team_name}'s goal.
Do NOT say {other_name} scored, and do not mistake who is leading.
""".strip()
    result = llm_text(_COMMENTATOR_SYSTEM, user, model=FAST_MODEL, max_tokens=80)
    return result or f"GOAL! {scorer} scores for {team_name}! {state.home_team} {hs}-{as_} {state.away_team}."


def _event_commentary(state: GameState, event_type: str,
                       description: str, player: str = None) -> str:
    user = f"""
Match: {state.score_str()} | Minute: {state.minute}' | Half: {state.half}
Event: {event_type}
Details: {description}
{f'Player involved: {player}' if player else ''}
Write ONE sentence of match commentary for this event.
""".strip()
    result = llm_text(_COMMENTATOR_SYSTEM, user, model=FAST_MODEL, max_tokens=60)
    return result or description


def _half_time_summary(state: GameState) -> str:
    stats = state.stats_summary()
    user = f"""
HALF TIME: {state.score_str()}
Possession: {state.home_team} {stats['possession']['home']}% — {state.away_team} {stats['possession']['away']}%
Shots: {state.home_team} {stats['shots']['home']} — {state.away_team} {stats['shots']['away']}

Write a 2-sentence half-time summary capturing the key themes of the first half.
""".strip()
    result = llm_text(_COMMENTATOR_SYSTEM, user, model=FAST_MODEL, max_tokens=100)
    return result or f"Half time: {state.score_str()}."


def _full_time_summary(state: GameState) -> str:
    stats = state.stats_summary()
    user = f"""
FULL TIME: {state.score_str()}
Possession: {state.home_team} {stats['possession']['home']}% — {state.away_team} {stats['possession']['away']}%
Shots: {state.home_team} {stats['shots']['home']} — {state.away_team} {stats['shots']['away']}

Write a 2-sentence final result summary capturing what happened in this match.
""".strip()
    result = llm_text(_COMMENTATOR_SYSTEM, user, model=FAST_MODEL, max_tokens=100)
    return result or f"Full time: {state.score_str()}."


class CommentatorAgent:
    def __init__(self, home_team: str, away_team: str):
        self.home_team = home_team
        self.away_team = away_team
        self.log: list[str] = []

    def describe(self, state: GameState, event_type: str,
                  description: str, player: str = None,
                  scoring_team: str = None) -> str:
        """Generate commentary for any event. Returns the commentary string."""
        if event_type == "goal" and scoring_team:
            text = _goal_commentary(state, player or "Unknown", scoring_team)
        elif event_type in ("shot_saved", "penalty_saved"):
            text = _event_commentary(state, event_type, description, player)
        elif event_type in ("yellow_card", "red_card"):
            text = _event_commentary(state, event_type, description, player)
        else:
            # For routine events (pass, dribble, etc.), use the description directly
            # to save API calls — only enrich special moments
            text = description
        self.log.append(f"  {state.time_str()} | {text}")
        return text

    def half_time(self, state: GameState) -> str:
        text = _half_time_summary(state)
        self.log.append(f"\n--- HALF TIME ---\n{text}\n")
        return text

    def full_time(self, state: GameState) -> str:
        text = _full_time_summary(state)
        self.log.append(f"\n--- FULL TIME ---\n{text}\n")
        return text

    def print_log(self) -> None:
        for line in self.log:
            print(line)
