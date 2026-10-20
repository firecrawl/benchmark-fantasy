"""Answer key for the 150 FACT questions (Weeks 1-5 of the 2026 NFL season), no LLM.

Sources (nflverse, public): stats_player_week_2026, snap_counts_2026, injuries_2026 (final weekly designation),
games.csv (scores, schedule), ngs_receiving/rushing/passing (Next Gen Stats), roster_2026.

Each question becomes a list of checks {entity, week, field, value, tol}. Real user queries are loose, so the
builder resolves players, teams and weeks from the text and picks fields from its wording. Anything the public
data cannot verify (routes, first-read share, DVOA, pressure rate) is listed under `unverifiable`.

python3 scripts/build_fact_key.py -> data/key/fact_key.jsonl, data/key/fact_key_review.md
"""
import csv, gzip, json, re, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "data" / "ref"
LAST_WEEK = 5
POS = {"QB", "RB", "WR", "TE", "FB", "K"}

TEAMS = {
    "ARI": "cardinals arizona", "ATL": "falcons atlanta", "BAL": "ravens baltimore", "BUF": "bills buffalo",
    "CAR": "panthers carolina", "CHI": "bears chicago", "CIN": "bengals cincinnati", "CLE": "browns cleveland",
    "DAL": "cowboys dallas", "DEN": "broncos denver", "DET": "lions detroit", "GB": "packers green bay",
    "HOU": "texans houston", "IND": "colts indianapolis", "JAX": "jaguars jacksonville", "KC": "chiefs kansas city",
    "LV": "raiders las vegas", "LAC": "chargers", "LA": "rams", "MIA": "dolphins miami", "MIN": "vikings minnesota",
    "NE": "patriots new england", "NO": "saints new orleans", "NYG": "giants", "NYJ": "jets", "PHI": "eagles philadelphia",
    "PIT": "steelers pittsburgh", "SF": "49ers san francisco", "SEA": "seahawks seattle", "TB": "buccaneers tampa bay",
    "TEN": "titans tennessee", "WAS": "commanders washington",
}
TEAM_FIX = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "OAK": "LV", "SD": "LAC", "STL": "LA"}

# keyword -> fields checked for each resolved player
FIELD_WORDS = [
    (r"snap", ["offense_snaps", "offense_pct"]),
    (r"target share|targets? per route|tprr", ["targets", "target_share"]),
    (r"\btargets?\b", ["targets"]),
    (r"catch|reception", ["receptions"]),
    (r"receiving|rec yards|yards", ["receiving_yards"]),
    (r"carries|rushing attempts|workload|touches", ["carries", "rushing_yards"]),
    (r"rushing yards|rushing", ["rushing_yards"]),
    (r"passing|quarterback|\bqb\b", ["passing_yards", "passing_tds", "passing_interceptions"]),
    (r"touchdown|\btd\b", ["total_tds"]),
    (r"air yards", ["receiving_air_yards"]),
    (r"adot|average depth", ["ngs_avg_intended_air_yards"]),
    (r"separation", ["ngs_avg_separation"]),
    (r"half.?ppr", ["fp_half"]),
    (r"\bppr\b(?!.*half)|fantasy points|\bpoints\b|\bfpts\b|scoring", ["fp_ppr"]),
    (r"injur|status|inactive|questionable|doubtful|practice|\bout\b", ["report_status"]),
    (r"tackle", ["def_tackles"]),
]
UNVERIFIABLE = [(r"route", "routes / route participation (PFF only)"), (r"first.?read", "first-read share"),
                (r"dvoa", "DVOA"), (r"pressure|time to throw", "pressure rate"), (r"red zone", "red-zone touches"),
                (r"win rate|man zone", "route win rate"), (r"cushion", "cushion")]

TEAM_WORDS = {w for v in TEAMS.values() for w in v.split()}
DATE_WEEK = {}  # filled from games.csv: 'oct 11' -> week


