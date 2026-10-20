"""Grade a v2 run. A model only copies values out of each answer (Claude Sonnet, JSON); a program compares them with the key.

FACT (F*): key = data/key/fact_key_verified.jsonl (nflverse, cross-checked vs Sleeper/ESPN).
  - player checks: a value is right within its tolerance (or any `accept` value); "did not play" / "bye" must be stated.
  - tables: injury report / games: recall + precision on the key column, designation/score cells; points allowed /
    pass defense / D/ST: cells for the teams the answer lists; usage report: listed players' values (no recall, league-wide).
  - pass = at least 90% of checkable items right and at least one item answered.
LIVE (L*): key = Week 6 projection snapshots (data/key/live/week6_pre_*.json and week6_post_*.json).
  - every player value the answer gives is compared with the site the answer (or the question) attributes it to, in the
    question's scoring format, within 0.3 points (any snapshot); with no site, within the min..max of all captured sites
    (+-0.3). Ranks: within 2 places of that site's rank. Players on bye must be reported as on bye / not playing.
  - a question passes when every player it names is answered right; table questions pass when at least 8 rows are given
    and 90% of them are right.
Writes runs/<id>/grades.jsonl (one row per cell) and runs/<id>/extract/ (cached extractions).

python3 scripts/grade.py --run-id pilot-1 [--live-pre FILE --live-post FILE]
"""
import argparse, glob, json, re, threading, time, urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTRACTOR = "claude-sonnet-5-5"
SITES = {"draftsharks": r"draft ?sharks", "fftoday": r"ff ?today", "cbs": r"\bcbs", "fantasydata": r"fantasy ?data",
         "ffcalc": r"fantasy ?football ?calculator|ffcalc", "startwho": r"start ?who", "fantasypros": r"fantasy ?pros"}


def norm(s):
    s = str(s or "").lower().replace("’", "'").replace("ja'marr", "jamarr").replace("st.", "st")
    s = re.sub(r"[^a-z0-9 ]", " ", s.replace("-", " "))
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\b([a-z]) (?=[a-z]\b)", r"\1", s)  # initials: "d j moore" and "dj moore" are the same player


TEAM_ALIASES = {"ARI": "cardinals arizona ari", "ATL": "falcons atlanta atl", "BAL": "ravens baltimore bal", "BUF": "bills buffalo buf",
    "CAR": "panthers carolina car", "CHI": "bears chicago chi", "CIN": "bengals cincinnati cin", "CLE": "browns cleveland cle",
    "DAL": "cowboys dallas dal", "DEN": "broncos denver den", "DET": "lions detroit det", "GB": "packers green bay gb gnb",
    "HOU": "texans houston hou", "IND": "colts indianapolis ind", "JAX": "jaguars jacksonville jax jac", "KC": "chiefs kansas city kc kan",
    "LV": "raiders las vegas lv lvr", "LAC": "chargers los angeles lac", "LA": "rams los angeles lar la", "MIA": "dolphins miami mia",
    "MIN": "vikings minnesota min", "NE": "patriots new england ne nwe", "NO": "saints new orleans no nor", "NYG": "giants new york nyg",
    "NYJ": "jets new york nyj", "PHI": "eagles philadelphia phi", "PIT": "steelers pittsburgh pit", "SF": "49ers san francisco sf sfo",
    "SEA": "seahawks seattle sea", "TB": "buccaneers bucs tampa bay tb tam", "TEN": "titans tennessee ten", "WAS": "commanders washington was wsh"}


def team_abbr(text):
    t = " " + norm(text) + " "
    for ab, words in TEAM_ALIASES.items():  # nicknames first: "new york" alone is ambiguous
        if f" {words.split()[0]} " in t:
            return ab
    for ab, words in TEAM_ALIASES.items():
        ws = words.split()[1:]
        for w in [" ".join(ws[:2]), " ".join(ws[:3])] + ws[-2:]:
            if w and w not in ("new york", "los angeles", "new", "york", "los", "angeles") and f" {w} " in t:
                return ab
    return None


