"""
player_match — Style-Matchup Prediction Augmentation (isolated experiment).

A SEPARATE, isolated experiment (sibling to psycho_stress) that asks: do unit-vs-unit
STYLE MISMATCHES between two squads add predictive value BEYOND the scalar team-strength
gap the base model already uses? Built from the p2.txt player-characteristics framework,
restricted to the attributes we actually have (pace/shooting/passing/dribbling/defending/
physical + position) — the mental/stimulus traits in p2.txt live in the simulation, not
here, because they cannot be scored against match results.

Like psycho_stress, it imports predict.py / match_engine / backtest.py READ-ONLY and applies
its adjustment in probability (logit) space on top of the baseline W/D/L. Kept only if it
beats the baseline `pred` OUT-OF-SAMPLE (evaluate.py).

KEY CAVEAT: player_stats.csv holds *current* squads, so profiles are only valid for recent
games. The 2026 World Cup games are the honest test; the full history is anachronistic
(2026 squads vs 2014-era teams) and reported as low-confidence context only.
"""
