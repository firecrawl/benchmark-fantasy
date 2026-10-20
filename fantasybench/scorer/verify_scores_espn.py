"""Check the fact key's game scores (nflverse games.csv, Weeks 1-5 of 2026) against ESPN's public scoreboard API.

python3 scripts/verify_scores_espn.py -> data/key/verify_espn_scores.json (every game, both sources, agree/disagree)
"""
import csv, json, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX"}


def main():
    ours = {(int(g["week"]), g["away_team"], g["home_team"]): (int(g["away_score"]), int(g["home_score"]))
            for g in csv.DictReader(open(ROOT / "data" / "ref" / "games.csv"))
            if g["season"] == "2026" and g["game_type"] == "REG" and int(g["week"]) <= 5}
    rows = []
    for w in range(1, 6):
        url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=2026&seasontype=2&week={w}"
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.load(r)
        for e in d["events"]:
            c = e["competitions"][0]["competitors"]
            h = next(x for x in c if x["homeAway"] == "home"); a = next(x for x in c if x["homeAway"] == "away")
            k = (w, FIX.get(a["team"]["abbreviation"], a["team"]["abbreviation"]), FIX.get(h["team"]["abbreviation"], h["team"]["abbreviation"]))
            espn = (int(a["score"]), int(h["score"]))
            rows.append({"week": w, "away": k[1], "home": k[2], "nflverse": ours.get(k), "espn": espn, "agree": ours.get(k) == espn})
    out = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "games_in_key": len(ours), "games_checked": len(rows),
           "agree": sum(r["agree"] for r in rows), "rows": rows}
    json.dump(out, open(ROOT / "data" / "key" / "verify_espn_scores.json", "w"), indent=1)
    print(f"ESPN scores: {out['agree']}/{out['games_checked']} agree ({out['games_in_key']} games in the key)")


if __name__ == "__main__":
    main()
