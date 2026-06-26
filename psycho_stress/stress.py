"""
Psychophysics primitives + the per-team composite match-stress.

The "psychophysics angle": a raw environmental stimulus (heat, noise, altitude) is
not felt linearly. It is transformed into *perceived* load by a perceptual law:

  * Weber–Fechner   perceived = k·ln(stimulus / threshold)   — diminishing returns,
                    with a just-noticeable-difference (JND) threshold below which
                    the stimulus is not felt at all.
  * Stevens' power  perceived = k·stimulus^n                 — an alternative whose
                    exponent n < 1 (compressive) or > 1 (expansive) per modality.
  * Yerkes–Dodson   an inverted-U: moderate arousal *helps* performance, while too
                    little or too much *hurts* it — used for crowd/pressure arousal.

This module is pure perception: it knows nothing about a specific team's traits
(that lives in team_profile.py). It turns a dict of raw component stimuli into a
dict of perceived-stress magnitudes.
"""
from __future__ import annotations
import math

# ── primitives ────────────────────────────────────────────────────────────────

def jnd(stimulus: float, threshold: float) -> float:
    """Stimulus magnitude above the just-noticeable-difference threshold (else 0)."""
    return max(0.0, stimulus - threshold)


def weber_fechner(stimulus: float, threshold: float, k: float = 1.0) -> float:
    """Perceived intensity k·ln(stimulus/threshold); 0 at/below threshold."""
    if stimulus <= threshold or threshold <= 0:
        return 0.0
    return k * math.log(stimulus / threshold)


def stevens(stimulus: float, n: float = 1.0, k: float = 1.0) -> float:
    """Stevens' power law: k·stimulus^n (compressive n<1, expansive n>1)."""
    return k * (max(0.0, stimulus) ** n)


def yerkes_dodson(arousal: float, optimum: float = 0.45, width: float = 0.28) -> float:
    """
    Inverted-U performance modifier in [-1, +1]. +1 at the optimum arousal level,
    falling toward -1 when arousal is far below or above it. `arousal` in [0, 1].
    Returned as a *performance* modifier (positive = helps), so callers negate it
    to express it as stress.
    """
    bump = math.exp(-((arousal - optimum) ** 2) / (2.0 * width * width))
    return 2.0 * bump - 1.0


# ── component thresholds / coefficients (literature-informed defaults) ──────────
# Tunable, but kept few: n≈59 validation games → high overfit risk, so we lean on
# priors from pyscho.txt rather than fitting many free parameters.
DEFAULT_CFG = {
    # heat: WBGT (°C). ~22 WBGT is the comfort/JND floor; cooling-break threshold ~28.
    "heat_threshold": 22.0,   "heat_k": 1.00,
    # altitude shock: metres of altitude change from prior venue. JND ~600 m.
    "alt_threshold": 600.0,   "alt_k": 0.55,   "alt_n": 0.80,
    # travel: km since prior match. JND ~800 km (a short hop is negligible).
    "travel_threshold": 800.0, "travel_k": 0.40, "travel_n": 0.70,
    # circadian disruption: (timezones crossed − days since arrival), hours. JND ~1.
    "circadian_threshold": 1.0, "circadian_k": 0.50,
    # sensory: crowd hostility/noise index 0..1 for the non-supported team.
    "sensory_k": 0.45, "sensory_arousal_opt": 0.45,
    # recovery deficit: (baseline_rest − rest_days) + prior heat carryover. JND 0.
    "recovery_k": 0.35,
    # weather disruption (rain/wind) index 0..1 — small, symmetric.
    "weather_k": 0.20,
}

COMPONENTS = ("heat", "altitude", "travel", "circadian", "sensory", "recovery", "weather")


def perceived_stress(stimuli: dict, cfg: dict | None = None) -> dict:
    """
    Map raw component stimuli (from env_data.team_stimuli) → perceived stress per
    component (all ≥ 0, in abstract "stress units"). Indoor venues damp the
    weather-driven components upstream (env_data sets those stimuli to ~0).
    """
    c = {**DEFAULT_CFG, **(cfg or {})}
    out = {}
    out["heat"] = weber_fechner(stimuli.get("wbgt", 0.0), c["heat_threshold"], c["heat_k"])
    out["altitude"] = stevens(jnd(stimuli.get("alt_shock", 0.0), c["alt_threshold"]) / 1000.0,
                              n=c["alt_n"], k=c["alt_k"])
    out["travel"] = stevens(jnd(stimuli.get("travel_km", 0.0), c["travel_threshold"]) / 1000.0,
                            n=c["travel_n"], k=c["travel_k"])
    out["circadian"] = weber_fechner(stimuli.get("circadian", 0.0) + c["circadian_threshold"],
                                     c["circadian_threshold"], c["circadian_k"])
    # sensory: hostility as arousal → negate the Yerkes–Dodson performance modifier
    host = stimuli.get("sensory", 0.0)
    out["sensory"] = max(0.0, -yerkes_dodson(host, c["sensory_arousal_opt"])) * c["sensory_k"]
    out["recovery"] = c["recovery_k"] * max(0.0, stimuli.get("recovery_deficit", 0.0))
    out["weather"] = c["weather_k"] * max(0.0, stimuli.get("weather", 0.0))
    return out
