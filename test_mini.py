"""Quick 5-play integration test."""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from match_engine.match_simulation import MatchSimulation, _kickoff, _advance_time
from match_engine.agents.player_agent import decide_with_ball
from match_engine.resolution import resolve_action, apply_fatigue

sim = MatchSimulation("England", "Germany", verbose=True)
sim.setup()
state = sim.state

_kickoff(state, "home")
state.phase = "open_play"

for play_num in range(5):
    if not state.ball_holder:
        all_on = [p for p in state.players.values() if p.is_on_pitch]
        nearest = min(all_on, key=lambda p: p.position.distance_to(state.ball_position))
        state.ball_holder = nearest.name
        state.ball_position = nearest.position

    holder = state.players[state.ball_holder]
    holder_prompt = sim.all_player_prompts.get(holder.name, "")

    print(f"\nPlay {play_num+1}: {holder.name} ({holder.role}) has the ball at x={holder.position.x:.1f}")

    action = decide_with_ball(state, holder, holder_prompt)
    action = sim.evaluator.validate_and_correct(state, holder, action)
    print(f"  Action: {action.get('action')} | {action.get('reasoning', '')[:70]}")

    result = resolve_action(state, action)
    print(f"  Result: {result.event_type} — {result.description[:80]}")

    apply_fatigue(state, action, result.time_seconds)
    _advance_time(state, result.time_seconds)
    state.ball_position = result.new_ball_position
    if result.new_ball_holder and result.new_ball_holder in state.players:
        state.ball_holder = result.new_ball_holder
    elif result.event_type in ("goal", "corner", "free_kick"):
        state.ball_holder = None

    if result.scoring_team:
        state.score[result.scoring_team] += 1
        print(f"  *** GOAL! Score: {state.score_str()} ***")

print(f"\nMini-simulation OK. Score: {state.score_str()}")
print(f"Evaluator corrections: {sim.evaluator.corrections_made}")
