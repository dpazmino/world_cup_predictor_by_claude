"""
Style-matchup advantage: unit-vs-unit mismatches that the scalar strength gap misses.

All terms are differences of normalised (0..1) attributes, so the net is a small signed
number (+ favours home). It is applied as a logit shift on the baseline in augment.py.
The cross-unit terms (my attackers' pace vs your defenders' pace) are structurally
different from the mean-overall gap the base model already encodes — that is the residual
signal this experiment tests.
"""
from __future__ import annotations
from .profiles import profile

# sub-weights within the composite (fixed; a single K in augment scales the whole thing,
# so only one free parameter is tuned — keeping overfit risk low on small data)
W = {"pace_break": 1.0, "dribble_break": 1.0, "finish": 1.2,
     "midfield": 0.9, "physical": 0.7}


def _attacking_threat(att: dict, dfn: dict) -> float:
    """How much an attack unit out-matches a defence unit (normalised attr diffs)."""
    pace_break = att["pace"] - dfn["pace"]                 # fast forwards vs slow defenders
    dribble_break = att["dribbling"] - dfn["defending"]    # carriers vs tacklers
    finish = att["shooting"] - dfn["defending"]            # finishing vs resistance
    return (W["pace_break"] * pace_break + W["dribble_break"] * dribble_break
            + W["finish"] * finish)


def net_advantage(home: str, away: str) -> float | None:
    """Net style-matchup advantage to `home` (+) / `away` (-), or None if unrated."""
    ph, pa = profile(home), profile(away)
    if ph is None or pa is None:
        return None
    threat_home = _attacking_threat(ph["ATT"], pa["DEF"])
    threat_away = _attacking_threat(pa["ATT"], ph["DEF"])
    midfield = ph["MID"]["passing"] - pa["MID"]["passing"]
    physical = ph["team_physical"] - pa["team_physical"]
    net = (threat_home - threat_away) + W["midfield"] * midfield + W["physical"] * physical
    return net
