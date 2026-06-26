"""
Environment data layer: venue table + per-fixture environment CSV → per-team stimuli.

`fixtures_env.csv` schema (one row per match; blanks fall back to neutral defaults):

  required : date, home, away, venue
  weather  : temp_c, humidity, wind_kph, rain_prob          (ignored for roofed venues)
  wbgt     : wbgt_c                                          (else estimated from temp+humidity)
  clock    : kickoff_local_hour
  per team : home_travel_km, away_travel_km,
             home_tz_cross, away_tz_cross,
             home_days_arr,  away_days_arr,
             home_rest_days, away_rest_days,
             home_prev_alt,  away_prev_alt,                  (metres; else venue altitude)
             home_prev_wbgt, away_prev_wbgt                  (prior-match heat carryover)
  crowd    : crowd_support (home|away|neutral), crowd_hostility (0..1)

`venue` must match a city or stadium in venues_2026.csv (case-insensitive).
"""
from __future__ import annotations
import csv
import math
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_VENUES = os.path.join(_HERE, "data", "venues_2026.csv")
_FIXTURES = os.path.join(_HERE, "data", "fixtures_env.csv")

BASELINE_REST = 4.0          # days; deficits below this drive recovery stress
INDOOR_WBGT = 21.5           # climate-controlled roofed venue


def _load_venues() -> dict:
    out = {}
    with open(_VENUES, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rec = {"altitude_m": float(row["altitude_m"]), "roof": row["roof"].strip(),
                   "utc_offset": float(row["utc_offset"]), "city": row["city"]}
            out[row["city"].strip().lower()] = rec
            out[row["stadium"].strip().lower()] = rec
    return out


VENUES = _load_venues()


def estimate_wbgt(temp_c: float, humidity: float) -> float:
    """Australian-BoM shade approximation of WBGT from air temp (°C) and RH (%)."""
    e = (humidity / 100.0) * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
    return 0.567 * temp_c + 0.393 * e + 3.94


def _f(row: dict, key: str, default: float) -> float:
    v = row.get(key, "")
    try:
        return float(v) if str(v).strip() != "" else default
    except ValueError:
        return default


def compute_features(row: dict) -> dict:
    """Augment a raw fixture row with venue + derived weather features."""
    v = VENUES.get(str(row.get("venue", "")).strip().lower())
    altitude = v["altitude_m"] if v else 0.0
    roof = v["roof"] if v else "open"
    indoor = roof in ("fixed", "retractable")   # climate-controlled in heat

    if indoor:
        wbgt, weather = INDOOR_WBGT, 0.0
    else:
        wbgt = _f(row, "wbgt_c", 0.0) or estimate_wbgt(_f(row, "temp_c", 22.0),
                                                       _f(row, "humidity", 50.0))
        rain = _f(row, "rain_prob", 0.0)          # 0..1
        wind = _f(row, "wind_kph", 0.0)
        weather = min(1.0, 0.6 * rain + 0.4 * min(1.0, wind / 40.0))
    return {**row, "altitude_m": altitude, "indoor": indoor, "wbgt": wbgt,
            "weather": weather}


def team_stimuli(feat: dict, side: str) -> dict:
    """Raw stress stimuli for one side ('home'|'away') of a featured fixture."""
    p = f"{side}_"
    venue_alt = feat["altitude_m"]
    prev_alt = _f(feat, p + "prev_alt", venue_alt)
    tz = _f(feat, p + "tz_cross", 0.0)
    days_arr = _f(feat, p + "days_arr", 5.0)
    rest = _f(feat, p + "rest_days", BASELINE_REST)
    prev_wbgt = _f(feat, p + "prev_wbgt", 0.0)

    support = str(feat.get("crowd_support", "neutral")).strip().lower()
    hostility = _f(feat, "crowd_hostility", 0.0)
    sensory = 0.0 if support == side else hostility

    heat_carry = max(0.0, prev_wbgt - 22.0) / 6.0      # prior-match heat fatigue
    return {
        "wbgt": feat["wbgt"],
        "alt_shock": abs(venue_alt - prev_alt),
        "travel_km": _f(feat, p + "travel_km", 0.0),
        "circadian": max(0.0, tz - days_arr),
        "sensory": sensory,
        "recovery_deficit": max(0.0, BASELINE_REST - rest) + heat_carry,
        "weather": feat["weather"],
    }


def load_fixtures(path: str = _FIXTURES) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [compute_features(r) for r in csv.DictReader(f)]
