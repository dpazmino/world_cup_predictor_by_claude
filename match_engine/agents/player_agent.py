"""
Player Agent — the core LLM decision maker for each player.

Two modes:
  1. decide_with_ball()  — player HAS the ball, chooses action
  2. decide_position()   — player does NOT have ball, repositions
"""
from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..game_state import GameState, PlayerState, Position
from ..field import clamp_to_pitch, get_zone
from .base_agent import llm_call, FAST_MODEL
from ..stats_loader import get_player_stats
from ..player_mind import risk_modifier, mental_line


_BALL_HOLDER_SYSTEM = """You are a World Cup footballer. Respond ONLY with valid JSON — no extra text.

YOUR ONLY OBJECTIVE IS TO HELP YOUR TEAM SCORE A GOAL.
Every decision must move the ball closer to the opponent's net.

SCORING RULES — follow in this order:
1. SHOOT if you are in PRIME SHOOTING POSITION or shootable range — do not hesitate.
2. PASS to a teammate who is closer to goal and OPEN — advance the ball forward.
3. DRIBBLE if you have space and can make ground toward goal.
4. CROSS if you are wide near the box and a striker is making a run.
5. CLEAR only if under HEAVY PRESSURE deep in your own half — then find a teammate quickly.

NEVER pass backward unless you are completely surrounded with no forward option.
NEVER hold the ball — the clock is running and your team needs a goal."""

_POSITION_SYSTEM = """You are a World Cup footballer. Respond ONLY with valid JSON — no extra text.
You are OFF the ball. Your job is to get into a position where you can RECEIVE the ball and help your team score.
- If your team has the ball: make a run toward the opponent's goal — get open, get forward.
- If the opponent has the ball: track back to defend, but be ready to counter the moment possession changes."""


def _state_summary(state: GameState) -> str:
    ctx = state.to_context_dict()
    return json.dumps(ctx, indent=None, separators=(",", ":"))


