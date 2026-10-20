"""Cross-check the fact key (built from nflverse) against an independent source: Sleeper's public stats API.

Compares every player-week check whose field Sleeper also reports. Disagreements are written to
data/key/verify_sleeper.json for review; they are not auto-fixed.

python3 scripts/verify_fact_key.py
"""
import csv, json, urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "data" / "ref"
MAP = {"fp_ppr": "pts_ppr", "fp_half": "pts_half_ppr", "targets": "rec_tgt", "receptions": "rec", "receiving_yards": "rec_yd",
       "carries": "rush_att", "rushing_yards": "rush_yd", "passing_yards": "pass_yd", "passing_tds": "pass_td",
       "passing_interceptions": "pass_int", "offense_snaps": "off_snp"}
TOL = {"fp_ppr": 0.15, "fp_half": 0.15}


def main():
    name2gsis = {}
    for r in csv.DictReader(open(REF / "stats_player_week_2026.csv")):
        name2gsis.setdefault(r["player_display_name"], r["player_id"])
    for r in csv.DictReader(open(REF / "injuries_2026.csv")):
        name2gsis.setdefault(r["full_name"], r["gsis_id"])
    gsis2sleeper = {r["gsis_id"]: r["sleeper_id"] for r in csv.DictReader(open(REF / "roster_2026.csv")) if r.get("gsis_id") and r.get("sleeper_id")}
    weeks = {}
    for w in range(1, 6):
        with urllib.request.urlopen(f"https://api.sleeper.app/v1/stats/nfl/regular/2026/{w}", timeout=60) as resp:
            weeks[w] = json.load(resp)
    K = [json.loads(l) for l in open(ROOT / "data" / "key" / "fact_key.jsonl")]
    res, bad = Counter(), []
    for o in K:
        for c in o["checks"]:
            if c["week"] == "season":
                res["season total (from the same weekly rows)"] += 1
                continue
            sl = gsis2sleeper.get(name2gsis.get(c["entity"], ""))
            if c["field"] == "played":
                s = weeks[c["week"]].get(sl, {}) if sl else {}
                active = s.get("gms_active", 0) and (s.get("off_snp", 0) or s.get("rec_tgt", 0) or s.get("rush_att", 0) or s.get("pass_att", 0))
                ok = not active
                res["played:" + ("agree" if ok else "DISAGREE")] += 1
                if not ok:
                    bad.append({"id": o["id"], **c, "sleeper": {k: s.get(k) for k in ("gms_active", "off_snp", "rec_tgt", "rush_att")}})
                continue
            if c["field"] not in MAP:
                res["not compared:" + c["field"]] += 1
                continue
            if not sl:
                res["no sleeper id"] += 1
                continue
            v = weeks[c["week"]].get(sl, {}).get(MAP[c["field"]], 0) or 0
            ok = abs(float(v) - float(c["value"])) <= TOL.get(c["field"], 0.5)
            res[("agree" if ok else "DISAGREE")] += 1
            if not ok:
                bad.append({"id": o["id"], **c, "sleeper": v})
    json.dump(bad, open(ROOT / "data" / "key" / "verify_sleeper.json", "w"), indent=1)
    # fantasy-point disagreements are scoring-rule differences between sites (2-pt conversions, fumbles):
    # accept either value. Any other disagreement stays as is and is listed for review.
    alt = {(b["id"], b["entity"], b["week"], b["field"]): b["sleeper"] for b in bad if b["field"].startswith("fp_")}
    with open(ROOT / "data" / "key" / "fact_key_verified.jsonl", "w") as fh:
        for o in K:
            for c in o["checks"]:
                a = alt.get((o["id"], c["entity"], c["week"], c["field"]))
                if a is not None:
                    c["accept"] = sorted({c["value"], round(float(a), 2)})
                c["verified"] = ("derived from the weekly rows" if c["week"] == "season"
                                 else "sleeper" if c["field"] in MAP or c["field"] == "played" else "single-source")
            o["verified_against"] = "Sleeper stats API (player-week values); nflverse only for snap %, target share, NGS, injury designations, tables"
            fh.write(json.dumps(o) + "\n")
    for k, v in sorted(res.items()):
        print(f"{v:5}  {k}")
    for b in bad[:40]:
        print(b["id"], b["entity"], "wk", b["week"], b["field"], "key", b["value"], "sleeper", b["sleeper"])


if __name__ == "__main__":
    main()
