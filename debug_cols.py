import csv
with open('player_stats.csv', encoding='utf-8') as f:
    rows = {r['name']: r for r in csv.DictReader(f)}

check = ['Lionel Messi','Vinicius Jr','Casemiro','Kylian Mbappé',
         'Harry Kane','Jude Bellingham','Rodri','Erling Haaland']
print(f"{'NAME':<25} {'POS':<5} {'OVR':>4} {'PAC':>4} {'SHO':>4} {'PAS':>4} {'DRI':>4} {'DEF':>4} {'PHY':>4}")
print('-'*65)
for n in check:
    r = rows.get(n)
    if r:
        print(f"{n:<25} {r['position']:<5} {r['overall']:>4} {r['pace']:>4} "
              f"{r['shooting']:>4} {r['passing']:>4} {r['dribbling']:>4} "
              f"{r['defending']:>4} {r['physical']:>4}")
    else:
        print(f"{n:<25} NOT FOUND")

print(f"\nTotal in CSV: {len(rows)}")
print(f"Unmatched (keyword fallback): {1248 - len(rows)}")
