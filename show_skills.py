"""Show extracted skill profiles for Argentina and Brazil squads."""
import sys
sys.path.insert(0, '.')
from match_engine.player_skills import extract_skills

ROLE_KEYWORDS = [
    "GK", "CB", "RB", "LB", "RWB", "LWB",
    "CDM", "CM", "CAM", "RM", "LM", "RW", "LW", "CF",
]

def infer_role(prompt):
    text = prompt[:300].lower()
    if any(k in text for k in ("goalkeeper", "first-choice goalkeeper", "number one")):
        return "GK"
    if any(k in text for k in ("centre-back", "center-back", "central defend", "right back", "left back", "wing-back")):
        return "CB"
    if any(k in text for k in ("defensive mid", "cdm", "holding mid")):
        return "CDM"
    if any(k in text for k in ("attacking mid", "cam", "number 10")):
        return "CAM"
    if any(k in text for k in ("centre-forward", "center-forward", "striker", "cf ")):
        return "CF"
    if any(k in text for k in ("right wing", "right winger", "rw ")):
        return "RW"
    if any(k in text for k in ("left wing", "left winger", "lw ")):
        return "LW"
    return "CM"

teams = [
    ("argentina_prompts", "ARGENTINA_PROMPTS"),
    ("brazil_prompts",    "BRAZIL_PROMPTS"),
]

for mod_name, dict_name in teams:
    mod = __import__(mod_name)
    prompts = getattr(mod, dict_name)
    label = mod_name.replace("_prompts", "").upper()
    print(f"\n{'='*70}")
    print(f"  {label}  ({len(prompts)} players)")
    print(f"{'='*70}")
    print(f"  {'PLAYER':<28} {'ROLE':<6} {'PASS':>6} {'INTCPT':>7} {'SHOOT':>7} {'DRIBBLE':>8}")
    print(f"  {'-'*28} {'-'*6} {'-'*6} {'-'*7} {'-'*7} {'-'*8}")
    for name, prompt in prompts.items():
        role = infer_role(prompt)
        s = extract_skills(prompt, role)
        print(f"  {name[:28]:<28} {role:<6} {s['pass']:>6.3f} {s['intercept']:>7.3f} "
              f"{s['shoot']:>7.3f} {s['dribble']:>8.3f}")