def _ball_holder_prompt(state: GameState, player: PlayerState,
                         player_system_prompt: str) -> tuple[str, str]:
    """Returns (system, user) prompts for the ball-holder decision."""
    attacking_right = state.team_attacks_right(player.team)
    goal_x = 105.0 if attacking_right else 0.0
    goal_y = 34.0
    zone = get_zone(player.position, player.team)

    teammates = [p for p in state.get_team_players(player.team) if p.name != player.name]
    opponents = state.get_team_players("away" if player.team == "home" else "home")

    # ── Pressure on the ball holder ──────────────────────────────────────────
    if opponents:
        nearest_opp = min(opponents, key=lambda p: player.position.distance_to(p.position))
        pressure_dist = player.position.distance_to(nearest_opp.position)
        if pressure_dist < 3:
            pressure = f"HEAVY PRESSURE — {nearest_opp.name} ({nearest_opp.role}) is {pressure_dist:.1f}m away"
        elif pressure_dist < 7:
            pressure = f"closing — {nearest_opp.name} ({nearest_opp.role}) is {pressure_dist:.1f}m away"
        else:
            pressure = f"space — nearest opponent ({nearest_opp.name}) is {pressure_dist:.1f}m away"
    else:
        pressure = "no immediate pressure"

    # ── Distance / angle to goal ──────────────────────────────────────────────
    dist_to_goal = ((player.position.x - goal_x) ** 2 + (player.position.y - goal_y) ** 2) ** 0.5
    angle_y = abs(player.position.y - goal_y)
    shot_quality = (
        "PRIME SHOOTING POSITION" if dist_to_goal < 18 and angle_y < 12 else
        "shootable" if dist_to_goal < 28 and angle_y < 20 else
        "long range" if dist_to_goal < 40 else
        "too far to shoot"
    )

    # ── Field scan: every teammate with open/marked status ───────────────────
    def _openness(tm: PlayerState) -> str:
        if not opponents:
            return "OPEN"
        nearest = min(opponents, key=lambda p: tm.position.distance_to(p.position))
        d = tm.position.distance_to(nearest.position)
        return "OPEN" if d > 6 else "half-open" if d > 3 else f"MARKED by {nearest.name}"

    # Sort teammates by how far forward they are (closest to opponent goal first)
    sorted_mates = sorted(
        teammates,
        key=lambda p: p.position.x if attacking_right else (105 - p.position.x),
        reverse=True,
    )

    from ..field import pass_lane_blocked as _lane_check

    scan_lines = []
    for tm in sorted_mates:
        status = _openness(tm)
        pass_dist = player.position.distance_to(tm.position)
        tm_dist_goal = ((tm.position.x - goal_x)**2 + (tm.position.y - goal_y)**2)**0.5
        fwd = "ahead" if (
            (attacking_right and tm.position.x > player.position.x) or
            (not attacking_right and tm.position.x < player.position.x)
        ) else "behind"
        lane_blocker = _lane_check(player.position, tm.position, opponents, intercept_radius=1.2)
        lane = f"LANE BLOCKED by {lane_blocker.name}" if lane_blocker else "lane clear"
        tm_stats = get_player_stats(tm.name)
        ovr = f" OVR:{tm_stats['raw_overall']}" if tm_stats else ""
        scan_lines.append(
            f"  {tm.name} ({tm.role}{ovr}) — {status}, {pass_dist:.0f}m [{fwd}], "
            f"{tm_dist_goal:.0f}m from goal, {lane}"
        )

    # ── Closest opponents (defenders to beat) ────────────────────────────────
    close_opps = sorted(opponents, key=lambda p: player.position.distance_to(p.position))[:5]
    opp_lines = [
        f"  {p.name} ({p.role}) — {player.position.distance_to(p.position):.0f}m away"
        for p in close_opps
    ]

    # ── Player's own attributes ──────────────────────────────────────────────
    my_stats = get_player_stats(player.name)
    if my_stats:
        my_ovr   = my_stats["raw_overall"]
        my_dri   = int(my_stats["dribble_skill"] * 99)
        my_pac   = int(my_stats.get("pace", player.dribble_skill) * 99)
        my_sho   = int(my_stats["shoot_skill"] * 99)
        attrs_line = f"OVR {my_ovr} | Dribbling {my_dri} | Pace {my_pac} | Shooting {my_sho}"
    else:
        my_dri = int(player.dribble_skill * 99)
        my_sho = int(player.shoot_skill * 99)
        attrs_line = f"Dribbling ~{my_dri} | Shooting ~{my_sho}"

    # Role-aware decision guidance — always goal-first
    role = player.role
    is_dribbler = role in ("RW", "LW", "CF", "ST", "CAM") or my_dri >= 80
    is_defender = role in ("GK", "CB", "RB", "LB", "RWB", "LWB", "CDM")
    is_winger   = role in ("RW", "LW", "RM", "LM")

    # Shooting urgency line — injected for every role when close enough
    if shot_quality == "PRIME SHOOTING POSITION":
        shoot_line = f"⚽ YOU ARE IN PRIME SHOOTING POSITION ({dist_to_goal:.0f}m) — SHOOT NOW unless completely blocked."
    elif shot_quality == "shootable":
        shoot_line = f"⚽ You are in shooting range ({dist_to_goal:.0f}m) — seriously consider a shot."
    else:
        shoot_line = ""

    if is_defender:
        guidance = (
            "- Clear if under HEAVY PRESSURE in your own half\n"
            "- Pass FORWARD to an OPEN midfielder or winger to launch an attack\n"
            "- Only dribble if you have 7m+ of space ahead\n"
            "- Do NOT shoot unless you are in the opponent half with a clear sight of goal"
        )
    elif is_winger:
        guidance = (
            f"- You have dribbling {my_dri} — run at the defender and beat them toward the box\n"
            "- CROSS into the box if a striker is making a run\n"
            "- SHOOT if you cut inside and have a clear angle\n"
            "- Only pass back if completely shut down with no forward option"
        )
    elif is_dribbler:
        guidance = (
            f"- Drive toward goal — your dribbling ({my_dri}) lets you beat defenders\n"
            "- SHOOT when in shooting range — you are here to score\n"
            "- Pass forward to an OPEN teammate who is closer to goal\n"
            "- Cross if wide and forwards are in the box"
        )
    else:
        guidance = (
            "- Pass FORWARD to the most advanced OPEN teammate\n"
            "- SHOOT if in shooting range — always look for goal\n"
            "- Dribble forward if you have space ahead of you\n"
            "- Cross if you're wide near the box with forwards making runs\n"
            "- Clear only if under HEAVY PRESSURE in your own half"
        )

    # Count teammates inside the box making runs
    box_runners = [
        tm for tm in teammates
        if (attacking_right and tm.position.x > 88) or
           (not attacking_right and tm.position.x < 16.5)
    ]
    box_note = (f"\n⚡ {len(box_runners)} teammate(s) INSIDE THE BOX making runs: "
                f"{', '.join(t.name for t in box_runners[:3])}"
                if box_runners else "")

    user = f"""
## MATCH SITUATION
{state.score_str()} | {state.minute}' | Half {state.half}
You: {player.name} ({player.role}) | Zone: {zone} | Fatigue: {player.fatigue:.0f}%
Your attributes: {attrs_line}
{mental_line(state, player)}
Goal direction: {'right →' if attacking_right else '← left'} | Distance to goal: {dist_to_goal:.0f}m | {shot_quality}
Pressure: {pressure}{box_note}
{shoot_line}

## FIELD SCAN — TEAMMATES (sorted nearest to goal first)
{chr(10).join(scan_lines)}

## CLOSEST OPPONENTS
{chr(10).join(opp_lines)}

## WHAT TO DO
{guidance}

Respond with JSON ONLY:
{{
  "action": "pass" | "shoot" | "dribble" | "cross" | "clear",
  "target_player": "Exact Name",
  "target_zone": "near_post" | "far_post" | "center" | "six_yard_box",
  "direction": "forward" | "left" | "right" | "backward" | "into_box",
  "reasoning": "one sentence — what you saw and why this moves toward GOAL"
}}
""".strip()

    system = f"{player_system_prompt}\n\n{_BALL_HOLDER_SYSTEM}"
    return system, user


