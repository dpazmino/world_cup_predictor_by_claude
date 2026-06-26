"""
Convert a public international-results CSV into data/results.csv format.

The manual dataset in data/results.csv is necessarily small. The scalable way to
reach thousands of matches is to ingest a verified results database — this script
targets the widely-used Kaggle dataset *"International football results from 1872
to <year>"* (Mart Jürisoo), whose columns are:

    date,home_team,away_team,home_score,away_score,tournament,city,country,neutral

It keeps only fixtures where BOTH teams have a squad in this repo (so ratings work),
maps a few name aliases, optionally filters by date / drops friendlies, and writes
our 6-column format: date,home,away,home_goals,away_goals,neutral.

Usage:
    python ingest_results.py path/to/results.csv --since 2014-01-01 --out data/ingested.csv
    python ingest_results.py path/to/results.csv --no-friendlies --append   # append to data/results.csv

After ingesting, rebuild the backtest cache:  python backtest.py -n 60
"""
from __future__ import annotations
import os
import csv
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_OUT = os.path.join(_HERE, "data", "results.csv")

# Map source spellings to this repo's team names (where they differ).
ALIASES = {
    "United States": "USA",
    "Korea Republic": "South Korea",
    "Korea DPR": None,                 # North Korea — not in repo
    "IR Iran": "Iran",
    "China PR": "China",
    "Côte d'Ivoire": "Ivory Coast",
    "Czechia": "Czech Republic",       # not in repo → dropped by has_squad
    "Cabo Verde": "Cape Verde",
}


def _canonical(name: str) -> str:
    return ALIASES.get(name.strip(), name.strip())


def has_squad(name: str) -> bool:
    """True if <name>_prompts.py exists (so the team has ratings/squad)."""
    if not name:
        return False
    fn = os.path.join(_HERE, name.lower().replace(" ", "_") + "_prompts.py")
    return os.path.exists(fn)


def ingest(src: str, since: str | None, drop_friendlies: bool) -> list[str]:
    out_lines, kept, seen = [], 0, 0
    with open(src, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            seen += 1
            date = (row.get("date") or "").strip()
            if since and date < since:
                continue
            if drop_friendlies and (row.get("tournament", "").strip().lower() == "friendly"):
                continue
            home = _canonical(row.get("home_team", ""))
            away = _canonical(row.get("away_team", ""))
            if not (home and away and has_squad(home) and has_squad(away)):
                continue
            try:
                hg = int(row["home_score"]); ag = int(row["away_score"])
            except (ValueError, KeyError, TypeError):
                continue
            neutral = 1 if str(row.get("neutral", "")).strip().lower() in ("true", "1", "yes") else 0
            out_lines.append(f"{date},{home},{away},{hg},{ag},{neutral}")
            kept += 1
    print(f"  read {seen} rows, kept {kept} (both teams have squads)")
    return out_lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="path to the public results CSV")
    ap.add_argument("--since", help="keep matches on/after this ISO date (e.g. 2014-01-01)")
    ap.add_argument("--no-friendlies", action="store_true", help="drop tournament==Friendly")
    ap.add_argument("--out", default=None, help="output file (default: print to stdout)")
    ap.add_argument("--append", action="store_true",
                    help=f"append to {_DEFAULT_OUT} instead of writing a new file")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        raise SystemExit(f"Source not found: {args.source}\n"
                         "Download e.g. the Kaggle 'International football results' results.csv.")

    lines = ingest(args.source, args.since, args.no_friendlies)

    if args.append:
        with open(_DEFAULT_OUT, "a", encoding="utf-8") as f:
            f.write("# --- ingested from " + os.path.basename(args.source) + " ---\n")
            f.write("\n".join(lines) + "\n")
        print(f"  appended {len(lines)} rows to {_DEFAULT_OUT}")
    elif args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("# date,home,away,home_goals,away_goals,neutral\n")
            f.write("\n".join(lines) + "\n")
        print(f"  wrote {len(lines)} rows to {args.out}")
    else:
        print("\n".join(lines[:20]))
        print(f"  ... ({len(lines)} rows; use --out or --append to save)")


if __name__ == "__main__":
    main()