def num(x):
    try:
        return float(str(x).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def env_key(name):
    for f in (ROOT.parent / "harness" / ".env.eval",):
        m = re.search(rf"^{name}=(.*)$", f.read_text(), re.M)
        if m:
            return m.group(1).strip().strip("'\"")
    raise SystemExit(f"{name} not found")


_lock = threading.Lock()


def claude_json(system, user, max_tokens=32000):
    body = json.dumps({"model": EXTRACTOR, "max_tokens": max_tokens, "system": system,
                       "messages": [{"role": "user", "content": user}]}).encode()
    for attempt in range(5):
        try:
            req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
                "x-api-key": env_key("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01", "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                out = json.load(r)
            text = "".join(c.get("text", "") for c in out["content"])
            m = re.search(r"\{.*\}", text, re.S)
            return json.loads(m.group(0)), out.get("usage", {})
        except Exception as e:  # rate limits, transient errors, malformed JSON
            err = e
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"extraction failed: {err}")


SYS_FACT = """You copy values out of an answer to an NFL fantasy question. Do not judge, compute or correct anything; copy only what
the ANSWER states. Return JSON: {"slots": {slot_id: value}, "tables": {table_id: [row, ...]}}.
- slots: for each slot, the value the answer gives for that player, week and field, as written (a number, a percent, a
  designation word like "questionable"). Use "bye" if the answer says the team was on bye, "did not play" if it says the
  player did not play / was inactive / out (and gives no stats), null if the answer does not give it. For a percent give
  the number as written (e.g. "68%" -> 68). Season or multi-week totals do not count for a single week: use null.
- tables: for each requested table, every row the answer lists, with the requested columns (null where not given)."""

SYS_LIVE = """You copy values out of an answer to an NFL fantasy projections question. Do not judge or compute; copy only what the
ANSWER states. Return JSON: {"rows": [{"player", "site", "scoring", "proj_pts", "rank", "status"}]} with one row per player and
source the answer gives a Week 6 value for.
- site: the source the answer attributes that value to (e.g. "Draft Sharks", "FFToday", "CBS", "FantasyData", "Fantasy Football
  Calculator", "StartWho", "FantasyPros", "ESPN"), or "consensus"/"average" if the answer averages sources, or null.
- scoring: "ppr", "half" or "std" if stated for that value, else null. proj_pts: the projected fantasy points as a number, or null.
- rank: the player's rank number if given (positional or overall as written), else null. Add "rank_type": "position" or "overall".
- status: "bye" if the answer says the player's team is on bye in Week 6, "out" if it says the player is out / not playing,
  else null. Include bye/out players even with no numbers."""


def fact_request(k):
    slots, tables = {}, {}
    for i, c in enumerate(k["checks"]):
        wk = "2026 season so far (Weeks 1-5 total; average for rates/percent)" if c["week"] == "season" else f"Week {c['week']}"
        slots[f"s{i}"] = f"{c['entity']} | {wk} | {FIELD_DESC.get(c['field'], c['field'])}"
    for i, t in enumerate(k["tables"]):
        cols = [x for x in (t["rows"][0].keys() if t["rows"] else []) if x != "games"]
        if t["kind"] == "games":
            cols = ["away_team", "home_team", "week", "away_score", "home_score"]
        if t["kind"] == "points_allowed":
            cols = ["team"] + [f"{p}_per_game" for p in t["positions"]] + ["scoring"]
        tables[f"t{i}"] = f"{t['kind']} ({', '.join(f'{a}={b}' for a, b in t.items() if a not in ('rows', 'key', 'kind', 'note'))}); columns: {cols}"
    return slots, tables


FIELD_DESC = {"fp_ppr": "full-PPR fantasy points", "fp_half": "half-PPR fantasy points", "targets": "targets", "receptions": "receptions",
              "receiving_yards": "receiving yards", "carries": "carries / rushing attempts", "rushing_yards": "rushing yards",
              "offense_snaps": "offensive snaps", "offense_pct": "offensive snap share (%)", "target_share": "target share (%)",
              "report_status": "game-status designation on the injury report (out/doubtful/questionable/none)",
              "played": "did he play? (bye / did not play / played)", "passing_yards": "passing yards", "passing_tds": "passing TDs",
              "passing_interceptions": "interceptions thrown", "ngs_avg_separation": "average separation (yards, Next Gen Stats)",
              "ngs_avg_intended_air_yards": "aDOT / average intended air yards", "receiving_air_yards": "receiving air yards", "total_tds": "touchdowns", "defense_snaps": "defensive snaps",
              "defense_pct": "defensive snap share (%)", "def_tackles": "total tackles"}


def same(field, got, c):
    if field == "played":
        g = str(got or "").lower()
        return ("bye" in g) if c["value"] == "bye" else bool(re.search(r"did not play|dnp|inactive|\bout\b|did not|ir\b|reserve|injur|bye", g))
    if field == "report_status":
        g = str(got or "none").lower()
        wants = c.get("accept") or [c["value"]]
        return any((w in g) if w != "none" else not re.search(r"\bout\b|doubtful|questionable", g) for w in wants)
    g = num(got)
    if g is None:
        return False
    vals = c.get("accept") or [c["value"]]
    if field in ("offense_pct", "defense_pct", "target_share"):
        g = g / 100 if g > 1.5 else g
    if field in ("offense_snaps", "defense_snaps"):
        return any(abs(g - float(v)) <= max(2, 0.1 * float(v)) for v in vals)
    if field in ("offense_pct", "defense_pct"):
        return any(abs(g - float(v)) <= 0.05 for v in vals)
    return any(abs(g - float(v)) <= max(c.get("tol", 0), 1e-9) + 1e-9 for v in vals)


def grade_fact(k, ex):
    items, right = 0, 0
    detail = []
    weekly = {}
    for i, c in enumerate(k["checks"]):
        if c.get("weekly_in_season"):
            got = (ex.get("slots") or {}).get(f"s{i}")
            if got is not None and c["field"] not in ("played", "report_status"):
                weekly.setdefault((c["entity"], c["field"]), []).append(same(c["field"], got, c))
    for i, c in enumerate(k["checks"]):
        got = (ex.get("slots") or {}).get(f"s{i}")
        if c.get("weekly_in_season"):
            continue
        if c["week"] == "season" and got is None and weekly.get((c["entity"], c["field"])):
            ws = weekly[(c["entity"], c["field"])]  # answered week by week instead of a season total
            ok = all(ws)
            items += 1; right += ok
            detail.append([c["entity"], "weekly", c["field"], c["value"], f"{sum(ws)}/{len(ws)} weeks right", ok])
            continue
        pair = {"offense_pct": "offense_snaps", "offense_snaps": "offense_pct", "defense_pct": "defense_snaps", "defense_snaps": "defense_pct"}.get(c["field"])
        if got is None and pair:
            j = next((x for x, cc in enumerate(k["checks"]) if cc["entity"] == c["entity"] and cc["week"] == c["week"] and cc["field"] == pair), None)
            if j is not None and (ex.get("slots") or {}).get(f"s{j}") is not None:
                detail.append([c["entity"], c["week"], c["field"], c["value"], got, "optional"])
                continue
        if got is None and (c["field"] == "played" or (c["field"] == "report_status" and c["value"] == "none")):
            detail.append([c["entity"], c["week"], c["field"], c["value"], got, "optional"])
            continue
        ok = got is not None and same(c["field"], got, c)
        items += 1; right += ok
        detail.append([c["entity"], c["week"], c["field"], c["value"], got, ok])
    answered_tables = 0
    for i, t in enumerate(k["tables"]):
        rows = (ex.get("tables") or {}).get(f"t{i}") or []
        if any(isinstance(r, list) for r in rows):  # rows returned as lists: map onto the requested column order
            req = [x for x in (t["rows"][0].keys() if t["rows"] else []) if x != "games"]
            if t["kind"] == "games":
                req = ["away_team", "home_team", "week", "away_score", "home_score"]
            elif t["kind"] == "points_allowed":
                req = ["team"] + [f"{p}_per_game" for p in t["positions"]] + ["scoring"]
            rows = [dict(zip(req, r)) if isinstance(r, list) else r for r in rows]
        rows = [r for r in rows if isinstance(r, dict)]
        answered_tables += bool(rows)
        keycol = t["key"]
        teamkey = keycol == "team"
        if keycol == "teams":  # games: an unordered team pair
            want = {frozenset((r["away_team"], r["home_team"])): r for r in t["rows"]}
            got = {}
            for r in rows:
                a_, h_ = team_abbr(r.get("away_team") or ""), team_abbr(r.get("home_team") or "")
                if not (a_ and h_):
                    m = re.split(r"\s+(?:@|at|vs\.?|v)\s+", str(r.get("matchup") or ""), flags=re.I)
                    if len(m) == 2:
                        a_, h_ = team_abbr(m[0]), team_abbr(m[1])
                if a_ and h_ and frozenset((a_, h_)) not in got:
                    got[frozenset((a_, h_))] = {**r, "_away": a_, "_home": h_}
        else:
            want = {(r[keycol] if teamkey else norm(r[keycol])): r for r in t["rows"]}
            got = {}
            for r in rows:
                raw = r.get(keycol) or r.get("player") or r.get("team") or r.get("matchup")
                kv = team_abbr(raw) if teamkey else norm(raw)
                if kv and kv not in got:
                    got[kv] = r
        if t["kind"] in ("injury_report", "games", "standings", "byes"):
            if t["kind"] == "injury_report":  # practice-only rows (no game designation) are not claims about the report
                got = {kv: r for kv, r in got.items() if re.search(r"out|doubtful|questionable", str(r.get("designation") or "").lower())}
            hit = [kv for kv in got if kv in want]
            rec = len(hit) / max(1, len(want)); prec = len(hit) / max(1, len(got))
            cells = 0
            for kv in hit:
                w, g = want[kv], got[kv]
                if t["kind"] == "injury_report":
                    cells += str(w["designation"]).lower() in str(g.get("designation", "")).lower()
                elif t["kind"] == "games":
                    gs = (num(g.get("away_score")), num(g.get("home_score")))
                    if gs == (None, None):
                        cells += 1  # schedule answer without scores: the matchup itself is the claim
                    else:
                        ws = (w["away_score"], w["home_score"]) if g["_away"] == w["away_team"] else (w["home_score"], w["away_score"])
                        cells += gs == ws or gs == ws[::-1]
                elif t["kind"] == "byes":
                    cells += 1
                else:
                    cells += num(g.get("w")) == w["w"] and num(g.get("l")) == w["l"]
            cell_acc = cells / max(1, len(hit))
            if t.get("mode") == "precision":  # league-wide lists: what is listed must be right; completeness not required
                if keycol == "teams" and k.get("teams"):  # games outside the asked teams are not claims about them
                    asked = set(k["teams"])
                    inside = [kv for kv in got if kv & asked]
                    hit = [kv for kv in inside if kv in want]
                    prec = len(hit) / max(1, len(inside))
                ok = len(got) >= min(1 if keycol == "teams" else 3, len(want)) and prec >= 0.9 and cell_acc >= 0.9
            else:
                ok = bool(got) and rec >= 0.9 and prec >= 0.9 and cell_acc >= 0.9
            items += 1; right += ok
            detail.append([t["kind"], "recall", round(rec, 2), "precision", round(prec, 2), "cells", round(cell_acc, 2), ok])
        else:  # value tables: grade the rows the answer gives
            numcols = [x for x in (t["rows"][0] if t["rows"] else {}) if x not in (keycol, "games", "player", "team", "week") and isinstance(t["rows"][0][x], (int, float))]
            n = r_ok = 0
            if t["kind"] == "points_allowed":
                for kv, g in got.items():
                    if kv not in want:
                        continue
                    for pos in t["positions"]:
                        gv = num(g.get(f"{pos}_per_game") or g.get(pos) or g.get(f"{pos.lower()}_per_game"))
                        if gv is None:
                            continue
                        n += 1; r_ok += any(abs(gv - want[kv][f"{pos}_per_game_{fm}"]) <= max(1.5, 0.1 * want[kv][f"{pos}_per_game_{fm}"]) for fm in ("ppr", "half", "std"))
                numcols = []
            for kv, g in got.items():
                if kv not in want or not numcols:
                    continue
                for col in numcols:
                    gv = num(g.get(col))
                    if gv is None:
                        continue
                    wv = want[kv][col]
                    if col in ("offense_pct", "target_share") and gv > 1.5:
                        gv /= 100
                    tol = 0.02 if col in ("offense_pct", "target_share") else (0.15 if isinstance(wv, float) else 0.5)
                    n += 1; r_ok += abs(gv - float(wv)) <= tol
            ok = n > 0 and r_ok / n >= 0.9 and (t["kind"] == "usage_report" or len(got) >= min(len(want), 1))
            items += 1; right += ok
            detail.append([t["kind"], "cells", n, "right", r_ok, ok])
    answered = any(d[4] is not None and d[5] != "optional" for d in detail if len(d) == 6) or answered_tables
    acc = right / max(1, items)
    return {"pass": bool(answered and acc >= 0.9), "items": items, "right": right, "acc": round(acc, 3), "detail": detail}


def live_name(name, pos=None):
    """player name for live matching; defenses become 'dst <ABBR>' whatever the site calls them"""
    n = norm(name)
    core = re.sub(r"\b(d st|dst|defense|def|special teams)\b", " ", n).split()
    ab = team_abbr(" ".join(core)) if core else None
    is_team = bool(ab) and all(w in TEAM_ALIASES[ab].split() for w in core)
    if is_team and (pos in ("DST", "DEF", "D/ST") or core != n.split() or len(core) <= 3):
        return f"dst {ab.lower()}"
    return n


def live_index(snaps):
    """site -> scoring -> player -> list of (points, positional rank) over all snapshots.
    Left out as not comparable: FF Calculator QBs (rankings-page points disagree with its own Week 6 stat line) and FantasyPros
    D/ST points (its projections and rankings pages show different numbers; its D/ST ranks are kept). Draft Sharks shows DS Proj,
    Consensus and 3D Proj side by side; each counts as a Draft Sharks projection."""
    idx = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for s in snaps:
        for site, d in s["sites"].items():
            if d.get("status") != "ok":
                continue
            for r in d["rows"]:
                if site == "ffcalc" and r.get("pos") == "QB":
                    continue
                pts = None if (site == "fantasypros" and r.get("pos") in ("DST", "DEF", "K")) else num(r.get("proj_pts"))
                key, sc = live_name(r["player"], r.get("pos")), r.get("scoring") or "ppr"
                idx[site][sc][key].append((pts, num(r.get("rank"))))
                if site == "draftsharks":
                    for alt in ("consensus_proj", "3d_proj"):
                        v = num((r.get("stats") or {}).get(alt))
                        if v is not None:
                            idx[site][sc][key].append((v, num(r.get("rank"))))
    return idx


def site_of(text):
    t = str(text or "").lower()
    return [s for s, pat in SITES.items() if re.search(pat, t)]


def scoring_of(text):
    t = str(text or "").lower()
    return "half" if re.search(r"half", t) else "std" if re.search(r"standard|\bstd\b|non.?ppr", t) else "ppr" if re.search(r"ppr", t) else None


FMTS = ("ppr", "half", "std")
NOT_PROJECTION = r"run defense|rushing defense|rush defense|yards allowed|points allowed"


def grade_live(q, ex, K, any_format=False):
    """Live (Week 6 projection) rubric, one rule set for every setup.
    Question level: FFToday-only questions (FFToday had not published Week 6 at run time) and questions about defensive stats
    allowed (not projections) are not gradable.
    Named players: every player (or D/ST) the question names that the key or the nflverse rosters know. A named player whose
    team is on bye must be reported as on bye / not playing.
    Each value in the answer: compared with the site it is attributed to (or the question's site); with no site, with the range of
    all captured sites. Format: the question's format if it names one, else the answer's label, else any format (any_format=True:
    any format always). Not checkable (left out, never wrong): sources we did not capture, a captured site that shows no such value
    for that player (paywalled rows), players in no captured table, overall ranks (keys hold positional ranks).
    "Out"/IR: right when no captured site projects the player above 0.5 points.
    Verdict: with named players, each is right (all its checked values right), wrong, missing (no row at all), or unverifiable.
    Pass = nothing wrong or missing and at least one non-bye player checked (or every named player is on bye). Not gradable when
    no named player can be checked, or only bye players can. Without named players (table questions): pass = at least 3 checked
    values and 90% of them right; fewer than 3 checked values is not gradable."""
    idx, byes = K["idx"], K["byes"]
    question = q["question"]
    qsites = site_of(question)
    if qsites == ["fftoday"]:
        return {"pass": None, "acc": 0.0, "items": 0, "right": 0, "named": [], "detail": [], "ungradable": "FFToday had not published Week 6"}
    if q["id"] in K.get("not_projection", {}):
        return {"pass": None, "acc": 0.0, "items": 0, "right": 0, "named": [], "detail": [], "ungradable": "not a projection request: " + K["not_projection"][q["id"]]}
    if re.search(NOT_PROJECTION, question, re.I):
        return {"pass": None, "acc": 0.0, "items": 0, "right": 0, "named": [], "detail": [], "ungradable": "defensive stats allowed, not a projection"}
    qscore = scoring_of(question)
    qn = f" {norm(question)} "
    named = []
    if re.search(r"d/?st|defen[cs]e", question, re.I):
        for ab, words in TEAM_ALIASES.items():
            if f" {words.split()[0]} " in qn:
                named.append(f"dst {ab.lower()}")
    for n in K["names"]:
        if f" {n} " in qn and n not in named:
            if re.search(rf"\b(without|no|minus|replacing) {re.escape(n)}\b", qn):
                qn = qn.replace(f" {n} ", " # "); continue  # context, not a requested player
            named.append(n); qn = qn.replace(f" {n} ", " # ")
    judged = []  # [player as written, reason, verdict True/False/None, key name]
    for r in ex.get("rows") or []:
        if not isinstance(r, dict):
            continue
        p = live_name(r.get("player"))
        if not p:
            continue
        status = str(r.get("status") or "").lower()
        pts, rank = num(r.get("proj_pts")), num(r.get("rank"))
        if p in byes:
            judged.append([r.get("player"), "bye", status in ("bye", "out") or (pts is not None and pts == 0), p]); continue
        attributed = str(r.get("site") or "").strip()
        asites = site_of(attributed)
        if attributed and not asites and not re.search(r"consensus|average|multiple|several", attributed.lower()):
            judged.append([r.get("player"), f"uncaptured source ({attributed})", None, p]); continue
        anywhere = [v for s in idx for f_ in FMTS for v in idx[s].get(f_, {}).get(p, [])]
        if status in ("out", "bye") and pts in (None, 0):
            said_out = re.search(r"\b(out|ir|injured reserve|reserve|inactive|ruled out|doubtful|suspended|bye)\b", K["answer_near"](r.get("player")))
            if asites and [s for s in asites if s in idx]:  # "not on Draft Sharks' board": checked against that site
                ok = not any(v[0] is not None and v[0] > 0.5 for s in asites if s in idx for f_ in FMTS for v in idx[s].get(f_, {}).get(p, []))
                judged.append([r.get("player"), "not listed by site", ok, p]); continue
            if not said_out:
                judged.append([r.get("player"), "no value", None, p]); continue
            ok = p in K["reserve"] or not any(v[0] is not None and v[0] > 1.0 for v in anywhere)  # official roster status first
            judged.append([r.get("player"), "out", ok, p] if (anywhere or p in K["reserve"]) else [r.get("player"), "out (not in key)", None, p]); continue
        sites = [s for s in (asites or ([] if attributed else qsites)) if s in idx] or ([] if asites else list(idx))
        if asites and not sites:
            judged.append([r.get("player"), "attributed site not captured", None, p]); continue
        fmt = qscore or scoring_of(r.get("scoring"))
        fmts = FMTS if fmt is None else (fmt,)
        cand = [v for s in sites for f_ in fmts for v in idx[s].get(f_, {}).get(p, [])]
        wide = [v for s in sites for f_ in FMTS for v in idx[s].get(f_, {}).get(p, [])] if any_format else cand  # lenient: same checkable values, any format
        if not [v for s in sites for f_ in FMTS for v in idx[s].get(f_, {}).get(p, [])]:
            judged.append([r.get("player"), "not in key", None, p]); continue
        if pts is not None:
            if not [v for v in cand if v[0] is not None]:
                judged.append([r.get("player"), "site shows no such value", None, p]); continue
            vals = [v[0] for v in wide if v[0] is not None]
            if asites or (qsites and not attributed):
                ok = any(abs(v - pts) <= 0.3 + 1e-6 for v in vals)
            else:
                ok = min(vals) - 0.3 - 1e-6 <= pts <= max(vals) + 0.3 + 1e-6
            judged.append([r.get("player"), "points", ok, p]); continue
        if rank is not None:
            if str(r.get("rank_type") or "position").lower().startswith("overall"):
                judged.append([r.get("player"), "overall rank (key holds positional ranks)", None, p]); continue
            if not [v for v in cand if v[1] is not None]:
                ranks = []
            else:
                ranks = [v[1] for v in wide if v[1] is not None]
            if not ranks:
                judged.append([r.get("player"), "site shows no such value", None, p]); continue
            ok = any(abs(v - rank) <= 2 for v in ranks) if (asites or qsites) else min(ranks) - 2 <= rank <= max(ranks) + 2
            judged.append([r.get("player"), "rank", ok, p]); continue
        judged.append([r.get("player"), "no value", None, p])
    checked = [j for j in judged if j[2] is not None]
    if named:
        per = {}
        for n in named:
            js = [j for j in judged if j[3] == n]
            cj = [j for j in js if j[2] is not None]
            if cj:
                per[n] = "right" if all(j[2] for j in cj) else "wrong"
            elif js and all(j[1] == "no value" for j in js):
                per[n] = "missing"
            elif js:
                per[n] = "unverifiable"
            elif n.startswith("dst ") and n in byes:
                per[n] = "bye defense left out"
            else:
                per[n] = "missing"
        decided = {n: v for n, v in per.items() if v in ("right", "wrong", "missing")}
        nonbye_checked = [n for n, v in per.items() if v in ("right", "wrong") and n not in byes]
        all_bye = all(n in byes for n in named)
        if not decided or (not nonbye_checked and not all_bye and not any(v in ("wrong", "missing") for v in per.values())):
            return {"pass": None, "acc": 0.0, "items": 0, "right": 0, "named": named, "per_player": per, "detail": judged[:80],
                    "ungradable": "no requested non-bye player could be checked"}
        ok = all(v == "right" for v in decided.values())
        acc = sum(1 for v in decided.values() if v == "right") / len(decided)
        return {"pass": ok, "acc": round(acc, 3), "items": len(decided), "right": sum(1 for v in decided.values() if v == "right"),
                "named": named, "per_player": per, "detail": judged[:80]}
    if not judged and not (ex.get("rows") or []):
        return {"pass": False, "acc": 0.0, "items": 0, "right": 0, "named": [], "detail": [], "note": "no values in the answer"}
    if len(checked) < 3:
        return {"pass": None, "acc": 0.0, "items": len(checked), "right": sum(1 for j in checked if j[2]), "named": [], "detail": judged[:80],
                "ungradable": "fewer than 3 checkable values"}
    acc = sum(1 for j in checked if j[2]) / len(checked)
    return {"pass": acc >= 0.9, "acc": round(acc, 3), "items": len(checked), "right": sum(1 for j in checked if j[2]), "named": [], "detail": judged[:80]}


def live_context(idx):
    """names the grader recognises (key + nflverse players) and who is on bye in Week 6 (from the schedule, not hand notes)"""
    import csv
    ref = ROOT / "data" / "ref"
    games = [g for g in csv.DictReader(open(ref / "games.csv")) if g["season"] == "2026" and g["game_type"] == "REG"]
    fix = {"LA": "LA", "LAR": "LA", "JAC": "JAX", "WSH": "WAS"}
    teams = {fix.get(t, t) for g in games for t in (g["home_team"], g["away_team"])}
    on6 = {fix.get(t, t) for g in games if g["week"] == "6" for t in (g["home_team"], g["away_team"])}
    bye_teams = teams - on6
    latest = {}
    for r in csv.DictReader(open(ref / "stats_player_week_2026.csv")):
        if r["season_type"] == "REG":
            latest[norm(r["player_display_name"])] = (int(r["week"]), fix.get(r["team"], r["team"]))
    for k in list(latest):
        if latest[k][0] < max(v[0] for v in latest.values()) - 2:
            pass  # keep: a player's last team is still his team for the bye check
    byes = {n for n, (_w, t) in latest.items() if t in bye_teams} | {f"dst {t.lower()}" for t in bye_teams}
    key_names = {n for site in idx.values() for sc in site.values() for n in sc}
    names = sorted({n for n in key_names | set(latest) if len(n.split()) >= 2 and not n.startswith("dst ")}, key=len, reverse=True)
    notp = json.load(open(ROOT / "data" / "questions" / "live_not_projection.json"))["questions"]
    status = {}  # latest roster status per player (ACT active; RES reserve/IR, INA inactive, EXE exempt, ...)
    for r in csv.DictReader(open(ref / "roster_2026.csv")):
        n = norm(r["full_name"])
        if n not in status or int(r["week"] or 0) >= status[n][0]:
            status[n] = (int(r["week"] or 0), r["status"])
    reserve = {n for n, (_w, st) in status.items() if st and st != "ACT"}
    return {"idx": idx, "byes": byes, "bye_teams": sorted(bye_teams), "names": names, "not_projection": notp, "reserve": reserve}


def alexandria_use(trace):
    calls = [t for t in trace if t["name"].startswith("mcp__firecrawl__")]
    ax = [t for t in calls if t["name"].endswith("find_tools") or (t["name"].endswith("firecrawl_scrape") and (t.get("input") or {}).get("alexandria"))]
    exe = [t for t in ax if t["name"].endswith("firecrawl_scrape")]
    return {"alexandria_calls": len(ax), "alexandria_exec": len(exe), "firecrawl_calls": len(calls)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--live-pre"); ap.add_argument("--live-post")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    rd = ROOT / "runs" / a.run_id
    M = json.load(open(rd / "manifest.json"))
    Q = {json.loads(l)["id"]: json.loads(l) for l in open(ROOT / M["questions"]["path"])}
    FK = {json.loads(l)["id"]: json.loads(l) for l in open(ROOT / "data" / "key" / "fact_key_verified.jsonl")}
    snaps = [json.load(open(p)) for p in (a.live_pre, a.live_post) if p]
    idx = live_index(snaps) if snaps else None
    K = live_context(idx) if idx else None
    (rd / "extract").mkdir(exist_ok=True)

    def one(c):
        d = rd / "answers" / c["id"] / c["arm"]
        res = json.load(open(d / "result.json")) if (d / "result.json").exists() else {"is_error": True, "answer": ""}
        trace = json.load(open(d / "trace.json")) if (d / "trace.json").exists() else []
        q = Q[c["id"]]
        base = {"id": c["id"], "arm": c["arm"], "bucket": q["bucket"], "error": bool(res.get("is_error")), "wall": res.get("wall"),
                "cost": res.get("total_cost_usd"), "tool_calls": res.get("tool_calls"), **alexandria_use(trace)}
        if res.get("is_error") or not res.get("answer"):
            return {**base, "pass": False, "acc": 0.0, "items": 0, "right": 0, "detail": ["no answer"]}
        cache = rd / "extract" / f"{c['id']}__{c['arm']}.json"
        if q["bucket"] == "fact":
            k = FK[c["id"]]
            slots, tables = fact_request(k)
            if cache.exists():
                ex = json.load(open(cache))
            else:
                ex, _u = claude_json(SYS_FACT, f"QUESTION:\n{q['prompt']}\n\nSLOTS (slot_id: player | week | field):\n{json.dumps(slots, indent=0)}\n\nTABLES (table_id: what to list):\n{json.dumps(tables, indent=0)}\n\nANSWER:\n{res['answer']}")
                json.dump(ex, open(cache, "w"))
            return {**base, **grade_fact(k, ex)}
        if idx is None:
            return {**base, "pass": None, "detail": ["live key not provided"]}
        if cache.exists():
            ex = json.load(open(cache))
        else:
            ex, _u = claude_json(SYS_LIVE, f"QUESTION:\n{q['prompt']}\n\nANSWER:\n{res['answer']}")
            json.dump(ex, open(cache, "w"))
        text = res["answer"].lower()

        def near(player, text=text):
            last = norm(player or "").split()[-1:] or [""]
            hits = [m.start() for m in re.finditer(re.escape(last[0]), text)] if last[0] else []
            return " ".join(text[max(0, h - 120): h + 160] for h in hits)
        Kc = {**K, "answer_near": near}
        strict = grade_live(q, ex, Kc)
        lenient = grade_live(q, ex, Kc, any_format=True)
        return {**base, **strict, "pass_any_format": lenient["pass"], "acc_any_format": lenient["acc"]}

    def safe(c):
        try:
            return one(c)
        except Exception as e:  # recorded as an extraction failure, never a silent pass
            return {"id": c["id"], "arm": c["arm"], "bucket": Q[c["id"]]["bucket"], "error": False, "extract_error": str(e)[:200], "pass": None,
                    "acc": 0.0, "items": 0, "right": 0, "detail": [], "wall": None, "cost": None, "tool_calls": None,
                    "alexandria_calls": 0, "alexandria_exec": 0, "firecrawl_calls": 0}

    with ThreadPoolExecutor(a.workers) as pool:
        G = list(pool.map(safe, M["expected"]))
    fails = [g for g in G if g.get("extract_error")]
    if fails:
        print(f"WARNING {len(fails)} extraction failures:", [(g["id"], g["arm"]) for g in fails][:10])
    with open(rd / "grades.jsonl", "w") as f:
        for g in G:
            f.write(json.dumps(g) + "\n")
    import hashlib, shutil, datetime as _dt
    sha = lambda p_: hashlib.sha256(open(p_, "rb").read()).hexdigest()
    me = Path(__file__).resolve()
    (rd / "grader_versions").mkdir(exist_ok=True)
    shutil.copy(me, rd / "grader_versions" / f"grade_{sha(me)[:12]}.py")
    keys = [p_ for p_ in (a.live_pre, a.live_post) if p_] + [str(ROOT / "data" / "key" / "fact_key_verified.jsonl"),
                                                             str(ROOT / "data" / "questions" / "live_not_projection.json")]
    json.dump({"graded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "grade_py_sha256": sha(me),
               "grader_copy": f"grader_versions/grade_{sha(me)[:12]}.py", "extractor_model": EXTRACTOR,
               "extractor_note": "extractions are cached in extract/; caches written before 2026-10-15 were made by the same model",
               "questions_sha256": sha(ROOT / M["questions"]["path"]), "keys": {str(Path(k_).name): sha(k_) for k_ in keys},
               "bye_teams": K["bye_teams"] if K else None}, open(rd / "grading.json", "w"), indent=1)
    by = defaultdict(lambda: defaultdict(list))
    for g in G:
        by[g["arm"]][g["bucket"]].append(g)
    for arm in M["arms"]:
        parts = []
        for b in ("fact", "live"):
            xs = [g for g in by[arm][b] if g["pass"] is not None]
            if xs:
                parts.append(f"{b} {sum(g['pass'] for g in xs)}/{len(xs)} pass, item acc {sum(g['acc'] for g in xs) / len(xs):.0%}")
        allx = by[arm]["fact"] + by[arm]["live"]
        parts.append(f"${sum(g['cost'] or 0 for g in allx):.2f}, {sum(g['wall'] or 0 for g in allx) / max(1, len(allx)):.0f}s avg, alexandria exec in {sum(1 for g in allx if g['alexandria_exec'])}/{len(allx)}")
        print(f"{arm:18} " + " | ".join(parts))


if __name__ == "__main__":
    main()
