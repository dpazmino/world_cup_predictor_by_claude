"""
psycho_stress — Psychophysical Match Stress Model (augmentation layer).

A SEPARATE, isolated experiment that tries to *augment* the existing strength
predictor with environmental + perceptual stress, WITHOUT changing any production
prediction code. Everything here imports `predict.py` / `match_engine` / `backtest.py`
read-only and post-processes their output.

Core idea (from downloads/pyscho.txt): environment does not predict the winner
directly — it predicts how much each team's *normal* performance degrades under
stress, and teams differ in vulnerability. So:

    augmented_strength(team) = base_strength(team)
                               - vulnerability · perceived_stress
                               + adaptation

The augmented strength gap is fed back through the existing W/D/L pipeline.

Kept only if it beats the baseline `pred` model out-of-sample (see evaluate.py).
"""