def _position_prompt(state: GameState, player: PlayerState,
                     player_system_prompt: str) -> tuple[str, str]:
    """Returns (system, user) prompts for off-ball positioning."""
    attacking_right = state.team_attacks_right(player.team)
    ball_team = state.get_attacking_team()
    in_possession = (ball_team == player.team)
    zone = get_zone(player.position, player.team)

    goal_x = 105.0 if attacking_right else 0.0
    own_goal_x = 0.0 if attacking_right else 105.0

    if in_possession:
        if player.role in ("ST", "CF"):
            run_hint = "Make a run INTO THE BOX — get in scoring position to receive and SHOOT."
        elif player.role in ("RW", "LW", "CAM"):
            run_hint = "Push into the attacking third — get open for a through ball or cut inside to shoot."
        elif player.role in ("CM", "RM", "LM"):
            run_hint = "Get forward — support the attack, arrive into the box late for a shot or tap-in."
        elif player.role in ("CDM", "CB"):
            run_hint = "Hold your shape — stay back to cover, keep team balanced while attack builds."
        else:
            run_hint = "Support the attack — get into a position to receive and progress toward goal."
    else:
        run_hint = "Track back — get goal-side of the ball and press the opponent to win possession back."

    user = f"""
## MATCH STATE
{state.score_str()} | Minute: {state.minute}' | Half: {state.half}
You attack toward: {'x=105 (right)' if attacking_right else 'x=0 (left)'}

## YOUR SITUATION
{player.name} ({player.role}) | Zone: {zone} | Fatigue: {player.fatigue:.0f}%
Your position: x={player.position.x:.1f}, y={player.position.y:.1f}
Ball at: x={state.ball_position.x:.1f}, y={state.ball_position.y:.1f} — held by {'YOUR TEAM' if in_possession else 'OPPONENT'}

## YOUR JOB RIGHT NOW
{run_hint}

Respond with JSON ONLY — choose a target position that gets you into scoring threat or defensive shape:
{{
  "target_x": <float 0-105>,
  "target_y": <float 0-68>,
  "reasoning": "one phrase"
}}
""".strip()

    system = f"{player_system_prompt}\n\n{_POSITION_SYSTEM}"
    return system, user


