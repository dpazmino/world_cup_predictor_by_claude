"""
AI prediction-verifier agent.

After predict.py produces a forecast, this agent has an LLM review it for problems
— the prediction equivalent of evaluator_agent.py (which catches hallucinations in
the match sim). It does NOT re-predict; it audits the finished forecast for:

  * probability coherence (sum to 1, no absurd values),
  * data reliability (games logged, thin/unknown squads, wide rating intervals),
  * internal consistency (the favoured side should have the higher expected goals;
    scorelines should match xG),
  * agreement with the betting market when odds were supplied,
  * football plausibility (is this favourite / margin believable for these teams?).

Deterministic facts are gathered in code and handed to the model so its judgement is
grounded. Requires ANTHROPIC_API_KEY; degrades gracefully to "skipped" without one.
"""
from __future__ import annotations
import os

from .base_agent import llm_call, SMART_MODEL

_SYSTEM = """You are a senior football-analytics reviewer auditing a single match
prediction produced by a strength model (dynamic Elo, calibrated by shrinking toward a
base-rate prior, optionally anchored to betting odds). Your job is to VERIFY the forecast
and flag anything wrong, implausible, or unreliable — you do NOT re-predict.

Weigh these, using the facts provided:
- Probability coherence: the three outcomes should sum to ~100% and be sensible.
- Data reliability: few games logged, thin (<11) or missing squad data, or a very wide
  rating confidence interval all mean the forecast is weakly grounded — say so.
- Internal consistency: the team with the higher win probability should also have the
  higher expected goals; the modal scoreline should fit the expected goals.
- Market: if odds are given, large disagreement with the de-vigged market is a red flag
  (the market is usually right).
- Football plausibility: is the favourite and the margin believable for these specific
  teams? Call out anything that fails the smell test.

Be fair: if the forecast is reasonable and well-grounded, say so — do not invent problems.
Respond ONLY with JSON:
{
  "verdict": "sound" | "minor_issues" | "warning",
  "issues": [{"severity": "low|medium|high", "issue": "short label", "detail": "one sentence"}],
  "summary": "one sentence overall assessment"
}"""


def _facts(out: dict) -> str:
    """Gather grounded, deterministic facts about the prediction for the reviewer."""
    from ..ratings import team_rating, canonical_name
    from ..elo import default_model

    m = default_model()
    h, a = out["home"], out["away"]
    rh, ra = team_rating(h), team_rating(a)
    gh = m.games.get(canonical_name(h), 0)
    ga = m.games.get(canonical_name(a), 0)
    ph, pd, pa = out["p_home"], out["p_draw"], out["p_away"]

    fav_by_prob = h if ph > pa else a if pa > ph else "even"
    fav_by_xg = h if out["xg_home"] > out["xg_away"] else a if out["xg_away"] > out["xg_home"] else "even"

    L = [
        f"Fixture: {h} vs {a} ({'neutral venue' if out['neutral'] else h + ' at home'})",
        f"Final probabilities: {h} {100*ph:.0f}% / draw {100*pd:.0f}% / {a} {100*pa:.0f}% "
        f"(sum {100*(ph+pd+pa):.1f}%)",
    ]
    st = out.get("p_strength")
    if st:
        L.append(f"Pre-calibration strength probs: {100*st[0]:.0f}/{100*st[1]:.0f}/{100*st[2]:.0f}")
    if out.get("p_market"):
        mk = out["p_market"]
        L.append(f"De-vigged market: {100*mk[0]:.0f}/{100*mk[1]:.0f}/{100*mk[2]:.0f} "
                 f"(anchor weight {out.get('market_weight', 0):.2f})")
    L += [
        f"{h}: squad overall {rh['overall']:.0f} from {rh['n']} rated players, "
        f"Elo {m.rating(h):.0f}, {gh} real games logged",
        f"{a}: squad overall {ra['overall']:.0f} from {ra['n']} rated players, "
        f"Elo {m.rating(a):.0f}, {ga} real games logged",
        f"Rating 95% CI: {h} win {100*out['ci_home'][0]:.0f}-{100*out['ci_home'][1]:.0f}%, "
        f"{a} win {100*out['ci_away'][0]:.0f}-{100*out['ci_away'][1]:.0f}%",
        f"Expected goals: {h} {out['xg_home']:.2f} - {out['xg_away']:.2f} {a}",
        f"Favourite by probability: {fav_by_prob}; favourite by expected goals: {fav_by_xg}",
    ]
    if out.get("unknown"):
        L.append(f"NO rating data (default used) for: {', '.join(out['unknown'])}")
    thin = [t for t, r in ((h, rh), (a, ra)) if 0 < r["n"] < 11]
    if thin:
        L.append(f"THIN squad data (<11 rated players): {', '.join(thin)}")
    return "\n".join(L)