def rd(name):
    op = gzip.open if name.endswith(".gz") else open
    with op(REF / name, "rt") as f:
        return list(csv.DictReader(f))


def norm(s):
    s = s.lower().replace("’", "'").replace("ja'marr", "jamarr").replace("st.", "st")
    s = re.sub(r"[^a-z0-9 ]", " ", s.replace("-", " "))
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def load():
    games = [g for g in rd("games.csv") if g["season"] == "2026" and g["game_type"] == "REG" and int(g["week"]) <= LAST_WEEK]
    for g in games:
        m, d = int(g["gameday"][5:7]), int(g["gameday"][8:10])
        mon = {9: ["sep", "sept", "september"], 10: ["oct", "october"]}[m]
        for mm in mon:
            DATE_WEEK[f"{mm} {d}"] = int(g["week"])
    stats = {}
    names = {}
    for r in rd("stats_player_week_2026.csv"):
        if r["season_type"] != "REG" or int(r["week"]) > LAST_WEEK:
            continue
        pid, w = r["player_id"], int(r["week"])
        std = f(r["fantasy_points"])
        stats[(pid, w)] = {
            "team": TEAM_FIX.get(r["team"], r["team"]), "opp": TEAM_FIX.get(r["opponent_team"], r["opponent_team"]), "pos": r["position"],
            "targets": int(f(r["targets"])), "receptions": int(f(r["receptions"])), "receiving_yards": int(f(r["receiving_yards"])),
            "carries": int(f(r["carries"])), "rushing_yards": int(f(r["rushing_yards"])),
            "passing_yards": int(f(r["passing_yards"])), "passing_tds": int(f(r["passing_tds"])), "passing_interceptions": int(f(r["passing_interceptions"])),
            "total_tds": int(f(r["rushing_tds"]) + f(r["receiving_tds"]) + f(r["passing_tds"])),
            "receiving_air_yards": int(f(r["receiving_air_yards"])),
            "target_share": round(f(r["target_share"]), 3),
            "def_tackles": int(f(r["def_tackles_solo"]) + f(r["def_tackle_assists"])),
            "fp_std": round(std, 2), "fp_half": round(std + 0.5 * f(r["receptions"]), 2), "fp_ppr": round(f(r["fantasy_points_ppr"]), 2),
        }
        names[pid] = {"name": r["player_display_name"], "pos": r["position"]}
    roster = {r["gsis_id"]: r for r in rd("roster_2026.csv") if r.get("gsis_id")}
    pfr2gsis = {r["pfr_id"]: r["gsis_id"] for r in roster.values() if r.get("pfr_id")}
    snaps = {}
    for r in rd("snap_counts_2026.csv"):
        g = pfr2gsis.get(r["pfr_player_id"])
        if g and r["game_type"] == "REG" and int(r["week"]) <= LAST_WEEK:
            snaps[(g, int(r["week"]))] = {"offense_snaps": int(f(r["offense_snaps"])), "offense_pct": round(f(r["offense_pct"]), 2),
                                         "defense_snaps": int(f(r["defense_snaps"])), "defense_pct": round(f(r["defense_pct"]), 2)}
    ngs = {}
    for fn, cols in (("ngs_receiving.csv.gz", ["avg_separation", "avg_intended_air_yards"]), ("ngs_rushing.csv.gz", ["rush_yards_over_expected"]),
                     ("ngs_passing.csv.gz", ["avg_time_to_throw"])):
        for r in rd(fn):
            if r["season"] == "2026" and r["season_type"] == "REG" and 0 <= int(r["week"]) <= LAST_WEEK:
                for c in cols:
                    ngs.setdefault((r["player_gsis_id"], int(r["week"])), {})[f"ngs_{c}"] = round(f(r[c]), 2)
    inj = {}
    for r in rd("injuries_2026.csv"):
        if r["game_type"] == "REG" and int(r["week"]) <= LAST_WEEK:
            inj[(r["gsis_id"], int(r["week"]))] = {"report_status": (r["report_status"] or "none").lower(),
                                                   "practice_status": r["practice_status"], "team": TEAM_FIX.get(r["team"], r["team"]),
                                                   "name": r["full_name"], "pos": r["position"]}
            names.setdefault(r["gsis_id"], {"name": r["full_name"], "pos": r["position"]})
    for g, r in roster.items():
        if r["position"] in POS and g not in names:
            names[g] = {"name": r["full_name"], "pos": r["position"]}
    return games, stats, names, snaps, ngs, inj


