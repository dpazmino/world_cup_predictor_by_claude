"""
Deterministic mental / emotional model for the simulation's player agents (p2.txt §6-9).

No LLM — purely keyword- and state-driven, mirroring player_skills.py. Implements the core
p2.txt idea:  Action = ability + personality + role + game state + pressure + fatigue.

  * init_mind        — set the two traits (composure, risk_tolerance) from prompt + role
  * situational_pressure — perceived pressure 0-100 from scoreline / clock / pitch zone / fatigue
  * risk_modifier    — a multiplier (centred on 1.0 at neutral state, so calibration is
                       unchanged by default) that the rule policy applies to shoot/dribble
                       tendencies, and the LLM prompt describes in words
  * update_mind      — evolve confidence / frustration after each play's outcome
  * mental_line      — a one-line mindset cue injected into the LLM ball-holder prompt
"""
from __future__ import annotations

# ── trait keywords ───────────────────────────────────────────────────────────
_COMPOSURE_HI = ("composure", "composed", "ice-cold", "ice cold", "calm", "cool",
                 "unflappable", "press resistance", "press-resistant", "resist pressure",
                 "scanning", "intelligent", "experienced", "leader", "captain", "veteran")
_COMPOSURE_LO = ("rash", "reckless", "hot-headed", "loses his head", "erratic",
                 "raw", "nervous", "wild")
_RISK_HI = ("creative", "flair", "audacious", "risk", "adventurous", "killer pass",
            "through ball", "line-breaking", "ambitious", "shoot on sight", "shoots on sight",
            "take on", "take-on")
_RISK_LO = ("safe", "simple", "recycle", "conservative", "disciplined", "holds position",
            "patient", "tidy", "low risk", "low-risk", "keeps it simple")

_ROLE_COMPOSURE = {"GK": 0.62, "CB": 0.60, "RB": 0.50, "LB": 0.50, "RWB": 0.50, "LWB": 0.50,
                   "CDM": 0.62, "CM": 0.56, "CAM": 0.54, "RM": 0.50, "LM": 0.50,
                   "RW": 0.46, "LW": 0.46, "CF": 0.50, "ST": 0.50}
_ROLE_RISK = {"GK": 0.18, "CB": 0.24, "RB": 0.40, "LB": 0.40, "RWB": 0.48, "LWB": 0.48,
              "CDM": 0.30, "CM": 0.46, "CAM": 0.70, "RM": 0.60, "LM": 0.60,
              "RW": 0.72, "LW": 0.72, "CF": 0.62, "ST": 0.66}


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def init_mind(player, prompt: str) -> None:
    """Set composure & risk_tolerance traits from the player's prompt + role; reset state."""
    text = (prompt or "").lower()
    comp = _ROLE_COMPOSURE.get(player.role, 0.52)
    comp += 0.04 * sum(k in text for k in _COMPOSURE_HI) - 0.05 * sum(k in text for k in _COMPOSURE_LO)
    risk = _ROLE_RISK.get(player.role, 0.5)
    risk += 0.03 * sum(k in text for k in _RISK_HI) - 0.04 * sum(k in text for k in _RISK_LO)
    player.composure = _clamp(comp, 0.15, 0.95)
    player.risk_tolerance = _clamp(risk, 0.10, 0.95)
    player.confidence = 50.0
    player.frustration = 0.0


def situational_pressure(state, player) -> float:
    """Perceived stimulus/pressure load 0-100 (p2.txt 'score and time' + zone + fatigue)."""
    p = 30.0
    ar = state.team_attacks_right(player.team)
    x = player.position.x
    if (ar and x < 35) or (not ar and x > 70):
        p += 22.0                                   # in own defensive third
    elif (ar and x > 70) or (not ar and x < 35):
        p += 8.0                                    # final third (chance pressure)
    opp = "away" if player.team == "home" else "home"
    diff = state.score[player.team] - state.score[opp]
    if state.minute >= 70:
        p += 22.0 if diff < 0 else 12.0 if diff == 0 else 8.0
    p += 0.15 * player.fatigue + 0.10 * player.frustration
    return _clamp(p, 0.0, 100.0)


