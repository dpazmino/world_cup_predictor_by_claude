"""
Fetch real player stats from EA Sports FC 25 Kaggle dataset and merge
into player_stats.csv for all 1,248 World Cup agents.

STEP 1 — Install requirements (run once):
    pip install kaggle pandas

STEP 2 — Set up Kaggle credentials:
    a) Go to https://www.kaggle.com/settings/account
    b) Click "Create New API Token" — downloads kaggle.json
    c) Place kaggle.json at:   C:\\Users\\david\\.kaggle\\kaggle.json

STEP 3 — Run this script:
    python -X utf8 fetch_player_stats.py

What it does:
    1. Downloads the EA FC 25 dataset from Kaggle (~50 MB)
    2. Loads all 1,248 player names from your prompt files
    3. Fuzzy-matches each name to the Kaggle database (handles accents,
       alternate spellings, name order differences)
    4. Rewrites player_stats.csv with real stats for every matched player
    5. Prints a summary of matched vs unmatched players
"""
import sys
import csv
import importlib
import unicodedata
import difflib
from pathlib import Path

# ── Try imports ──────────────────────────────────────────────────────────────
try:
    import pandas as pd
except ImportError:
    sys.exit("ERROR: pandas not installed. Run:  pip install pandas")

try:
    import kaggle
except ImportError:
    sys.exit("ERROR: kaggle not installed. Run:  pip install kaggle")

CSV_OUT      = Path("player_stats.csv")
DATASET      = "nyagami/ea-sports-fc-25-database-ratings-and-stats"
DOWNLOAD_DIR = Path("kaggle_data")

# ── All 48 World Cup teams ────────────────────────────────────────────────────
ALL_TEAMS = [
    ("Argentina",    "argentina_prompts",    "ARGENTINA_PROMPTS"),
    ("Brazil",       "brazil_prompts",       "BRAZIL_PROMPTS"),
    ("France",       "france_prompts",       "FRANCE_PROMPTS"),
    ("England",      "england_prompts",      "ENGLAND_PROMPTS"),
    ("Germany",      "germany_prompts",      "GERMANY_PROMPTS"),
    ("Spain",        "spain_prompts",        "SPAIN_PROMPTS"),
    ("Portugal",     "portugal_prompts",     "PORTUGAL_PROMPTS"),
    ("Netherlands",  "netherlands_prompts",  "NETHERLANDS_PROMPTS"),
    ("USA",          "usa_prompts",          "USA_PROMPTS"),
    ("Croatia",      "croatia_prompts",      "CROATIA_PROMPTS"),
    ("Belgium",      "belgium_prompts",      "BELGIUM_PROMPTS"),
    ("Mexico",       "mexico_prompts",       "MEXICO_PROMPTS"),
    ("Canada",       "canada_prompts",       "CANADA_PROMPTS"),
    ("Senegal",      "senegal_prompts",      "SENEGAL_PROMPTS"),
    ("Japan",        "japan_prompts",        "JAPAN_PROMPTS"),
    ("Morocco",      "morocco_prompts",      "MOROCCO_PROMPTS"),
    ("South Korea",  "south_korea_prompts",  "SOUTH_KOREA_PROMPTS"),
    ("Uruguay",      "uruguay_prompts",      "URUGUAY_PROMPTS"),
    ("Colombia",     "colombia_prompts",     "COLOMBIA_PROMPTS"),
    ("Denmark",      "denmark_prompts",      "DENMARK_PROMPTS"),
    ("Switzerland",  "switzerland_prompts",  "SWITZERLAND_PROMPTS"),
    ("Austria",      "austria_prompts",      "AUSTRIA_PROMPTS"),
    ("Poland",       "poland_prompts",       "POLAND_PROMPTS"),
    ("Serbia",       "serbia_prompts",       "SERBIA_PROMPTS"),
    ("Turkey",       "turkey_prompts",       "TURKEY_PROMPTS"),
    ("Scotland",     "scotland_prompts",     "SCOTLAND_PROMPTS"),
    ("Ecuador",      "ecuador_prompts",      "ECUADOR_PROMPTS"),
    ("Nigeria",      "nigeria_prompts",      "NIGERIA_PROMPTS"),
    ("Italy",        "italy_prompts",        "ITALY_PROMPTS"),
    ("Australia",    "australia_prompts",    "AUSTRALIA_PROMPTS"),
    ("Ivory Coast",  "ivory_coast_prompts",  "IVORY_COAST_PROMPTS"),
    ("Romania",      "romania_prompts",      "ROMANIA_PROMPTS"),
    ("Venezuela",    "venezuela_prompts",    "VENEZUELA_PROMPTS"),
    ("Panama",       "panama_prompts",       "PANAMA_PROMPTS"),
    ("Egypt",        "egypt_prompts",        "EGYPT_PROMPTS"),
    ("Saudi Arabia", "saudi_arabia_prompts", "SAUDI_ARABIA_PROMPTS"),
    ("Honduras",     "honduras_prompts",     "HONDURAS_PROMPTS"),
    ("Jamaica",      "jamaica_prompts",      "JAMAICA_PROMPTS"),
    ("Cameroon",     "cameroon_prompts",     "CAMEROON_PROMPTS"),
    ("Algeria",      "algeria_prompts",      "ALGERIA_PROMPTS"),
    ("Tunisia",      "tunisia_prompts",      "TUNISIA_PROMPTS"),
    ("South Africa", "south_africa_prompts", "SOUTH_AFRICA_PROMPTS"),
    ("Iran",         "iran_prompts",         "IRAN_PROMPTS"),
    ("Qatar",        "qatar_prompts",        "QATAR_PROMPTS"),
    ("China",        "china_prompts",        "CHINA_PROMPTS"),
    ("Iraq",         "iraq_prompts",         "IRAQ_PROMPTS"),
    ("New Zealand",  "new_zealand_prompts",  "NEW_ZEALAND_PROMPTS"),
    ("Ghana",        "ghana_prompts",        "GHANA_PROMPTS"),
]