# ── Rule-based (no-LLM) ball-holder policy ───────────────────────────────────
# Produces the same action dict shape as decide_with_ball, but with zero API
# calls — used for the offline/calibration mode. Tuned for a realistic action
# mix (passes dominate, ~5% shots, occasional dribbles/crosses/clears).

_RULE_SHOOT_TENDENCY = {
    "GK": 0.00, "CB": 0.03, "RB": 0.05, "LB": 0.05,
    "RWB": 0.06, "LWB": 0.06, "CDM": 0.10, "CM": 0.22,
    "CAM": 0.44, "RM": 0.34, "LM": 0.34,
    "RW": 0.48, "LW": 0.48, "CF": 0.56, "ST": 0.62,
}


def _shot_quality_mult(dist: float, angle_y: float) -> float:
    if dist < 18 and angle_y < 12:
        return 1.0          # prime
    if dist < 28 and angle_y < 20:
        return 0.55         # shootable
    if dist < 38 and angle_y < 26:
        return 0.20         # long range
    return 0.0              # too far


def _rule_pick_pass(state: GameState, player: PlayerState,
                    teammates: list, attacking_right: bool,
                    opponents: list) -> dict:
    """Choose a pass target: open, lane-clear, forward-biased, not too far."""
    from ..field import pass_lane_blocked
    pos = player.position

    def fwd(p) -> float:
        return p.position.x if attacking_right else (105.0 - p.position.x)

    def openness(t) -> float:
        if not opponents:
            return 12.0
        return min(t.position.distance_to(o.position) for o in opponents)

    my_fwd = fwd(player)
    best, best_score = None, -1e9
    for t in teammates:
        if t.name == state.last_ball_holder:
            continue
        d = pos.distance_to(t.position)
        if d < 1.0:
            continue
        op = min(8.0, openness(t))   # being "more open" past 8m doesn't help
        lane_blocked = pass_lane_blocked(pos, t.position, opponents, intercept_radius=1.2)
        # Prefer SHORT FORWARD passes: forward progress is rewarded but capped (no
        # incentive to launch 40m balls), and distance stays a real cost — so play
        # advances up the pitch while completion stays realistic.
        progress = min(16.0, fwd(t) - my_fwd)
        score = op * 0.25 + progress * 0.32 - (10.0 if lane_blocked else 0.0) - d * 0.18
        if score > best_score:
            best_score, best = score, t

    if best is None:
        pool = [t for t in teammates if t.name != player.name]
        if not pool:
            return {"action": "hold", "reasoning": "no passing option"}
        best = min(pool, key=lambda t: pos.distance_to(t.position))
    return {"action": "pass", "target_player": best.name,
            "reasoning": f"passes to {best.name}"}


