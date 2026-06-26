"""
Generate psycho_stress/data/fixtures_env.csv from the logged WC2026 results, using the
REAL 2026 venue for every fixture (data/match_venues.csv, ESPN schedule).

Because the repo follows the real draw + schedule, every game maps to a genuine venue, so
we attach real signal to ALL of them — not just host games:

  * heat      — that city's June climate normal (host_climate.csv); roofed venues damped
  * altitude  — venue elevation + each team's altitude *change* from its previous venue
                (first game: from the team's home altitude), with adaptation in augment.py
  * travel    — great-circle km between a team's consecutive venues (venue lat/lon)
  * circadian — timezone offset crossed vs days since the previous match
  * recovery  — rest days computed from the actual fixture sequence

Run after logging new results:  python -m psycho_stress.build_env
"""
from __future__ import annotations
import csv
import datetime
import math
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_RESULTS = os.path.join(_ROOT, "data", "results.csv")
_OUT = os.path.join(_HERE, "data", "fixtures_env.csv")
HOSTS = {"USA", "Mexico", "Canada"}
WC_START = "2026-06-11"


def _decomment(f):
    for line in f:
        if line.strip() and not line.lstrip().startswith("#"):
            yield line


def _rows(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(_decomment(f)))


def _haversine(a, b):
    (la1, lo1), (la2, lo2) = a, b
    r = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _load_venue_geo():
    geo = {}
    for r in _rows(os.path.join(_HERE, "data", "venues_2026.csv")):
        geo[r["city"]] = {"alt": float(r["altitude_m"]), "utc": float(r["utc_offset"]),
                          "lat": float(r["lat"]), "lon": float(r["lon"])}
    return geo


def _load_match_venues():
    """Return (by_date_pair, by_pair). Group-stage pairs are unique, so the pair-only
    index recovers fixtures whose logged date differs from the real schedule by a day."""
    by_date_pair, by_pair = {}, {}
    for r in _rows(os.path.join(_HERE, "data", "match_venues.csv")):
        pair = frozenset((r["team_a"].strip(), r["team_b"].strip()))
        by_date_pair[(r["date"], pair)] = r["city"].strip()
        by_pair[pair] = r["city"].strip()
    return by_date_pair, by_pair


def _load_results():
    games = []
    with open(_RESULTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split(",")
            if len(p) < 5 or p[0] < WC_START:
                continue
            games.append({"date": p[0], "home": p[1].strip(), "away": p[2].strip()})
    games.sort(key=lambda g: g["date"])
    return games


def build():
    geo = _load_venue_geo()
    mv, mv_pair = _load_match_venues()
    climate = {r["city"]: r for r in _rows(os.path.join(_HERE, "data", "host_climate.csv"))}
    home_alt = {r["team"]: float(r["altitude_m"])
                for r in _rows(os.path.join(_HERE, "data", "team_home_altitude.csv"))}
    games = _load_results()

    prev: dict = {}          # team -> (date, city)
    missing = []
    rows = []
    for g in games:
        home, away, date = g["home"], g["away"], g["date"]
        pair = frozenset((home, away))
        city = mv.get((date, pair)) or mv_pair.get(pair)
        if city is None or city not in geo:
            missing.append((date, home, away))
            continue
        cl = climate.get(city, {})
        v = geo[city]

        def team_fields(team):
            d = datetime.date.fromisoformat(date)
            pdate, pcity = prev.get(team, (None, None))
            if pdate is None:
                rest, travel, tz, palt, days = 6, 0.0, 0.0, home_alt.get(team, 100.0), 7
            else:
                rest = max(1, min(14, (d - pdate).days))
                pv = geo[pcity]
                travel = _haversine((pv["lat"], pv["lon"]), (v["lat"], v["lon"]))
                tz = abs(v["utc"] - pv["utc"])
                palt = pv["alt"]
                days = rest
            return {"rest_days": rest, "travel_km": round(travel), "tz_cross": tz,
                    "prev_alt": palt, "days_arr": days}

        hf, af = team_fields(home), team_fields(away)
        host = home if home in HOSTS else (away if away in HOSTS else None)
        row = {"date": date, "home": home, "away": away, "venue": city,
               "temp_c": cl.get("temp_c", ""), "humidity": cl.get("humidity", ""),
               "wind_kph": cl.get("wind_kph", ""), "rain_prob": cl.get("rain_prob", ""),
               "crowd_support": "home" if host == home else ("away" if host == away else "neutral"),
               "crowd_hostility": 0.50 if host else 0.18}
        for side, ff in (("home", hf), ("away", af)):
            for k, val in ff.items():
                row[f"{side}_{k}"] = val
        rows.append(row)
        prev[home] = prev[away] = (datetime.date.fromisoformat(date), city)

    cols = ["date", "home", "away", "venue", "temp_c", "humidity", "wind_kph", "rain_prob",
            "home_travel_km", "away_travel_km", "home_tz_cross", "away_tz_cross",
            "home_days_arr", "away_days_arr", "home_rest_days", "away_rest_days",
            "home_prev_alt", "away_prev_alt", "crowd_support", "crowd_hostility"]
    with open(_OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {_OUT}: {len(rows)} fixtures with real venues.")
    if missing:
        print(f"  WARNING: no venue match for {len(missing)}: {missing[:5]}")


if __name__ == "__main__":
    build()