def name_index(names, stats):
    full, last = {}, defaultdict(set)
    active = {pid for (pid, _w), s in stats.items() if s["targets"] + s["carries"] + (s["passing_yards"] > 0) > 0}
    for pid, v in names.items():
        n = norm(v["name"])
        full.setdefault(n, pid)
        parts = n.split()
        if len(parts) >= 2 and pid in active:
            last[parts[-1]].add(pid)
    return full, last, active


def resolve_players(q, full, last):
    qn = " " + norm(q) + " "
    found, how = [], {}
    for n, pid in sorted(full.items(), key=lambda x: -len(x[0])):
        if len(n.split()) >= 2 and f" {n} " in qn and pid not in found:
            found.append(pid); how[pid] = "full"
            qn = qn.replace(f" {n} ", " # ")
    for tok in set(qn.split()):
        if len(tok) >= 5 and tok in last and len(last[tok]) == 1 and tok not in TEAM_WORDS:
            pid = next(iter(last[tok]))
            if pid not in found:
                found.append(pid); how[pid] = "last-name"
    return found, how


def resolve_teams(q):
    ql = " " + norm(q) + " "
    out = []
    for ab, words in TEAMS.items():
        nick = words.split()[0]
        if f" {nick} " in ql or (ab == "SF" and "49ers" in ql):
            out.append(ab)
    return out


def resolve_weeks(q):
    ql = q.lower()
    m = re.search(r"weeks? (\d+)\s*(?:-|to|through|and| )\s*(\d+)\b", ql)
    if m and int(m[2]) <= LAST_WEEK and int(m[1]) < int(m[2]):
        return list(range(int(m[1]), int(m[2]) + 1)), "range"
    ws = sorted({int(x) for x in re.findall(r"weeks? (\d+)", ql) if 1 <= int(x) <= LAST_WEEK})
    if ws:
        return ws, "stated"
    for k, w in DATE_WEEK.items():
        if re.search(rf"\b{k}\b", ql):
            return [w], "date"
    return list(range(1, LAST_WEEK + 1)), "season-to-date"


OFF_POS = {"QB", "RB", "WR", "TE", "FB", "K"}


def fields_for(q, pos):
    ql = q.lower()
    out = []
    for pat, fl in FIELD_WORDS:
        if re.search(pat, ql):
            out += [x for x in fl if x not in out]
    if not out or out == ["fp_ppr"]:
        base = {"QB": ["passing_yards", "passing_tds", "passing_interceptions", "rushing_yards"], "RB": ["carries", "rushing_yards", "targets", "receptions"],
                "WR": ["targets", "receptions", "receiving_yards"], "TE": ["targets", "receptions", "receiving_yards"]}.get(pos, [])
        out = base + [x for x in out if x not in base]
    if re.search(r"half", ql):
        out = [("fp_half" if x == "fp_ppr" else x) for x in out]
        out = list(dict.fromkeys(out))
    if pos not in OFF_POS:  # defenders: defensive snaps, tackles
        out = [{"offense_snaps": "defense_snaps", "offense_pct": "defense_pct"}.get(x, x) for x in out]
        out = [x for x in out if x in ("defense_snaps", "defense_pct", "def_tackles", "report_status")] or ["defense_snaps", "def_tackles"]
    if pos != "QB":
        out = [x for x in out if not x.startswith("passing")]
    if pos == "QB":
        out = [x for x in out if x not in ("targets", "receptions", "receiving_yards", "target_share", "receiving_air_yards", "ngs_avg_separation")]
    return out


