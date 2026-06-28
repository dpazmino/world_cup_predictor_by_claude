"""
latent_factor — Matrix-Factorization / Collaborative-Filtering Prediction Augmentation
(isolated experiment).

A SEPARATE, isolated experiment (sibling to player_match / psycho_stress) that asks the
recommendation-engine question directly: does a LOW-RANK LATENT-FACTOR model of the
results matrix — teams' k-dimensional attack/defence "taste" vectors, à la collaborative
filtering — capture NON-TRANSITIVE matchup structure that the scalar team-strength gap
(Elo) misses?

The framing matters:
  * Elo is rank-1 matrix factorization: one latent scalar (strength) per team, P(win) a
    function of the difference.
  * Dixon-Coles is 2-factor MF (latent attack + latent defence); already benchmarked
    (`backtest.py --dc`) and found NO better than Elo on this sparse data.
  * This experiment generalises that to k latent dimensions and isolates the *interaction
    residual* — the bilinear cross term <u_home, v_away> - <u_away, v_home> that a single
    overall (rank-1) cannot represent. k=0 degenerates exactly to "no shift" (pure
    strength), so the rank sweep is a clean test of "does k>0 buy anything?".

Like its siblings it imports predict.py / match_engine / backtest.py READ-ONLY and applies
its adjustment in probability (logit) space on top of the baseline `pred`. Kept only if it
beats the baseline OUT-OF-SAMPLE (evaluate.py, walk-forward + cross-validation).

LEAK-FREE BY CONSTRUCTION: unlike player_match/psycho_stress (whose signal comes from static
squad data), this signal is *learned from results*, so it MUST be fit walk-forward — at each
fixture the latent model has seen only strictly-earlier games (signal.py). Fitting on the
whole table then scoring it would leak; we never do that in evaluation.

PRIOR: every prior augmentation here (full sim, player_match, psycho_stress, coach_clash,
dense-data ingest) is an out-of-sample null because scalar strength already absorbs the
signal, and DC (=2-factor MF) was no better than Elo. The honest expectation is that this
is null too ON THIS SPARSE INTERNATIONAL DATA (~5-10 games/team). Latent-factor models earn
their keep on DENSE data (thousands of club games, many obs/team); the value of this package
is to *measure* that, and to be ready as a better Elo seed if dense data is ingested.
"""