def decide_with_ball_rule(state: GameState, player: PlayerState) -> dict:
    """Deterministic-ish (RNG only) ball-holder decision. No LLM."""
    import random
    ar = state.team_attacks_right(player.team)
    goal_x = 105.0 if ar else 0.0
    pos = player.position
    dist = ((pos.x - goal_x) ** 2 + (pos.y - 34.0) ** 2) ** 0.5
    angle_y = abs(pos.y - 34.0)

    opp_team = "away" if player.team == "home" else "home"
    opponents = state.get_team_players(opp_team)
    teammates = [p for p in state.get_team_players(player.team) if p.name != player.name]
    nearest_opp = min(opponents, key=lambda p: pos.distance_to(p.position), default=None)
    pressure = pos.distance_to(nearest_opp.position) if nearest_opp else 10.0

    role = player.role
    own_third = (ar and pos.x < 35) or (not ar and pos.x > 70)
    att_third = (ar and pos.x > 70) or (not ar and pos.x < 35)
    wide = pos.y < 16 or pos.y > 52

    # Mental/emotional modifier (p2.txt): centred on 1.0 at neutral state, so the action
    # mix is unchanged by default and shifts only as confidence/pressure/fatigue evolve.
    mind = risk_modifier(state, player)

    # Keeper: distribute, or clear under pressure.
    if role == "GK":
        if pressure < 6:
            return {"action": "clear", "reasoning": "keeper clears under pressure"}
        return _rule_pick_pass(state, player, teammates, ar, opponents)

    # Deep + under heavy pressure: defenders clear the danger — a fragile player clears more
    # readily, a composed one tries to play out.
    if own_third and pressure < 2.5 and role in ("CB", "RB", "LB", "CDM", "RWB", "LWB"):
        clear_p = 0.45 + 0.30 * (1.0 - player.composure)
        if random.random() < clear_p:
            return {"action": "clear", "reasoning": "clears the danger"}

    # Shoot when in range (role + quality + finishing + mental state dependent).
    q = _shot_quality_mult(dist, angle_y)
    if q > 0:
        role_base = _RULE_SHOOT_TENDENCY.get(role, 0.25)
        p_shoot = role_base * q * (0.45 + player.shoot_skill * 0.55) * mind
        if random.random() < p_shoot:
            return {"action": "shoot", "reasoning": f"shot from {dist:.0f}m"}

    # Cross from wide attacking areas when there are runners in the box.
    if wide and att_third and role in ("RW", "LW", "RM", "LM", "RB", "LB", "RWB", "LWB", "CM"):
        box_runners = [t for t in teammates
                       if (ar and t.position.x > 88) or (not ar and t.position.x < 16.5)]
        if box_runners and random.random() < 0.45:
            return {"action": "cross", "target_zone": "six_yard_box",
                    "reasoning": "crosses into the box"}

    # Dribble when there is room (more often for natural dribblers; mental state scales it).
    is_dribbler = role in ("RW", "LW", "CF", "ST", "CAM", "RM", "LM") or player.dribble_skill > 0.78
    if pressure > 4 and is_dribbler and random.random() < 0.20 * mind:
        return {"action": "dribble", "direction": "forward", "reasoning": "drives forward"}
    # Any outfielder with space ahead carries the ball forward — lets a pinned
    # team progress out of its own third rather than passing sideways forever.
    if not att_third and pressure > 6 and random.random() < 0.15 * mind:
        return {"action": "dribble", "direction": "forward", "reasoning": "carries into space"}
    if pressure > 8 and random.random() < 0.08 * mind:
        return {"action": "dribble", "direction": "forward", "reasoning": "steps into space"}

    # Default: keep the ball moving with a pass.
    return _rule_pick_pass(state, player, teammates, ar, opponents)


def decide_with_ball(state: GameState, player: PlayerState,
                     player_system_prompt: str) -> dict:
    """Ask the player LLM what to do with the ball. Returns action dict."""
    system, user = _ball_holder_prompt(state, player, player_system_prompt)
    result = llm_call(system, user, model=FAST_MODEL, max_tokens=200, temperature=0.8)

    # Validate and sanitize — default to "pass" so a failed LLM call still moves the ball
    action = result.get("action", "pass").lower()
    valid_actions = {"pass", "shoot", "dribble", "cross", "clear", "hold"}
    if action not in valid_actions:
        action = "pass"
    result["action"] = action
    return result


def decide_position(state: GameState, player: PlayerState,
                    player_system_prompt: str) -> Position:
    """Ask the player LLM where to move (off-ball). Returns a Position."""
    system, user = _position_prompt(state, player, player_system_prompt)
    result = llm_call(system, user, model=FAST_MODEL, max_tokens=120, temperature=0.7)

    try:
        x = float(result.get("target_x", player.position.x))
        y = float(result.get("target_y", player.position.y))
        return clamp_to_pitch(Position(x, y))
    except (TypeError, ValueError):
        return player.position   # stay put if parsing fails


def reposition_all_parallel(state: GameState,
                              player_prompts: dict[str, str],
                              max_workers: int = 12) -> dict[str, Position]:
    """
    Parallel positioning for all non-ball-holders.
    Returns dict[player_name -> new_position].
    """
    non_holders = [
        p for p in state.players.values()
        if p.name != state.ball_holder and p.is_on_pitch
    ]

    results: dict[str, Position] = {}

    def _task(player: PlayerState) -> tuple[str, Position]:
        prompt = player_prompts.get(player.name, "")
        pos = decide_position(state, player, prompt)
        return player.name, pos

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_task, p): p.name for p in non_holders}
        for future in as_completed(futures):
            name, pos = future.result()
            results[name] = pos

    return results
