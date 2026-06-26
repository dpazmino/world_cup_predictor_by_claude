"""
Generate FIFA-style stats for every player NOT yet in player_stats.csv.
Reads each team's _prompts.py, estimates stats from role + prompt keywords,
and appends to player_stats.csv.

Run once:  python generate_missing_stats.py
"""
import sys, csv, importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

CSV_PATH = Path("player_stats.csv")
PROMPTS_DIR = Path(__file__).parent

# ── Role-based FIFA stat baselines (0-99 scale) ────────────────────────────
ROLE_BASES = {
    "GK":  dict(overall=76, pace=48, shooting=22, passing=62, dribbling=40, defending=20, physical=72),
    "CB":  dict(overall=78, pace=68, shooting=38, passing=68, dribbling=52, defending=80, physical=80),
    "RB":  dict(overall=76, pace=78, shooting=58, passing=72, dribbling=68, defending=72, physical=70),
    "LB":  dict(overall=76, pace=78, shooting=58, passing=72, dribbling=68, defending=72, physical=70),
    "RWB": dict(overall=75, pace=80, shooting=56, passing=70, dribbling=70, defending=68, physical=70),
    "LWB": dict(overall=75, pace=80, shooting=56, passing=70, dribbling=70, defending=68, physical=70),
    "CDM": dict(overall=78, pace=70, shooting=58, passing=78, dribbling=66, defending=80, physical=80),
    "CM":  dict(overall=78, pace=72, shooting=68, passing=80, dribbling=72, defending=64, physical=74),
    "CAM": dict(overall=78, pace=74, shooting=74, passing=80, dribbling=78, defending=46, physical=66),
    "RM":  dict(overall=76, pace=82, shooting=70, passing=74, dribbling=76, defending=44, physical=68),
    "LM":  dict(overall=76, pace=82, shooting=70, passing=74, dribbling=76, defending=44, physical=68),
    "RW":  dict(overall=77, pace=84, shooting=72, passing=74, dribbling=78, defending=40, physical=66),
    "LW":  dict(overall=77, pace=84, shooting=72, passing=74, dribbling=78, defending=40, physical=66),
    "CF":  dict(overall=77, pace=78, shooting=78, passing=70, dribbling=72, defending=38, physical=74),
    "ST":  dict(overall=77, pace=78, shooting=78, passing=70, dribbling=72, defending=38, physical=74),
}
DEFAULT_BASE = ROLE_BASES["CM"]

# ── Keyword modifiers (applied to all stats as a flat bonus) ───────────────
ELITE_BONUS   = [("world-class", 6), ("generational", 7), ("best in the world", 8),
                 ("ballon d'or", 7), ("legendary", 6), ("elite", 4), ("extraordinary", 5)]
GOOD_BONUS    = [("exceptional", 3), ("outstanding", 3), ("clinical", 2),
                 ("precise", 2), ("technical", 2), ("creative", 2), ("prolific", 3),
                 ("accomplished", 2), ("composed", 2)]
WEAK_PENALTY  = [("inconsistent", -3), ("raw talent", -4), ("still developing", -4),
                 ("erratic", -3), ("struggles under pressure", -3)]

# Attribute-specific bonuses
PACE_BONUS    = [("explosive pace", 6), ("blistering speed", 7), ("lightning-fast", 7),
                 ("pace", 3), ("burst of speed", 5), ("rapid", 4)]
SHOOT_BONUS   = [("lethal finisher", 6), ("clinical in front of goal", 6),
                 ("powerful shot", 5), ("eye for goal", 5), ("goalscoring instinct", 5),
                 ("natural finisher", 5), ("deadly", 4)]
PASS_BONUS    = [("pinpoint delivery", 5), ("vision", 4), ("through ball", 4),
                 ("precise passing", 5), ("dictates tempo", 5), ("range of passing", 4),
                 ("creative passing", 4), ("orchestrates", 4)]
DRIBBLE_BONUS = [("silky dribbler", 6), ("beats defenders", 5), ("close control", 5),
                 ("elusive", 5), ("quicksilver", 5), ("nimble", 4), ("electrifying", 5)]
DEFEND_BONUS  = [("reading the game", 4), ("intercept", 4), ("tenacious", 4),
                 ("aggressive press", 4), ("win the ball", 4), ("positional awareness", 4),
                 ("combative", 3), ("defensive", 3)]
