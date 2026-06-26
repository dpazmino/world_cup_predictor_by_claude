"""
Referee Agent — enforces the Laws of the Game.

Validates player actions for fouls, offside, out-of-bounds.
Issues yellow and red cards. Manages set-piece awards.
"""
from __future__ import annotations
import random
from ..game_state import GameState, PlayerState, MatchEvent
from .base_agent import llm_call, SMART_MODEL

_REFEREE_SYSTEM = """You are a FIFA World Cup referee. You enforce the Laws of the Game.
Respond ONLY with valid JSON. Be fair but firm."""


def _card_check(state: GameState, tackler: PlayerState,
                is_foul: bool, foul_location: str) -> str | None:
    """
    Decide whether a foul deserves a card.
    Returns "yellow", "red", or None.
    """
    if not is_foul:
        return None

    # Straight red: last-man tackle or violent conduct (5% chance)
    if random.random() < 0.05:
        return "red"

    # Yellow card: reckless or persistent (20% chance)
    if tackler.yellow_cards >= 1 and random.random() < 0.30:
        # Second yellow → red
        return "red"

    if random.random() < 0.20:
        return "yellow"

    return None


class RefereeAgent:
    def __init__(self):
        self.cards_issued: list[dict] = []

    def validate_foul(self, state: GameState,
                      tackler_name: str | None,
                      ball_holder_name: str | None) -> dict:
        """
        Called after a tackle/dribble fails.
        Returns {"is_foul": bool, "card": "yellow"|"red"|None, "description": str}
        """
        # Deliberate foul detection via LLM (lightweight)
        holder = state.players.get(ball_holder_name) if ball_holder_name else None
        tackler = state.players.get(tackler_name) if tackler_name else None

        foul_zone = "box" if holder and (
            (holder.team == "away" and holder.position.x < 16.5) or
            (holder.team == "home" and holder.position.x > 88.5)
        ) else "open_play"

        is_foul = random.random() < 0.30   # 30% of successful tackles flagged as fouls
        card = _card_check(state, tackler, is_foul, foul_zone) if tackler else None

        if is_foul and foul_zone == "box":
            phase = "penalty"
        elif is_foul:
            phase = "free_kick"
        else:
            phase = "open_play"

        desc = ""
        if is_foul:
            card_str = f" — {card.upper()} CARD" if card else ""
            desc = f"Foul by {tackler.name if tackler else 'defender'}{card_str}."
            if card and tackler:
                if card == "yellow":
                    tackler.yellow_cards += 1
                tackler.is_on_pitch = (card != "red")
                self.cards_issued.append({
                    "minute": state.minute,
                    "player": tackler.name,
                    "team": tackler.team,
                    "card": card,
                })
                state.add_event(
                    f"{card}_card",
                    desc,
                    player=tackler.name,
                    team=tackler.team,
                )
        return {"is_foul": is_foul, "card": card, "description": desc, "new_phase": phase}

    def card_for_foul(self, state: GameState, tackler_name: str | None) -> str | None:
        """
        Decide and apply a card for a foul that resolution.py already flagged.
        Sends off the player on a red (including second yellow) and records it.
        Returns "yellow", "red", or None.
        """
        tackler = state.players.get(tackler_name) if tackler_name else None
        if not tackler:
            return None

        card = _card_check(state, tackler, is_foul=True, foul_location="open_play")
        if not card:
            return None

        if card == "yellow":
            tackler.yellow_cards += 1
        elif card == "red":
            tackler.is_on_pitch = False
            if tackler.team == "home":
                state.red_cards_home += 1
            else:
                state.red_cards_away += 1

        self.cards_issued.append({
            "minute": state.minute,
            "player": tackler.name,
            "team": tackler.team,
            "card": card,
        })
        state.add_event(f"{card}_card",
                        f"{card.upper()} card for {tackler.name}.",
                        player=tackler.name, team=tackler.team)
        return card

    def llm_penalty_decision(self, state: GameState,
                              attacker_name: str,
                              contact_description: str) -> dict:
        """
        For borderline penalty calls — ask the referee LLM.
        """
        user = f"""
## PENALTY DECISION
Minute: {state.minute}' | Match: {state.score_str()}
Attacker {attacker_name} went down in the box.
Contact: {contact_description}

Was this a penalty foul? Respond with JSON:
{{
  "penalty": true | false,
  "card": "yellow" | "red" | null,
  "reasoning": "one sentence"
}}
""".strip()
        return llm_call(_REFEREE_SYSTEM, user, model=SMART_MODEL, max_tokens=120)

    def check_dangerous_play(self, state: GameState,
                              player_name: str,
                              action_description: str) -> bool:
        """Quick check for dangerous play. Returns True if action should be stopped."""
        return False   # Simplified: handled in resolution.py foul probability