def risk_modifier(state, player) -> float:
    """
    Multiplier on adventurous-action tendencies (shoot/dribble/risky pass). Centred on 1.0
    at neutral state (conf 50, frustration 0, low pressure, fresh) so default calibration is
    unchanged; deviates as the player's emotional state and the game situation change.
    """
    pressure = situational_pressure(state, player)
    m = 1.0
    m *= 1.0 + 0.16 * (player.risk_tolerance - 0.5)         # trait, ±8%
    m *= 1.0 + 0.0030 * (player.confidence - 50.0)          # confidence, ±15%
    choke = max(0.0, (pressure - 45.0) / 100.0) * (1.0 - player.composure)
    m *= 1.0 - 0.45 * choke                                 # pressure beyond comfort, if low composure
    m *= 1.0 + 0.0025 * player.frustration                  # frustration → force it, +25% max
    m *= 1.0 - 0.0020 * max(0.0, player.fatigue - 30.0)     # tired → fewer risks
    opp = "away" if player.team == "home" else "home"
    diff = state.score[player.team] - state.score[opp]
    if state.minute >= 75 and diff < 0:
        m *= 1.12                                           # chasing the game
    elif state.minute >= 80 and diff > 0:
        m *= 0.90                                           # protecting a lead
    return _clamp(m, 0.55, 1.5)


def _bump(p, conf=0.0, frus=0.0) -> None:
    p.confidence = _clamp(p.confidence + conf, 0.0, 100.0)
    p.frustration = _clamp(p.frustration + frus, 0.0, 100.0)


def update_mind(state, holder, result) -> None:
    """Evolve confidence/frustration after a play's outcome. Called once per play."""
    for p in state.players.values():                        # gentle decay toward baseline
        if not p.is_on_pitch:
            continue
        p.frustration = max(0.0, p.frustration - 0.6)
        p.confidence += (50.0 - p.confidence) * 0.02

    et = getattr(result, "event_type", None)
    if getattr(result, "scoring_team", None):
        sc = result.scoring_team
        if holder is not None and holder.team == sc:
            _bump(holder, conf=+16, frus=-12)
        for p in state.get_team_players(sc):
            _bump(p, conf=+5, frus=-4)
        conceding = "away" if sc == "home" else "home"
        for p in state.get_team_players(conceding):
            _bump(p, conf=-5, frus=+7 if p.role in ("GK", "CB", "RB", "LB") else +4)
    elif et in ("shot_missed", "shot_saved", "shot_blocked", "woodwork", "penalty_saved"):
        if holder is not None:
            _bump(holder, conf=-6, frus=+7)
    elif et in ("interception", "tackle", "misplaced_pass"):
        if holder is not None:
            _bump(holder, conf=-4, frus=+5)
        w = state.players.get(getattr(result, "new_ball_holder", None))
        if w is not None:
            _bump(w, conf=+4, frus=-2)
    elif et == "foul" and getattr(result, "foul_by", None):
        fp = state.players.get(result.foul_by)
        if fp is not None:
            _bump(fp, frus=+6)
    elif et in ("pass", "dribble", "cross") and holder is not None:
        _bump(holder, conf=+1.5, frus=-1.0)


def mental_line(state, player) -> str:
    """One-line mindset cue for the LLM ball-holder prompt (steers behaviour per p2.txt §6)."""
    pressure = situational_pressure(state, player)
    conf = "high" if player.confidence > 62 else "low" if player.confidence < 38 else "steady"
    comp = "elite" if player.composure > 0.65 else "fragile" if player.composure < 0.40 else "average"
    pres = "HEAVY" if pressure > 65 else "moderate" if pressure > 45 else "low"
    if pressure > 58 and player.composure < 0.45:
        steer = "Under this pressure take the safe option — a simple pass or clearance, don't force it."
    elif player.confidence > 65 and player.risk_tolerance > 0.6:
        steer = "You're confident — back yourself to try the ambitious pass, dribble, or shot."
    elif player.fatigue > 70:
        steer = "Legs are heavy — keep it simple, fewer dribbles."
    elif player.frustration > 45:
        steer = "You're frustrated — stay composed, don't force a rash pass or shot."
    else:
        steer = ""
    return (f"Mindset: confidence {conf}, composure {comp}, pressure {pres}. {steer}").strip()