TOL = {"offense_pct": 0.02, "defense_pct": 0.02, "target_share": 0.03, "fp_ppr": 0.1, "fp_half": 0.1, "fp_std": 0.1, "ngs_avg_separation": 0.1,
       "ngs_avg_intended_air_yards": 0.2, "receiving_air_yards": 1}


def build():
    global TEAM_WEEK
    games, stats, names, snaps, ngs, inj = load()
    TEAM_WEEK = rd("stats_team_week_2026.csv")
    played_teams = defaultdict(set)
    for g in games:
        played_teams[int(g["week"])] |= {TEAM_FIX.get(g["home_team"], g["home_team"]), TEAM_FIX.get(g["away_team"], g["away_team"])}
    last_roster = {}
    for r in rd("roster_2026.csv"):
        if r.get("gsis_id"):
            last_roster[r["gsis_id"]] = TEAM_FIX.get(r["team"], r["team"])

    def team_of(pid, w):
        for ww in range(w, 0, -1):
            if (pid, ww) in stats:
                return stats[(pid, ww)]["team"]
        if (pid, w) in inj:
            return inj[(pid, w)]["team"]
        return last_roster.get(pid)
    full, last, active = name_index(names, stats)
    qs = [json.loads(l) for l in open(ROOT / "data" / "questions" / "questions_300.jsonl") if json.loads(l)["bucket"] == "fact"]
    out = []
    for qd in qs:
        q = qd["question"]
        ql = q.lower()
        weeks, wmode = resolve_weeks(q)
        players, how = resolve_players(q, full, last)
        teams = resolve_teams(q)
        checks, tables, notes = [], [], []
        unver = [lab for pat, lab in UNVERIFIABLE if re.search(pat, ql)]
        if re.search(r"injur|practice|inactive|status", ql) and not players:
            posf = [p for p, pat in (("QB", r"quarterback|\bqb"), ("RB", r"running back|\brb"), ("WR", r"wide receiver|\bwr"), ("TE", r"tight end|\bte\b")) if re.search(pat, ql)] if not teams else []
            for w in (weeks if wmode != "season-to-date" else weeks[-1:]):
                rows = [{"player": v["name"], "team": v["team"], "designation": v["report_status"]} for (g, ww), v in inj.items()
                        if ww == w and v["report_status"] not in ("none", "")
                        and (not teams or v["team"] in teams) and (not posf or v["pos"] in posf)]
                tables.append({"kind": "injury_report", "week": w, "teams": teams or "all", "positions": posf or "all", "key": "player",
                               "mode": "recall+precision" if teams else "precision",
                               "rows": sorted(rows, key=lambda r: (r["team"], r["player"]))})
        if re.search(r"\bbyes?\b", ql) and not players:
            allg = [g for g in rd("games.csv") if g["season"] == "2026" and g["game_type"] == "REG"]
            bw = sorted({int(x) for x in re.findall(r"week[- _/]?(\d+)", ql)}) or weeks[-1:]
            for w in bw:
                on = {TEAM_FIX.get(g["home_team"], g["home_team"]) for g in allg if int(g["week"]) == w} | {TEAM_FIX.get(g["away_team"], g["away_team"]) for g in allg if int(g["week"]) == w}
                tables.append({"kind": "byes", "week": w, "key": "team", "mode": "recall+precision", "rows": [{"team": t} for t in sorted(set(TEAMS) - on)]})
        if re.search(r"score|result|scoreboard|box ?score|standings|schedule|kickoff", ql) and not players and not re.search(r"teams on bye", ql):
            for w in weeks:
                gs = [g for g in games if int(g["week"]) == w and (not teams or TEAM_FIX.get(g["home_team"], g["home_team"]) in teams or TEAM_FIX.get(g["away_team"], g["away_team"]) in teams)]
                if ("standings" in ql or "record" in ql) and any(t["kind"] == "standings" for t in tables):
                    continue
                if "standings" in ql or "record" in ql:
                    rec = defaultdict(lambda: [0, 0, 0])
                    for g in games:
                        if int(g["week"]) > w:
                            continue
                        h, a, hs, as_ = TEAM_FIX.get(g["home_team"], g["home_team"]), TEAM_FIX.get(g["away_team"], g["away_team"]), int(g["home_score"]), int(g["away_score"])
                        for t, mine, theirs in ((h, hs, as_), (a, as_, hs)):
                            rec[t][0 if mine > theirs else 1 if mine < theirs else 2] += 1
                    tables.append({"kind": "standings", "through_week": w, "key": "team", "rows": [{"team": t, "w": r[0], "l": r[1], "t": r[2]} for t, r in sorted(rec.items())]})
                else:
                    if any(t["kind"] == "games" for t in tables):
                        tables[[t["kind"] for t in tables].index("games")]["rows"] += [{"away_team": TEAM_FIX.get(g['away_team'], g['away_team']), "home_team": TEAM_FIX.get(g['home_team'], g['home_team']),
                                             "week": int(g["week"]), "gameday": g["gameday"], "away_score": int(g["away_score"]), "home_score": int(g["home_score"])} for g in gs]
                        continue
                    tables.append({"kind": "games", "weeks": weeks, "key": "teams", "mode": "recall+precision" if (wmode == "stated" and len(weeks) == 1 and not teams) else "precision",
                                   "rows": [{"away_team": TEAM_FIX.get(g['away_team'], g['away_team']), "home_team": TEAM_FIX.get(g['home_team'], g['home_team']),
                                             "week": int(g["week"]), "gameday": g["gameday"], "away_score": int(g["away_score"]), "home_score": int(g["home_score"])} for g in gs]})
        if re.search(r"points allowed|allowed|defense vs|vs (rb|wr|te|qb)", ql) or (re.search(r"matchup", ql) and not players):
            posw = [p for p in ("QB", "RB", "WR", "TE") if re.search(rf"\b{p.lower()}s?\b|{ {'QB': 'quarterback', 'RB': 'running back', 'WR': 'wide receiver', 'TE': 'tight end'}[p]}", ql)] or ["QB", "RB", "WR", "TE"]
            span = weeks if wmode == "range" else list(range(1, max(weeks) + 1))
            allowed = defaultdict(lambda: defaultdict(float)); gp = defaultdict(set)
            for (pid, w), s in stats.items():
                if w in span and s["pos"] in posw:
                    for fm in ("fp_ppr", "fp_half", "fp_std"):
                        allowed[s["opp"]][(s["pos"], fm)] += s[fm]
                    gp[s["opp"]].add(w)
            dteams = teams or sorted(allowed)
            tables.append({"kind": "points_allowed", "weeks": span, "positions": posw, "key": "team",
                           "note": "per-game fantasy points allowed; any scoring format (ppr/half/std) accepted, +-1.0 (site rules differ)",
                           "rows": [{"team": t, **{f"{p}_per_game_{fm[3:]}": round(allowed[t][(p, fm)] / max(1, len(gp[t])), 2) for p in posw for fm in ("fp_ppr", "fp_half", "fp_std")}, "games": len(gp[t])} for t in dteams]})
        if re.search(r"defense passing|pass(ing)? defense|team.?stats defense", ql) and not players:
            span = list(range(1, max(weeks) + 1))
            allowed = defaultdict(lambda: [0, 0, 0, set()])
            for r in TEAM_WEEK:
                if r["season_type"] == "REG" and int(r["week"]) in span:
                    d = allowed[TEAM_FIX.get(r["opponent_team"], r["opponent_team"])]
                    d[0] += int(f(r["passing_yards"])); d[1] += int(f(r["passing_tds"])); d[2] += int(f(r["passing_interceptions"])); d[3].add(r["week"])
            tables.append({"kind": "pass_defense", "weeks": span, "key": "team",
                           "rows": [{"team": t, "pass_yards_allowed": v[0], "pass_tds_allowed": v[1], "interceptions": v[2], "games": len(v[3])} for t, v in sorted(allowed.items())]})
        if re.search(r"\bdst\b|d/st|defense special teams", ql) and not players:
            score = {(TEAM_FIX.get(g["home_team"], g["home_team"]), int(g["week"])): int(g["away_score"]) for g in games}
            score.update({(TEAM_FIX.get(g["away_team"], g["away_team"]), int(g["week"])): int(g["home_score"]) for g in games})
            rows = []
            for r in TEAM_WEEK:
                t, w = TEAM_FIX.get(r["team"], r["team"]), int(r["week"])
                if r["season_type"] == "REG" and w in weeks and (not teams or t in teams):
                    rows.append({"team": t, "week": w, "sacks": f(r["def_sacks"]), "interceptions": int(f(r["def_interceptions"])),
                                 "fumble_recoveries": int(f(r["fumble_recovery_opp"])), "def_st_tds": int(f(r["def_tds"]) + f(r["special_teams_tds"])),
                                 "safeties": int(f(r["def_safeties"])), "points_allowed": score.get((t, w))})
            tables.append({"kind": "dst_components", "weeks": weeks, "key": "team", "rows": rows,
                           "note": "FPTS depend on the site's D/ST scoring; grade the components, accept any FPTS consistent with them"})
        if re.search(r"usage report|every team|usage snaps|usage data", ql) and not players and not re.search(r"standings|record", ql):
            for w in weeks[-1:]:
                rows = []
                for (pid, ww), s in stats.items():
                    if ww == w and s["pos"] in ("RB", "WR", "TE") and s["targets"] >= 5:
                        sn = snaps.get((pid, w), {})
                        rows.append({"player": names[pid]["name"], "team": s["team"], "targets": s["targets"], "target_share": s["target_share"],
                                     "carries": s["carries"], "offense_pct": sn.get("offense_pct")})
                tables.append({"kind": "usage_report", "week": w, "key": "player", "rows": sorted(rows, key=lambda r: -r["targets"]),
                               "note": "graded on a sample: every listed player's targets/snap% must match; recall not required (league-wide)"})
        for pid in players:
            pos = names[pid]["pos"]
            fl = fields_for(q, pos)
            for w in weeks:
                s, sn, ng, ij = stats.get((pid, w)), snaps.get((pid, w)), ngs.get((pid, w), {}), inj.get((pid, w))
                if s is None:
                    tm = team_of(pid, w)
                    if tm and tm not in played_teams[w]:
                        checks.append({"entity": names[pid]["name"], "week": w, "field": "played", "value": "bye", "tol": 0, "team": tm})
                        continue
                    checks.append({"entity": names[pid]["name"], "week": w, "field": "played", "value": False, "tol": 0})
                    if ij and ij["report_status"] not in ("none", ""):
                        checks.append({"entity": names[pid]["name"], "week": w, "field": "report_status", "value": ij["report_status"], "tol": 0,
                                       "accept": sorted({ij["report_status"], "out", "inactive"})})
                    else:
                        notes.append(f"{names[pid]['name']} wk{w}: did not play and not on the game-status report (reserve/IR, exempt, inactive or traded); grade 'did not play' only")
                    continue
                for fld in fl:
                    if fld in ("offense_snaps", "offense_pct", "defense_snaps", "defense_pct"):
                        if sn: checks.append({"entity": names[pid]["name"], "week": w, "field": fld, "value": sn[fld], "tol": TOL.get(fld, 0)})
                    elif fld.startswith("ngs_"):
                        if fld in ng: checks.append({"entity": names[pid]["name"], "week": w, "field": fld, "value": ng[fld], "tol": TOL.get(fld, 0)})
                        else: notes.append(f"{names[pid]['name']} wk{w}: no NGS row (below NGS minimum)")
                    elif fld == "report_status":
                        checks.append({"entity": names[pid]["name"], "week": w, "field": "report_status", "value": (ij or {}).get("report_status", "none"), "tol": 0})
                    elif s:
                        checks.append({"entity": names[pid]["name"], "week": w, "field": fld, "value": s[fld], "tol": TOL.get(fld, 0)})
        if wmode == "season-to-date" and players:
            SUM = {"targets", "receptions", "receiving_yards", "carries", "rushing_yards", "passing_yards", "passing_tds", "passing_interceptions",
                   "total_tds", "receiving_air_yards", "fp_ppr", "fp_half", "fp_std", "offense_snaps", "defense_snaps", "def_tackles"}
            for c in checks:
                c["weekly_in_season"] = True
            for pid in players:
                nm = names[pid]["name"]
                for fld in fields_for(q, names[pid]["pos"]):
                    if fld in SUM:
                        vals = [(snaps.get((pid, w)) or {}).get(fld) if fld.endswith("_snaps") else (stats.get((pid, w)) or {}).get(fld) for w in weeks]
                        vals = [v for v in vals if v is not None]
                        if vals:
                            tot = round(sum(vals), 2)
                            checks.append({"entity": nm, "week": "season", "field": fld, "value": tot, "tol": max(1, 0.02 * abs(tot)) if fld.startswith("fp_") or "yards" in fld else 1})
                    elif fld.startswith("ngs_") and ngs.get((pid, 0), {}).get(fld) is not None:
                        checks.append({"entity": nm, "week": "season", "field": fld, "value": ngs[(pid, 0)][fld], "tol": TOL.get(fld, 0.1) + 0.1})
                    elif fld in ("offense_pct", "defense_pct", "target_share"):
                        vals = [((snaps.get((pid, w)) or {}) if fld != "target_share" else (stats.get((pid, w)) or {})).get(fld) for w in weeks]
                        vals = [v for v in vals if v]
                        if vals:
                            checks.append({"entity": nm, "week": "season", "field": fld, "value": round(sum(vals) / len(vals), 3), "tol": 0.05})
        n_checks = len(checks) + sum(len(t["rows"]) for t in tables)
        conf = "high" if (players and all(how[p] == "full" for p in players) and wmode in ("stated", "range", "date", "season-to-date")) or (tables and not players) else "review"
        if not n_checks:
            conf = "unkeyable"
        out.append({"id": qd["id"], "question": q, "weeks": weeks, "week_mode": wmode,
                    "players": [{"name": names[p]["name"], "pos": names[p]["pos"], "match": how[p]} for p in players], "teams": teams,
                    "checks": checks, "tables": tables, "unverifiable": unver, "notes": notes, "confidence": conf, "n_checks": n_checks})
    return out