PHYSICAL_BONUS= [("commanding presence", 4), ("powerful", 4), ("dominant in the air", 5),
                 ("aerial", 4), ("physical", 3), ("robust", 4), ("aggressive", 3)]

# Map position string in formation roles to FIFA position label
ROLE_TO_POS = {
    "GK": "GK", "CB": "CB", "RB": "RB", "LB": "LB",
    "RWB": "RWB", "LWB": "LWB",
    "CDM": "CDM", "CM": "CM", "CAM": "CAM",
    "RM": "RM", "LM": "LM",
    "RW": "RW", "LW": "LW",
    "CF": "ST", "ST": "ST",
}

ALL_TEAMS = [
    ("Argentina", "argentina_prompts", "ARGENTINA_PROMPTS"),
    ("Brazil", "brazil_prompts", "BRAZIL_PROMPTS"),
    ("France", "france_prompts", "FRANCE_PROMPTS"),
    ("England", "england_prompts", "ENGLAND_PROMPTS"),
    ("Germany", "germany_prompts", "GERMANY_PROMPTS"),
    ("Spain", "spain_prompts", "SPAIN_PROMPTS"),
    ("Portugal", "portugal_prompts", "PORTUGAL_PROMPTS"),
    ("Netherlands", "netherlands_prompts", "NETHERLANDS_PROMPTS"),
    ("USA", "usa_prompts", "USA_PROMPTS"),
    ("Croatia", "croatia_prompts", "CROATIA_PROMPTS"),
    ("Belgium", "belgium_prompts", "BELGIUM_PROMPTS"),
    ("Mexico", "mexico_prompts", "MEXICO_PROMPTS"),
    ("Canada", "canada_prompts", "CANADA_PROMPTS"),
    ("Senegal", "senegal_prompts", "SENEGAL_PROMPTS"),
    ("Japan", "japan_prompts", "JAPAN_PROMPTS"),
    ("Morocco", "morocco_prompts", "MOROCCO_PROMPTS"),
    ("South Korea", "south_korea_prompts", "SOUTH_KOREA_PROMPTS"),
    ("Uruguay", "uruguay_prompts", "URUGUAY_PROMPTS"),
    ("Colombia", "colombia_prompts", "COLOMBIA_PROMPTS"),
    ("Denmark", "denmark_prompts", "DENMARK_PROMPTS"),
    ("Switzerland", "switzerland_prompts", "SWITZERLAND_PROMPTS"),
    ("Austria", "austria_prompts", "AUSTRIA_PROMPTS"),
    ("Poland", "poland_prompts", "POLAND_PROMPTS"),
    ("Serbia", "serbia_prompts", "SERBIA_PROMPTS"),
    ("Turkey", "turkey_prompts", "TURKEY_PROMPTS"),
    ("Scotland", "scotland_prompts", "SCOTLAND_PROMPTS"),
    ("Ecuador", "ecuador_prompts", "ECUADOR_PROMPTS"),
    ("Nigeria", "nigeria_prompts", "NIGERIA_PROMPTS"),
    ("Italy", "italy_prompts", "ITALY_PROMPTS"),
    ("Australia", "australia_prompts", "AUSTRALIA_PROMPTS"),
    ("Ivory Coast", "ivory_coast_prompts", "IVORY_COAST_PROMPTS"),
    ("Romania", "romania_prompts", "ROMANIA_PROMPTS"),
    ("Venezuela", "venezuela_prompts", "VENEZUELA_PROMPTS"),
    ("Panama", "panama_prompts", "PANAMA_PROMPTS"),
    ("Egypt", "egypt_prompts", "EGYPT_PROMPTS"),
    ("Saudi Arabia", "saudi_arabia_prompts", "SAUDI_ARABIA_PROMPTS"),
    ("Honduras", "honduras_prompts", "HONDURAS_PROMPTS"),
    ("Jamaica", "jamaica_prompts", "JAMAICA_PROMPTS"),
    ("Cameroon", "cameroon_prompts", "CAMEROON_PROMPTS"),
    ("Algeria", "algeria_prompts", "ALGERIA_PROMPTS"),
    ("Tunisia", "tunisia_prompts", "TUNISIA_PROMPTS"),
    ("South Africa", "south_africa_prompts", "SOUTH_AFRICA_PROMPTS"),
    ("Iran", "iran_prompts", "IRAN_PROMPTS"),
    ("Qatar", "qatar_prompts", "QATAR_PROMPTS"),
    ("China", "china_prompts", "CHINA_PROMPTS"),
    ("Iraq", "iraq_prompts", "IRAQ_PROMPTS"),
    ("New Zealand", "new_zealand_prompts", "NEW_ZEALAND_PROMPTS"),
    ("Ghana", "ghana_prompts", "GHANA_PROMPTS"),
]

