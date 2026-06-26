"""
Evaluator Agent — the "soccer coach observer" that prevents hallucinations.

Checks player decisions for:
  1. Physical possibility (can a GK shoot from 80m?)
  2. Positional realism (does a CDM go into the opponent box alone?)
  3. Tactical sense (does the action match the match situation?)
  4. Rule compliance (is the target in a valid position?)

If a decision is flagged as unrealistic, this agent corrects it.
"""
from __future__ import annotations
from ..game_state import GameState, PlayerState, Position
from ..field import get_zone, PITCH_LENGTH, CENTER_X
from .base_agent import llm_call, SMART_MODEL

_EVALUATOR_SYSTEM = """You are an expert football analyst and anti-hallucination evaluator.
Your job is to catch unrealistic decisions by player agents and correct them.
Respond ONLY with valid JSON."""

# Hard-coded rule violations (fast, no LLM needed)
def _quick_sanity_check(state: GameState, player: PlayerState, action: dict) -> tuple[bool, str, dict]:
    """
    Returns (is_valid, reason, corrected_action).
    Fast rule-based checks that don't need an LLM.
    """
    act = action.get("action", "hold")
    pos = player.position
    zone = get_zone(pos, player.team)

    # GK should not dribble forward when far from own goal
    if player.role == "GK" and act == "dribble":
        direction = action.get("direction", "forward")
        if direction in ("forward", "into_box") and zone not in ("own_box", "defensive_third"):
            corrected = {**action, "action": "pass", "reasoning": "GK should pass, not dribble in this zone"}
            return False, "GK dribbling out of position", corrected

    # CB should not shoot from beyond 35m
    if player.role in ("CB", "RB", "LB") and act == "shoot":
        goal_x = 105.0 if state.team_attacks_right(player.team) else 0.0
        dist = abs(pos.x - goal_x)
        if dist > 35:
            corrected = {**action, "action": "pass", "reasoning": "Too far to shoot — pass instead"}
            return False, f"{player.role} attempting shot from {dist:.0f}m", corrected

    # CDM should not leave defensive zone when team is losing and in open play
    if (player.role == "CDM" and act in ("dribble", "cross") and
            state.score.get(player.team, 0) < state.score.get("away" if player.team == "home" else "home", 0)):
        if zone in ("attacking_third", "opponent_box"):
            direction = action.get("direction", "")
            if direction == "into_box":
                corrected = {**action, "action": "pass", "reasoning": "CDM should hold shape when losing"}
                return False, "CDM going into box while team is losing", corrected

    # Can't pass to a player on the bench
    target = action.get("target_player")
    if target and target not in state.players:
        teammates = [p.name for p in state.get_team_players(player.team) if p.name != player.name]
        corrected = {**action, "target_player": teammates[0] if teammates else None}
        return False, f"Target player {target} not on pitch", corrected

    # Can't pass to opponent
    if target and target in state.players:
        target_player = state.players[target]
        if target_player.team != player.team:
            teammates = [p.name for p in state.get_team_players(player.team) if p.name != player.name]
            corrected = {**action, "target_player": teammates[0] if teammates else None}
            return False, f"Player tried to pass to opponent {target}", corrected

    return True, "", action


def _llm_validate(state: GameState, player: PlayerState, action: dict, issue: str) -> dict:
    """LLM-based correction for borderline cases."""
    teammates = [p.name for p in state.get_team_players(player.team) if p.name != player.name]
    user = f"""
## FLAGGED DECISION
Player: {player.name} ({player.role}, {player.team} team)
Position: x={player.position.x:.1f}, y={player.position.y:.1f}
Minute: {state.minute}' | Score: {state.score_str()}
Proposed action: {action}
Issue detected: {issue}

Correct this decision. The action must be realistic for a {player.role}.
Available teammates: {', '.join(teammates[:5])}

Respond with JSON (valid action dict):
{{
  "action": "pass" | "shoot" | "dribble" | "cross" | "clear" | "hold",
  "target_player": "name or null",
  "direction": "forward | left | right | backward",
  "reasoning": "corrected reason"
}}
""".strip()
    result = llm_call(_EVALUATOR_SYSTEM, user, model=SMART_MODEL, max_tokens=150)
    if "action" in result:
        return result
    return {**action, "action": "pass"}   # safe fallback


class EvaluatorAgent:
    """
    Validates and corrects player decisions.
    Run BEFORE the resolution engine processes an action.
    """

    def __init__(self):
        self.corrections_made: int = 0
        self.log: list[dict] = []

    def validate_and_correct(self, state: GameState,
                              player: PlayerState,
                              action: dict) -> dict:
        """
        Returns the (possibly corrected) action dict.
        Logs any corrections for post-match analysis.
        """
        is_valid, reason, corrected = _quick_sanity_check(state, player, action)

        if not is_valid:
            self.corrections_made += 1
            entry = {
                "minute": state.minute,
                "player": player.name,
                "original": action,
                "reason": reason,
                "corrected": corrected,
            }
            self.log.append(entry)
            # For severe violations, ask LLM for correction
            if reason.startswith("GK") or "opponent" in reason:
                corrected = _llm_validate(state, player, corrected, reason)
            return corrected

        return action

    def validate_position(self, player: PlayerState,
                           proposed: Position,
                           state: GameState) -> Position:
        """
        Sanity-check a proposed repositioning.
        GK must stay near own goal. CF must not drop behind half-way.
        """
        from ..field import clamp_to_pitch, PITCH_LENGTH, CENTER_X

        attacking_right = state.team_attacks_right(player.team)

        if player.role == "GK":
            # GK must stay in own half near goal
            if attacking_right:
                proposed.x = min(proposed.x, 20.0)
            else:
                proposed.x = max(proposed.x, 85.0)

        elif player.role == "CF" and state.phase == "open_play":
            # CF should stay in or near attacking half
            if attacking_right:
                proposed.x = max(proposed.x, CENTER_X - 10)
            else:
                proposed.x = min(proposed.x, CENTER_X + 10)

        return clamp_to_pitch(proposed)

    def summary(self) -> str:
        return f"Evaluator: {self.corrections_made} corrections made across {len(self.log)} incidents."