def main():
    out = build()
    kd = ROOT / "data" / "key"
    kd.mkdir(parents=True, exist_ok=True)
    with open(kd / "fact_key.jsonl", "w") as fh:
        for o in out:
            fh.write(json.dumps(o) + "\n")
    with open(kd / "fact_key_review.md", "w") as fh:
        for o in out:
            fh.write(f"### {o['id']} [{o['confidence']}] {o['question']}\n")
            fh.write(f"weeks {o['weeks']} ({o['week_mode']}); players {[(p['name'], p['match']) for p in o['players']]}; teams {o['teams']}\n")
            for c in o["checks"][:40]:
                fh.write(f"- {c['entity']} wk{c['week']} {c['field']} = {c['value']}\n")
            for t in o["tables"]:
                fh.write(f"- TABLE {t['kind']} {({k: v for k, v in t.items() if k not in ('rows',)})} rows={len(t['rows'])} e.g. {t['rows'][:3]}\n")
            if o["unverifiable"]:
                fh.write(f"- not verifiable: {o['unverifiable']}\n")
            for n in o["notes"]:
                fh.write(f"- note: {n}\n")
            fh.write("\n")
    from collections import Counter
    print(Counter(o["confidence"] for o in out), "checks:", sum(o["n_checks"] for o in out))


if __name__ == "__main__":
    main()