def _infer_role(prompt: str) -> str:
    text = prompt[:400].lower()
    if any(k in text for k in ("goalkeeper", "first-choice goalkeeper", "number one", "shot-stopper")):
        return "GK"
    if any(k in text for k in ("centre-back", "center-back", "central defend", "sweeper")):
        return "CB"
    if any(k in text for k in ("right back", "right-back")):
        return "RB"
    if any(k in text for k in ("left back", "left-back")):
        return "LB"
    if any(k in text for k in ("wing-back",)):
        return "RWB"
    if any(k in text for k in ("defensive mid", "holding mid", "cdm", "double pivot")):
        return "CDM"
    if any(k in text for k in ("attacking mid", "number 10", "trequartista", "cam ")):
        return "CAM"
    if any(k in text for k in ("centre-forward", "center-forward", "striker", "number 9", "lead striker")):
        return "ST"
    if any(k in text for k in ("right winger", "right wing", "rw ")):
        return "RW"
    if any(k in text for k in ("left winger", "left wing", "lw ")):
        return "LW"
    return "CM"

def _apply_bonus(bonuses, text):
    total = 0
    for phrase, val in bonuses:
        if phrase in text:
            total += val
    return total

def estimate_stats(name: str, team: str, prompt: str) -> dict:
    role = _infer_role(prompt)
    base = dict(ROLE_BASES.get(role, DEFAULT_BASE))
    text = prompt.lower()

    # Overall quality modifier
    quality = 0
    quality += _apply_bonus(ELITE_BONUS, text)
    quality += _apply_bonus(GOOD_BONUS, text)
    quality += _apply_bonus(WEAK_PENALTY, text)
    quality = max(-8, min(12, quality))

    def cap(v): return max(30, min(99, int(v)))

    return {
        "name":      name,
        "team":      team,
        "position":  ROLE_TO_POS.get(role, "CM"),
        "overall":   cap(base["overall"] + quality),
        "pace":      cap(base["pace"]     + quality * 0.5 + _apply_bonus(PACE_BONUS, text)),
        "shooting":  cap(base["shooting"] + quality       + _apply_bonus(SHOOT_BONUS, text)),
        "passing":   cap(base["passing"]  + quality       + _apply_bonus(PASS_BONUS, text)),
        "dribbling": cap(base["dribbling"]+ quality * 0.8 + _apply_bonus(DRIBBLE_BONUS, text)),
        "defending": cap(base["defending"]+ quality * 0.5 + _apply_bonus(DEFEND_BONUS, text)),
        "physical":  cap(base["physical"] + quality * 0.5 + _apply_bonus(PHYSICAL_BONUS, text)),
    }


def main():
    # Load existing names
    existing = set()
    with open(CSV_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            existing.add(row["name"].strip().lower())

    fieldnames = ["name","team","position","overall","pace","shooting",
                  "passing","dribbling","defending","physical"]

    new_rows = []
    missing_teams = []

    for team_name, mod_name, dict_name in ALL_TEAMS:
        try:
            mod = importlib.import_module(mod_name)
            prompts: dict = getattr(mod, dict_name, {})
        except (ModuleNotFoundError, AttributeError):
            missing_teams.append(team_name)
            continue

        for player_name, prompt in prompts.items():
            if player_name.strip().lower() in existing:
                continue   # already in CSV
            row = estimate_stats(player_name, team_name, prompt)
            new_rows.append(row)
            existing.add(player_name.strip().lower())

    # Append to CSV
    with open(CSV_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        for row in new_rows:
            writer.writerow(row)

    print(f"Added {len(new_rows)} players to {CSV_PATH}")
    if missing_teams:
        print(f"Could not load: {', '.join(missing_teams)}")

    # Final count
    with open(CSV_PATH, encoding="utf-8") as f:
        total = sum(1 for _ in csv.DictReader(f))
    print(f"Total players in CSV: {total}")


if __name__ == "__main__":
    main()