_TOURNAMENT_SYSTEM = """You are a senior football-analytics reviewer auditing a Monte-Carlo
World Cup forecast (per-team probabilities of winning the group, qualifying, reaching each
knockout round, and winning the tournament). You VERIFY the table — you do NOT re-run it.

Weigh these, using the facts provided:
- Coherence: title odds across all teams should sum to ~100%; for each team the round odds
  must be monotonically non-increasing (qualify >= R16 >= QF >= SF >= final >= win).
- Plausibility: the strongest teams (an Elo ranking is given) should sit near the top of the
  title odds. Flag any team whose title/qualify odds look too high or too low for its strength.
- Data reliability: thin-data teams (listed) appearing high up may be driven by noisy ratings.
- Host effect: hosts USA/Mexico/Canada should show a group-stage boost; note if it's missing.
- Monte-Carlo noise: if the 95% interval on the leaders is wide, precision is limited (raise N).

Be fair — if the forecast is coherent and plausible, say so. Respond ONLY with JSON:
{ "verdict": "sound" | "minor_issues" | "warning",
  "issues": [{"severity": "low|medium|high", "issue": "short label", "detail": "one sentence"}],
  "summary": "one sentence overall assessment" }"""

_THIN_TEAMS = {"Egypt", "Honduras", "Iran", "Iraq", "Qatar", "South Africa"}
_HOSTS = {"USA", "Mexico", "Canada"}


def _tournament_facts(tally: dict, n: int) -> str:
    from .scheduler_agent import GROUPS_2026
    from ..elo import default_model
    import math

    tg = {t: g for g, ts in GROUPS_2026.items() for t in ts}
    m = default_model()
    teams = list(tally)
    pct = lambda t, k: 100 * tally[t].get(k, 0) / n
    by_title = sorted(teams, key=lambda t: -tally[t].get("win", 0))

    # coherence
    title_sum = sum(pct(t, "win") for t in teams)
    order = ["qualify", "R16", "QF", "SF", "final", "win"]
    viol = [t for t in teams
            if any(tally[t].get(order[i], 0) < tally[t].get(order[i + 1], 0)
                   for i in range(len(order) - 1))]
    lead = tally[by_title[0]].get("win", 0) / n
    ci = 1.96 * math.sqrt(max(lead, 1e-9) * (1 - lead) / n) * 100

    L = [f"Simulations: {n}.  Sum of title odds across all teams: {title_sum:.1f}% "
         f"(should be ~100).  95% MC interval on the leader: +/-{ci:.1f}%.",
         f"Monotonicity violations (round odds increasing): {viol or 'none'}.",
         "", "Top 10 by title odds (team | group | title% | reach-final% | qualify%):"]
    for t in by_title[:10]:
        L.append(f"  {t:<14} {tg.get(t,'?'):>2}  {pct(t,'win'):5.1f}  "
                 f"{pct(t,'final'):5.1f}  {pct(t,'qualify'):5.1f}")
    L.append("\nElo ranking (strongest first) of the top 10 rated teams:")
    for t in sorted(teams, key=lambda t: -m.rating(t))[:10]:
        L.append(f"  {t:<14} Elo {m.rating(t):.0f}")
    L.append(f"\nHost nations (should get a group-stage boost): " +
             ", ".join(f"{t} title {pct(t,'win'):.1f}% / qualify {pct(t,'qualify'):.0f}%"
                       for t in by_title if t in _HOSTS))
    thin_present = [t for t in by_title if t in _THIN_TEAMS]
    if thin_present:
        L.append("Thin-data teams: " +
                 ", ".join(f"{t} title {pct(t,'win'):.1f}% / qualify {pct(t,'qualify'):.0f}%"
                           for t in thin_present))
    return "\n".join(L)


def verify_tournament(tally: dict, n: int) -> dict:
    """AI audit of a whole tournament forecast (tally from tournament.run)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"verdict": "skipped", "issues": [],
                "summary": "ANTHROPIC_API_KEY not set — AI verification skipped."}
    user = ("## TOURNAMENT FORECAST TO VERIFY\n" + _tournament_facts(tally, n) +
            "\n\nAudit this forecast and report any problems as specified.")
    res = llm_call(_TOURNAMENT_SYSTEM, user, model=SMART_MODEL, max_tokens=700, temperature=0.2)
    if not isinstance(res, dict) or "verdict" not in res:
        return {"verdict": "unavailable", "issues": [],
                "summary": "AI verification unavailable — the model call failed "
                           "(check Anthropic API credits, key, and network)."}
    res.setdefault("issues", [])
    res.setdefault("summary", "")
    return res


def verify(out: dict) -> dict:
    """Return the AI verifier's verdict for a prediction (dict from predict.predict)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"verdict": "skipped", "issues": [],
                "summary": "ANTHROPIC_API_KEY not set — AI verification skipped."}
    user = ("## PREDICTION TO VERIFY\n" + _facts(out) +
            "\n\nAudit this forecast and report any problems as specified.")
    res = llm_call(_SYSTEM, user, model=SMART_MODEL, max_tokens=600, temperature=0.2)
    if not isinstance(res, dict) or "verdict" not in res:
        return {"verdict": "unavailable", "issues": [],
                "summary": "AI verification unavailable — the model call failed "
                           "(check Anthropic API credits, key, and network)."}
    res.setdefault("issues", [])
    res.setdefault("summary", "")
    return res