def _normalize(name: str) -> str:
    """Lowercase, strip accents, remove punctuation for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return stripped.lower().strip()


def _load_all_prompt_players() -> list[tuple[str, str]]:
    """Returns list of (player_name, team_name) for all 1,248 players."""
    sys.path.insert(0, str(Path(__file__).parent))
    players = []
    for team_name, mod_name, dict_name in ALL_TEAMS:
        try:
            mod = importlib.import_module(mod_name)
            prompts = getattr(mod, dict_name, {})
            for pname in prompts:
                players.append((pname, team_name))
        except (ModuleNotFoundError, AttributeError):
            print(f"  [WARN] Could not load {mod_name}")
    return players


def _download_kaggle(dataset: str, dest: Path) -> Path:
    """Download and unzip kaggle dataset. Returns path to CSV file."""
    dest.mkdir(exist_ok=True)
    print(f"Downloading Kaggle dataset: {dataset} ...")
    import kaggle.api
    api = kaggle.api
    api.authenticate()
    api.dataset_download_files(dataset, path=str(dest), unzip=True, quiet=False)

    # Find the main CSV (largest file)
    csvs = sorted(dest.glob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)
    if not csvs:
        sys.exit("ERROR: No CSV found in downloaded dataset.")
    print(f"Using: {csvs[0].name}")
    return csvs[0]


# Known name differences between our prompts and the Kaggle dataset
NAME_ALIASES = {
    "vinicius jr":          "vini jr.",
    "gabriel magalhães":    "gabriel",
    "guilherme arana":      "arana",
    "álvaro morata":        "morata",
    "memphis depay":        "memphis",
    "gio reyna":            "giovanni reyna",
    "ivan perišić":         "ivan perisic",
    "wojciech szczęsny":    "wojciech szczesny",
    "yusuf yazici":         "yusuf yazıcı",
    "romain saïss":         "romain saiss",
    "hannibal mejbri":      "hannibal",
    "alireza jahanbakhsh":  "jahanbakhsh",
    "sardar azmoun":        "azmoun",
    "almoez ali":           "almoez ali",
    "akram afif":           "akram afif",
    "così così":            "cosicosi",
    "gonzalo plata":        "plata",
    "romell quioto":        "quioto",
    "alberth elis":         "elis",
    "agustín álvarez martínez": "agustin alvarez",
    "nicolás de la cruz":   "nicolas de la cruz",
    "eric maxim choupo-moting": "choupo-moting",
    "oghenekaro etebo":     "etebo",
    "serge aurier":         "aurier",
    "sofiane feghouli":     "feghouli",
    "islam slimani":        "slimani",
    "percy tau":            "tau",
    "bongani zungu":        "zungu",
    "mehdi taremi":         "taremi",
}


def _find_best_match(name: str, norm_name: str,
                     kaggle_index: dict[str, dict]) -> dict | None:
    """
    Try exact match first, then fuzzy match.
    kaggle_index: {normalized_name -> row_dict}
    """
    # 0. Check alias table first
    alias = NAME_ALIASES.get(norm_name)
    if alias and alias in kaggle_index:
        return kaggle_index[alias]

    # 1. Exact normalized match
    if norm_name in kaggle_index:
        return kaggle_index[norm_name]

    # 2. Fuzzy match
    candidates = difflib.get_close_matches(norm_name, kaggle_index.keys(),
                                            n=1, cutoff=0.82)
    if candidates:
        return kaggle_index[candidates[0]]

    # 3. Last-name only if unique
    parts = norm_name.split()
    if len(parts) > 1:
        last = parts[-1]
        last_matches = [k for k in kaggle_index if k.endswith(" " + last) or k == last]
        if len(last_matches) == 1:
            return kaggle_index[last_matches[0]]

    return None


def _infer_position(kaggle_row: dict) -> str:
    # Try all possible position column names from the Kaggle dataset
    pos = (kaggle_row.get("Position") or kaggle_row.get("position") or
           kaggle_row.get("best_position") or kaggle_row.get("player_positions") or "CM")
    pos = str(pos).split(",")[0].strip().upper()
    pos_map = {"LWF": "LW", "RWF": "RW", "AMF": "CAM", "DMF": "CDM", "SS": "ST"}
    return pos_map.get(pos, pos) if pos else "CM"


def main():
    # ── 1. Use cached download if available, otherwise fetch ────────────────
    existing_csvs = sorted(DOWNLOAD_DIR.glob("*.csv"), key=lambda p: p.stat().st_size, reverse=True) if DOWNLOAD_DIR.exists() else []
    if existing_csvs:
        kaggle_csv = existing_csvs[0]
        print(f"Using cached dataset: {kaggle_csv.name}")
    else:
        kaggle_csv = _download_kaggle(DATASET, DOWNLOAD_DIR)

    # ── 2. Load Kaggle data ──────────────────────────────────────────────────
    print("Loading Kaggle data ...")
    df = pd.read_csv(kaggle_csv, low_memory=False)
    print(f"  {len(df)} players in Kaggle dataset")
    print(f"  Columns: {list(df.columns[:20])}")

    # Detect column names — handles both full names and short codes (OVR/PAC/SHO etc.)
    col_map = {}
    for col in df.columns:
        cl = col.lower().strip()
        # Name columns
        if cl in ("name", "short_name"):
            col_map.setdefault("name", col)
        if cl in ("long_name", "full_name"):
            col_map.setdefault("long_name", col)
        # Ratings — full names and EA short codes
        if cl in ("overall", "ova", "overall_rating", "ovr"):
            col_map.setdefault("overall", col)
        if cl in ("pace", "pac", "speed"):
            col_map.setdefault("pace", col)
        if cl in ("shooting", "sho"):
            col_map.setdefault("shooting", col)
        if cl in ("passing", "pas"):
            col_map.setdefault("passing", col)
        if cl in ("dribbling", "dri"):
            col_map.setdefault("dribbling", col)
        if cl in ("defending", "def"):
            col_map.setdefault("defending", col)
        if cl in ("physic", "physical", "phy"):
            col_map.setdefault("physical", col)
        if cl in ("position", "positions", "best_position", "player_positions"):
            col_map.setdefault("position", col)

    required = ["name", "overall", "pace", "shooting", "passing", "dribbling", "defending", "physical"]
    for req in required:
        if req not in col_map:
            print(f"  [WARN] Column '{req}' not found. Available: {list(df.columns)}")

    name_col = col_map.get("long_name", col_map.get("name", ""))
    if not name_col:
        sys.exit("ERROR: Cannot find player name column in Kaggle CSV.")

    # ── 3. Build normalized index ────────────────────────────────────────────
    kaggle_index: dict[str, dict] = {}
    for _, row in df.iterrows():
        raw_name = str(row.get(name_col, ""))
        norm = _normalize(raw_name)
        if norm:
            kaggle_index[norm] = row.to_dict()
        # Also index short_name if different
        if "name" in col_map and col_map["name"] != name_col:
            short = _normalize(str(row.get(col_map["name"], "")))
            if short and short not in kaggle_index:
                kaggle_index[short] = row.to_dict()

    print(f"  Index built: {len(kaggle_index)} unique names")

    # ── 4. Load all World Cup players ────────────────────────────────────────
    print("Loading World Cup player roster ...")
    wc_players = _load_all_prompt_players()
    print(f"  {len(wc_players)} players across 48 teams")

    # ── 5. Match and build output rows ───────────────────────────────────────
    fieldnames = ["name", "team", "position", "overall", "pace", "shooting",
                  "passing", "dribbling", "defending", "physical"]

    matched, unmatched = [], []
    for pname, team in wc_players:
        norm = _normalize(pname)
        row = _find_best_match(pname, norm, kaggle_index)
        if row:
            def _get(attr, default=70):
                c = col_map.get(attr)
                if c and c in row:
                    try:
                        return int(float(row[c]))
                    except (ValueError, TypeError):
                        pass
                return default

            pos = _infer_position(row)
            matched.append({
                "name":      pname,
                "team":      team,
                "position":  pos,
                "overall":   _get("overall", 75),
                "pace":      _get("pace", 70),
                "shooting":  _get("shooting", 60),
                "passing":   _get("passing", 70),
                "dribbling": _get("dribbling", 65),
                "defending": _get("defending", 55),
                "physical":  _get("physical", 68),
            })
        else:
            unmatched.append((pname, team))

    # ── 6. Write CSV ─────────────────────────────────────────────────────────
    with open(CSV_OUT, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(matched)

    print(f"\n{'='*60}")
    print(f"  Matched:   {len(matched)}/{len(wc_players)} players")
    print(f"  Written to: {CSV_OUT}")

    if unmatched:
        print(f"\n  Unmatched ({len(unmatched)}) — will use keyword fallback:")
        for name, team in unmatched:
            print(f"    {team:<20} {name}")

    print(f"\nDone. Run your match:  python -X utf8 run_match.py Argentina Brazil")


if __name__ == "__main__":
    main()
