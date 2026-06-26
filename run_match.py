"""
Entry point for running a World Cup match simulation.

Usage:
    python run_match.py                          # Argentina vs Brazil (default)
    python run_match.py England Germany          # Custom teams
    python run_match.py --schedule group_A       # Run group A fixtures via scheduler

Requires: ANTHROPIC_API_KEY environment variable set.
"""
import os
import sys
import json
from pathlib import Path

# Load .env if present
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

def check_api_key():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("Set it with: $env:ANTHROPIC_API_KEY = 'your-key-here'  (PowerShell)")
        sys.exit(1)

def run_single_match(home: str, away: str, verbose: bool = True,
                     fast_mode: bool = False, knockout: bool = False,
                     offline: bool = False, ai=None) -> dict:
    from match_engine import MatchSimulation
    sim = MatchSimulation(home_team=home, away_team=away, verbose=verbose,
                          fast_mode=fast_mode, knockout=knockout,
                          offline=offline, ai=ai)
    return sim.run()

def run_tournament_group(group_name: str) -> None:
    """Run all fixtures in a group stage group."""
    from match_engine import MatchSimulation, SchedulerAgent
    scheduler = SchedulerAgent()
    scheduler.create_group_fixtures()

    # Filter fixtures for this group
    group_fixtures = [f for f in scheduler.fixtures
                      if f.group == group_name.upper()]

    if not group_fixtures:
        print(f"No fixtures found for group {group_name.upper()}")
        available = sorted(set(f.group for f in scheduler.fixtures if f.group))
        print(f"Available groups: {', '.join(available)}")
        return

    print(f"\n{'='*60}")
    print(f"  2026 WORLD CUP — GROUP {group_name.upper()}")
    print(f"{'='*60}")

    for fixture in group_fixtures:
        print(f"\nFixture {fixture.fixture_id}: {fixture.home_team} vs {fixture.away_team}")
        result = run_single_match(fixture.home_team, fixture.away_team, verbose=True)
        scheduler.record_result(
            fixture.fixture_id,
            result["score"]["home"],
            result["score"]["away"],
        )

    print(f"\n{'='*60}")
    print(f"  GROUP {group_name.upper()} FINAL STANDINGS")
    print(f"{'='*60}")
    scheduler.print_group_table(group_name.upper())


def main():
    args = sys.argv[1:]

    # --speed: rule-based off-ball positioning, AI kept for on-ball decisions.
    fast_mode = "--speed" in args
    if fast_mode:
        idx = args.index("--speed")
        args.pop(idx)
        if idx < len(args) and args[idx].replace(".", "", 1).isdigit():
            args.pop(idx)   # consume an optional number but don't use it

    # --knockout: a tie cannot stand — extra time then a penalty shootout.
    knockout = "--knockout" in args
    if knockout:
        args.remove("--knockout")

    # --offline: fully deterministic, ZERO LLM calls (no API key needed).
    offline = "--offline" in args
    if offline:
        args.remove("--offline")

    # --hybrid: AI ONLY for the on-ball decision (mimic the human choice), rules
    # for everything else — ~1 LLM call/play instead of ~22.
    hybrid = "--hybrid" in args
    if hybrid:
        args.remove("--hybrid")

    # --ai=decisions,coach : fine-grained control of which surfaces use the LLM.
    ai = None
    ai_arg = next((a for a in args if a.startswith("--ai=")), None)
    if ai_arg:
        args.remove(ai_arg)
        ai = {s.strip() for s in ai_arg.split("=", 1)[1].split(",") if s.strip()}
    elif hybrid:
        ai = {"decisions"}

    def announce():
        if offline:
            print("Offline mode — fully rule-based, no AI / no API calls.")
        elif ai is not None:
            print(f"Hybrid mode — AI surfaces: {sorted(ai)}; everything else rule-based.")
        elif fast_mode:
            print("Fast mode — AI on-ball decisions, rule-based positioning (~20x fewer LLM calls).")
        if knockout:
            print("Knockout mode ON — extra time + penalty shootout if level after 90'.")

    # An API key is only required when some surface actually uses the LLM.
    uses_ai = not offline and (ai is None or len(ai) > 0)
    if uses_ai:
        check_api_key()

    if "--schedule" in args:
        idx = args.index("--schedule")
        group = args[idx + 1] if idx + 1 < len(args) else "A"
        run_tournament_group(group)
        return

    home, away = (args[0], args[1]) if len(args) >= 2 else ("Argentina", "Brazil")
    if len(args) < 2:
        print("No teams specified — running default: Argentina vs Brazil")
    announce()
    result = run_single_match(home, away, fast_mode=fast_mode, knockout=knockout,
                              offline=offline, ai=ai)
    print("\n[Result JSON]")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
