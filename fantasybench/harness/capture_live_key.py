"""Capture the live answer key: each site's current NFL 2026 Week 6 fantasy projections / weekly rankings.

Pulled straight from the sites' own public pages (no Firecrawl Alexandria). Plain HTTP first; FFToday sits behind a
Cloudflare JS check, so it falls back to a plain Firecrawl scrape (rawHtml, maxAge 0). No logins; paywalled rows are
not fetched (FantasyData shows only the top few rows per table to anonymous users, and that is all we record).

python3 scripts/capture_live_key.py --label pre   -> data/key/live/week6_pre_<UTC ts>.json
python3 scripts/capture_live_key.py --label post --sites cbs,startwho
"""
import argparse, html, json, re, sys, time, urllib.error, urllib.parse, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "key" / "live"
ENV = ROOT.parent / "harness" / ".env.eval"
SEASON, WEEK = 2026, 6
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"

# ---------------------------------------------------------------- teams
TEAM_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers", "CHI": "Chicago Bears", "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys", "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
    "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams", "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings", "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers", "SF": "San Francisco 49ers",
    "SEA": "Seattle Seahawks", "TB": "Tampa Bay Buccaneers", "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}
ALIAS = {"LA": "LAR", "JAC": "JAX", "WSH": "WAS", "LVR": "LV", "OAK": "LV", "SD": "LAC", "STL": "LAR", "GNB": "GB",
         "KAN": "KC", "NWE": "NE", "NOR": "NO", "SFO": "SF", "TAM": "TB"}
_BY_NAME = {}
for _a, _n in TEAM_NAMES.items():
    _BY_NAME[_n.lower()] = _a
    _BY_NAME[_n.split()[-1].lower()] = _a  # nickname ("Chiefs", "49ers")
_BY_NAME.update({"washington": "WAS", "commanders": "WAS"})


def team(s):
    """Any site's team string (abbr, full name, nickname, 'Chiefs D/ST') -> standard abbr, or None."""
    if not s:
        return None
    s = clean(s)
    up = s.upper()
    if up in TEAM_NAMES:
        return up
    if up in ALIAS:
        return ALIAS[up]
    low = re.sub(r"\s*(d/st|dst|defense|def)$", "", s.lower()).strip()
    if low in _BY_NAME:
        return _BY_NAME[low]
    for n, a in _BY_NAME.items():
        if len(n) > 4 and n in low:
            return a
    return None


def dst_name(abbr):
    return TEAM_NAMES.get(abbr, abbr) + " D/ST" if abbr else None


# ---------------------------------------------------------------- http
def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def num(s):
    s = clean(str(s)).replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


RAW = None  # --save-raw: every fetched page body is kept, gzipped, with an index, so the key can be audited later


def keep_raw(url, status, text, via):
    if RAW is None:
        return
    import gzip, hashlib
    h = hashlib.sha1(f"{via} {url}".encode()).hexdigest()[:16]
    with gzip.open(RAW / f"{h}.html.gz", "wt", encoding="utf-8") as fh:
        fh.write(text or "")
    with open(RAW / "index.jsonl", "a") as fh:
        fh.write(json.dumps({"url": url, "via": via, "status": status, "file": f"{h}.html.gz", "bytes": len(text or ""),
                             "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")


def get(url, headers=None, tries=2):
    """Plain HTTP GET. Returns (status, text)."""
    h = {"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en"}
    h.update(headers or {})
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                st, tx = r.status, r.read().decode("utf-8", "replace")
                keep_raw(url, st, tx, "http"); return st, tx
        except urllib.error.HTTPError as e:
            st, tx = e.code, e.read().decode("utf-8", "replace")
            keep_raw(url, st, tx, "http"); return st, tx
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2)


def firecrawl_key():
    for line in ENV.read_text().splitlines():
        if line.startswith("FIRECRAWL_API_KEY="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError("FIRECRAWL_API_KEY not in " + str(ENV))


def firecrawl_scrape(url):
    """Plain Firecrawl scrape (no Alexandria). Returns (statusCode, rawHtml)."""
    body = {"url": url, "formats": ["rawHtml"], "maxAge": 0}
    req = urllib.request.Request("https://api.firecrawl.dev/v2/scrape", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + firecrawl_key(),
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.load(r)
    data = d.get("data") or {}
    st, tx = (data.get("metadata") or {}).get("statusCode"), data.get("rawHtml") or ""
    keep_raw(url, st, tx, "firecrawl-plain-scrape")
    return st, tx


def row(player, tm, pos, scoring, proj, rank=None, stats=None):
    r = {"player": clean(player), "team": tm, "pos": pos, "scoring": scoring, "proj_pts": proj, "rank": rank}
    if stats:
        r["stats"] = stats
    return r


def site(status, url_template, note, rows=None, urls=None):
    return {"status": status, "url_template": url_template, "note": note, "rows": rows or [], "urls_fetched": urls or []}


# ---------------------------------------------------------------- 1. FFToday
FFT_POS = {"QB": 10, "RB": 20, "WR": 30, "TE": 40, "K": 80, "DST": 99}
FFT_LEAGUE = {"std": 1, "half": 193033, "ppr": 107644}  # FFToday Standard / Half-PPR / PPR presets


def _fft_fetch(url):
    st, h = get(url)
    via = "http"
    if st == 403 or "Just a moment" in h[:3000] or "_cf_chl_opt" in h:
        st, h = firecrawl_scrape(url)
        via = "firecrawl"
    return st, h, via


def _fft_parse(h, pos, scoring):
    groups = []
    m = re.search(r'<tr class="tablehdr">(.*?)</tr>', h, re.S)
    if m:
        for attrs, txt in re.findall(r"<td([^>]*)>(.*?)</td>", m.group(1), re.S):
            cs = re.search(r'colspan="(\d+)"', attrs)
            groups += [clean(txt).lower()] * int(cs.group(1) if cs else 1)
    m = re.search(r'<tr class="tableclmhdr">(.*?)</tr>', h, re.S)
    labels = [clean(x) for x in re.findall(r"<b>(.*?)</b>", m.group(1), re.S)] if m else []
    cols = []
    for i, lab in enumerate(labels):
        g = groups[i] if i < len(groups) else ""
        cols.append(f"{g}_{lab.lower()}" if g and g not in ("fantasy",) and lab not in ("Player", "Team", "Opp", "Chg")
                    else lab.lower())
    rows = []
    for tr in re.findall(r"<tr>\s*(<td class=\"bodycontent\".*?)</tr>", h, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(cells) != len(cols) or "fpts" not in cols:
            continue
        d = dict(zip(cols, cells))
        name = clean(d.get("player"))
        tm = team(d.get("team")) or team(name)
        if pos == "DST":
            tm = team(name) or tm
            name = dst_name(tm) or name
        stats = {k: num(v) for k, v in d.items() if k not in ("chg", "player", "team", "opp", "fpts")}
        stats["opp"] = clean(d.get("opp"))
        rows.append(row(name, tm, pos, scoring, num(d["fpts"]), None, stats))
    return rows


def cap_fftoday():
    tpl = ("https://www.fftoday.com/rankings/playerwkproj.php?Season=2026&GameWeek=6&PosID={PosID}"
           "&LeagueID={LeagueID}  (PosID QB10 RB20 WR30 TE40 K80 DST99; LeagueID std1 half193033 ppr107644)")
    rows, urls, via_used, bad = [], [], set(), []
    for scoring, lid in FFT_LEAGUE.items():
        for pos, pid in FFT_POS.items():
            url = f"https://www.fftoday.com/rankings/playerwkproj.php?Season={SEASON}&GameWeek={WEEK}&PosID={pid}&LeagueID={lid}"
            st, h, via = _fft_fetch(url)
            urls.append(url)
            via_used.add(via)
            title = re.search(r"Projections:\s*(\d{4})\s*Week\s*(\d+)", clean(h))
            if not title or (int(title.group(1)), int(title.group(2))) != (SEASON, WEEK):
                msg = "database error page" if "technical issues with our database" in h else f"status {st}, no Week 6 title"
                bad.append(f"{pos}/{scoring}: {msg}")
                if not rows:  # first probe failed -> Week 6 not up; don't burn 17 more scrapes
                    _, dh, _ = _fft_fetch("https://www.fftoday.com/rankings/playerwkproj.php?PosID=10")
                    cur = re.search(r"Projections:\s*(\d{4})\s*Week\s*(\d+)", clean(dh))
                    cur = f"{cur.group(1)} Week {cur.group(2)}" if cur else "unknown"
                    return site("error", tpl, f"Week 6 page not available ({msg}); site default page currently shows "
                                f"{cur}. Fetched via {'+'.join(sorted(via_used))} (plain HTTP gets a Cloudflare 403). "
                                "Re-run later.", [], urls)
                continue
            rows += _fft_parse(h, pos, scoring)
    note = f"Projections only (no ranks). Fetched via {'+'.join(sorted(via_used))}; plain HTTP is Cloudflare-blocked (403)."
    if bad:
        note += " Missing: " + "; ".join(bad)
    return site("ok" if rows else "error", tpl, note, rows, urls)


# ---------------------------------------------------------------- 2. CBS Sports
CBS_SCORING = {"ppr": "ppr", "std": "nonppr"}  # CBS has no half-PPR projections page


def cap_cbs():
    tpl = "https://www.cbssports.com/fantasy/football/stats/{POS}/2026/6/projections/{ppr|nonppr}/"
    rows, urls, notes = [], [], []
    for scoring, slug in CBS_SCORING.items():
        for pos in POSITIONS:
            url = f"https://www.cbssports.com/fantasy/football/stats/{pos}/{SEASON}/{WEEK}/projections/{slug}/"
            st, h = get(url)
            urls.append(url)
            if st != 200 or f"Week {WEEK} Proj" not in h:
                notes.append(f"{pos}/{scoring}: status {st} or no 'Week {WEEK} Proj' title")
                continue
            th = h[h.find("<thead"):h.find("</thead>")]
            cols = [urllib.parse.unquote(c) for c in re.findall(r"sortcol=([^&\"]+)", th)]
            body = h[h.find("<tbody"):h.find("</tbody>", h.find("<tbody"))]
            for tr in re.findall(r'<tr class="TableBase-bodyTr[^"]*">(.*?)</tr>', body, re.S):
                cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
                if len(cells) != len(cols):
                    continue
                if pos == "DST":
                    m = re.search(r"/nfl/teams/([A-Z]+)/", cells[0])
                    tm = team(m.group(1)) if m else team(clean(cells[0]))
                    name = dst_name(tm)
                else:
                    long = re.search(r'CellPlayerName--long">(.*?)</span></span></span>', cells[0], re.S)
                    seg = long.group(1) if long else cells[0]
                    name = clean(re.search(r"<a [^>]*>(.*?)</a>", seg, re.S).group(1))
                    tms = re.findall(r'CellPlayerName-team">(.*?)</span>', cells[0], re.S)
                    tm = team(clean(tms[-1])) if tms else None
                vals = {c: num(v) for c, v in zip(cols[1:], cells[1:])}
                stats = {k: v for k, v in vals.items() if k not in ("misc_fpts", "fpts", "misc_fppg", "fppg", "gp")}
                proj = vals.get("misc_fpts", vals.get("fpts"))
                rows.append(row(name, tm, pos, scoring, proj, None, stats))
    note = "Projections only (no ranks); CBS offers PPR and Non-PPR (std), no half-PPR."
    if notes:
        note += " Issues: " + "; ".join(notes)
    return site("ok" if rows else "error", tpl, note, rows, urls)


# ---------------------------------------------------------------- 3. FantasyData
FD_SCORING = {"ppr": "fpts_ppr", "half": "fpts_half_ppr", "std": "fpts"}
FD_POS = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k", "DST": "dst"}


def cap_fantasydata():
    tpl = ("https://fantasydata.com/nfl/fantasy-football-weekly-projections?scope=game&season=2026&spw=2026_REG_6"
           "&position={qb|rb|wr|te|k|dst}&scoring={fpts_ppr|fpts_half_ppr|fpts}&order_by=<scoring>&sort_dir=desc")
    rows, urls, paywalled, issues = [], [], 0, []
    for scoring, sc in FD_SCORING.items():
        for pos, p in FD_POS.items():
            url = ("https://fantasydata.com/nfl/fantasy-football-weekly-projections?"
                   + urllib.parse.urlencode({"scope": "game", "season": SEASON, "spw": f"{SEASON}_REG_{WEEK}",
                                             "position": p, "scoring": sc, "order_by": sc, "sort_dir": "desc"}))
            st, h = get(url)
            urls.append(url)
            if st != 200 or not re.search(rf'value="{SEASON}_REG_{WEEK}"\s+selected', h):
                issues.append(f"{pos}/{scoring}: status {st} or Week {WEEK} not selected")
                continue
            th = h[h.find("<thead"):h.find("</thead>")]
            cols = re.findall(r'data-id="([^"]+)"', th)
            body = h[h.find("<tbody>"):h.find("</tbody>")]
            paywalled += body.count("SIGN UP TO SEE MORE")
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
                cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
                if len(cells) != len(cols):
                    continue
                d = dict(zip(cols, cells))
                wk = num(d.get("game.week"))
                if wk is not None and int(wk) != WEEK:
                    continue
                if pos == "DST":
                    tm = team(clean(d.get("team")))
                    name = dst_name(tm)
                else:
                    name, tm = clean(d.get("name") or d.get("player")), team(clean(d.get("team")))
                stats = {k: num(v) for k, v in d.items()
                         if k not in ("rank", "name", "player", "team", "pos", "position", "opp", "game.week", sc)}
                stats["opp"] = clean(d.get("opp"))
                rows.append(row(name, tm, pos, scoring, num(d.get(sc)), None, stats))
    note = ("PAYWALLED: anonymous users see only the top ~3 rows per table (a 'SIGN UP TO SEE MORE' row hides the "
            f"rest; {paywalled} hidden-row markers). Rows here are only what is publicly visible; ranks not recorded.")
    if issues:
        note += " Issues: " + "; ".join(issues)
    return site("ok" if rows else "blocked", tpl, note, rows, urls)


# ---------------------------------------------------------------- 4. Draft Sharks
DS_SCORING = {"ppr": "ppr", "half": "half-ppr", "std": ""}
DS_POS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "DST": "DEF"}


def cap_draftsharks():
    tpl = ("https://www.draftsharks.com/weekly-rankings/load-table?pprSuperflexSlug={ppr|half-ppr|''}"
           "&fantasyPosition={QB|RB|WR|TE|K|DEF}&researchDepth=rankings&playerGroup=all&week=6 "
           "(the htmx endpoint behind the position filter on https://www.draftsharks.com/weekly-rankings/6/{ppr|half-ppr})")
    rows, urls, issues = [], [], []
    page = "https://www.draftsharks.com/weekly-rankings/6/ppr"
    st, h = get(page)
    urls.append(page)
    if st != 200 or f'name="week" value="{WEEK}"' not in h or f"Week {WEEK} PPR Rankings {SEASON}" not in h:
        return site("error", tpl, f"landing page status {st} / no Week {WEEK} title", [], urls)
    for scoring, slug in DS_SCORING.items():
        for pos, fp in DS_POS.items():
            url = "https://www.draftsharks.com/weekly-rankings/load-table?" + urllib.parse.urlencode(
                {"pprSuperflexSlug": slug, "fantasyPosition": fp, "researchDepth": "rankings", "playerGroup": "all",
                 "sort": "", "selectedTeam": "", "playerSearchTerm": "", "week": WEEK})
            st, h = get(url, {"HX-Request": "true", "Referer": page})
            urls.append(url)
            n0 = len(rows)
            for b in re.findall(r"<tbody\s+data-player-row(.*?)</tbody>", h, re.S):
                if re.search(r'data-fantasy-position="([^"]*)"', b).group(1) != fp:
                    continue  # table pads the filtered list with other positions
                name = html.unescape(re.search(r'data-player-name="([^"]*)"', b).group(1))
                rk = re.search(r'rank-index">\s*<span>\s*(\d+)', b)
                vals = {k: v for v, k in re.findall(r'data-value="([^"]*)" data-attribute="([^"]*)"', b)}
                if pos == "DST":
                    tm = team(name)
                    name = dst_name(tm)
                else:
                    tm = team(clean((re.search(r'team-name">(.*?)</span>', b, re.S) or [None, ""])[1]))
                stats = {"consensus_proj": num(vals.get("consensusWeeklyPts", "")),
                         "floor_proj": num(vals.get("weeklyFloorPts", "")),
                         "ceiling_proj": num(vals.get("weeklyCeilingPts", "")),
                         "3d_proj": num(vals.get("weekly3dPts", "")), "matchup": vals.get("matchup")}
                rows.append(row(name, tm, pos, scoring, num(vals.get("weeklyPts", "")),
                                int(rk.group(1)) if rk else None, stats))
            if len(rows) == n0:
                issues.append(f"{pos}/{scoring}: status {st}, 0 rows")
    note = ("Weekly rankings; rank = Draft Sharks positional rank, proj_pts = 'DS Proj' (median), stats carry "
            "consensus/floor/ceiling/3D. Std = Non-PPR. The landing page server-renders only the overall top 25.")
    if issues:
        note += " Issues: " + "; ".join(issues)
    return site("ok" if rows else "error", tpl, note, rows, urls)


# ---------------------------------------------------------------- 5. Fantasy Football Calculator
FFC_SCORING = {"ppr": "ppr", "half": "half-ppr", "std": "standard"}
FFC_POS = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "kicker", "DST": "defense"}


def cap_ffcalc():
    tpl = "https://fantasyfootballcalculator.com/rankings/{ppr|half-ppr|standard}/{qb|rb|wr|te|kicker|defense}"
    rows, urls, issues, updated = [], [], [], set()
    for scoring, s in FFC_SCORING.items():
        for pos, p in FFC_POS.items():
            url = f"https://fantasyfootballcalculator.com/rankings/{s}/{p}"
            st, h = get(url)
            urls.append(url)
            if st != 200 or not re.search(rf"<title>[^<]*Week {WEEK}\b", h):
                issues.append(f"{pos}/{scoring}: status {st} or title not Week {WEEK}")
                continue
            m = re.search(r"Last updated:\s*([A-Za-z]+ \d+, \d{4})", clean(h))
            if m:
                updated.add(m.group(1))
            body = h[h.find("<tbody>"):h.find("</tbody>")]
            for tr in re.findall(r"<tr>(.*?)</tr>", body, re.S):
                c = [clean(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
                if len(c) < 6:
                    continue
                tm = team(c[2])
                name = dst_name(tm) if pos == "DST" else c[1]
                rows.append(row(name, tm, pos, scoring, num(c[5]), int(num(c[0].rstrip("."))),
                                {"bye": num(c[4])}))
    note = (f"Weekly rankings with 'Proj. FP'. No public API for weekly data (only /api/v1/adp). Page 'Last updated': "
            f"{', '.join(sorted(updated)) or 'n/a'}.")
    if issues:
        note += " Issues: " + "; ".join(issues)
    return site("ok" if rows else "error", tpl, note, rows, urls)


# ---------------------------------------------------------------- 6. StartWho
SW_SCORING = {"ppr": "PPR", "half": "Half-PPR", "std": "Standard"}
SW_POS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "DST": "DEF"}


def cap_startwho():
    tpl = ("https://startwho.com/api/projections?sport=NFL&format={PPR|Half-PPR|Standard}&position={QB|RB|WR|TE|K|DEF} "
           "(JSON the https://startwho.com/nfl page itself calls; page SSR embeds the same PPR list)")
    rows, urls, issues, upd = [], [], [], set()
    st, h = get("https://startwho.com/nfl")
    urls.append("https://startwho.com/nfl")
    if f"Week {WEEK}" not in h:
        issues.append(f"/nfl page status {st}, no 'Week {WEEK}' text")
    for scoring, f in SW_SCORING.items():
        for pos, p in SW_POS.items():
            url = "https://startwho.com/api/projections?" + urllib.parse.urlencode(
                {"sport": "NFL", "format": f, "position": p})
            st, txt = get(url, {"Accept": "application/json", "Referer": "https://startwho.com/nfl"})
            urls.append(url)
            try:
                d = json.loads(txt)
            except ValueError:
                issues.append(f"{pos}/{scoring}: status {st}, not JSON")
                continue
            if d.get("weekNumber") != WEEK:
                issues.append(f"{pos}/{scoring}: weekNumber={d.get('weekNumber')}")
                continue
            upd.add(d.get("lastUpdated"))
            for i, pr in enumerate(d.get("projections") or [], 1):
                if pr.get("week") not in (None, WEEK):
                    continue
                tm = team(pr.get("team"))
                name = dst_name(tm) if pos == "DST" else pr.get("playerName")
                stats = {x["propType"]: x.get("evValue") for x in pr.get("props") or [] if x.get("propType")}
                stats.update({"opponent": pr.get("opponent"), "vegas_percent": pr.get("vegasPercent"),
                              "locked": pr.get("locked")})
                rows.append(row(name, tm, pos, scoring, pr.get("fantasyPoints"), i, stats))
    note = ("Betting-market projections; rank = order within position as served (sorted by projected points). "
            f"stats = market prop expectations. API lastUpdated: {', '.join(sorted(x for x in upd if x)) or 'n/a'}.")
    if issues:
        note += " Issues: " + "; ".join(issues)
    return site("ok" if rows else "error", tpl, note, rows, urls)


# ---------------------------------------------------------------- 7. FantasyPros
FP_SCORING = {"ppr": "PPR", "half": "HALF", "std": "STD"}
FP_POS = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k", "DST": "dst"}
FP_ECR_PREFIX = {"ppr": "ppr-", "half": "half-point-ppr-", "std": ""}  # QB/K/DST ECR has a single (STD) list


def _fp_ecr(pos, scoring):
    p = FP_POS[pos]
    slug = (FP_ECR_PREFIX[scoring] if pos in ("RB", "WR", "TE") else "") + p
    url = f"https://www.fantasypros.com/nfl/rankings/{slug}.php?week={WEEK}"
    st, h = get(url)
    m = re.search(r"ecrData = (\{.*?\});\s", h, re.S)
    if st != 200 or not m:
        return url, None, f"ECR {pos}/{scoring}: status {st}, no ecrData"
    d = json.loads(m.group(1))
    if str(d.get("week")) != str(WEEK) or str(d.get("year")) != str(SEASON):
        return url, None, f"ECR {pos}/{scoring}: week={d.get('week')} year={d.get('year')}"
    return url, {p_["player_id"]: p_ for p_ in d.get("players") or []}, None


def cap_fantasypros():
    tpl = ("https://www.fantasypros.com/nfl/projections/{qb|rb|wr|te|k|dst}.php?week=6&scoring={PPR|HALF|STD}; "
           "ECR rank from https://www.fantasypros.com/nfl/rankings/{[ppr-|half-point-ppr-]rb|wr|te, qb, k, dst}.php?week=6")
    rows, urls, issues, ecr_only = [], [], [], 0
    for scoring, sc in FP_SCORING.items():
        for pos, p in FP_POS.items():
            url = f"https://www.fantasypros.com/nfl/projections/{p}.php?week={WEEK}&scoring={sc}"
            st, h = get(url)
            urls.append(url)
            if st != 200 or not re.search(rf"<title>Week {WEEK}\b[^<]*Projections", h):
                issues.append(f"proj {pos}/{scoring}: status {st} or title not Week {WEEK}")
                continue
            eurl, ecr, err = _fp_ecr(pos, scoring)
            urls.append(eurl)
            if err:
                issues.append(err)
            ecr = ecr or {}
            th = h[h.find("<thead"):h.find("</thead>")]
            labels = [clean(x) for x in re.findall(r"<th[^>]*>\s*<small>(.*?)</small>", th, re.S)]
            m = re.search(r'<tr class="tablehdr[^"]*">(.*?)</tr>', th, re.S) or re.search(r"<tr[^>]*>(.*?)</tr>", th, re.S)
            groups = []
            for attrs, txt in re.findall(r"<td([^>]*)>(.*?)</td>", m.group(1) if m else "", re.S):
                cs = re.search(r'colspan="(\d+)"', attrs)
                groups += [clean(txt).lower()] * int(cs.group(1) if cs else 1)
            groups = groups[1:]  # first group cell spans the Player column
            cols = [(f"{groups[i]}_{lab.lower()}" if i < len(groups) and groups[i] not in ("", "misc") else lab.lower())
                    for i, lab in enumerate(labels)]
            body = h[h.find("<tbody>"):h.find("</tbody>")]
            seen = set()
            for tr in re.findall(r'<tr class="mpb-player-[^"]*">(.*?)</tr>', body, re.S):
                cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
                if len(cells) != len(cols) + 1:
                    continue
                pid = re.search(r"fp-id-(\d+)", cells[0])
                pid = int(pid.group(1)) if pid else None
                seen.add(pid)
                name = clean(re.search(r"<a [^>]*>(.*?)</a>", cells[0], re.S).group(1))
                if pos == "DST":
                    tm = team(name)
                    name = dst_name(tm)
                else:
                    tm = team(clean(re.sub(r"<a .*?</a>", "", cells[0], flags=re.S)))
                vals = dict(zip(cols, (num(c) for c in cells[1:])))
                proj = vals.pop("fpts", None)
                e = ecr.get(pid)
                if e:
                    vals.update({"ecr_pos_rank": e.get("pos_rank"), "ecr_rank_ave": num(e.get("rank_ave") or ""),
                                 "ecr_rank_min": num(e.get("rank_min") or ""), "ecr_rank_max": num(e.get("rank_max") or ""),
                                 "opponent": e.get("player_opponent"), "ecr_r2p_pts": num(e.get("r2p_pts") or "")})
                rows.append(row(name, tm, pos, scoring, proj, e.get("rank_ecr") if e else None, vals))
            for k, e in ecr.items():  # ECR players beyond the registration-fenced projection rows
                if k in seen:
                    continue
                ecr_only += 1
                tm = team(e.get("player_team_id")) or team(e.get("player_name"))
                name = dst_name(tm) if pos == "DST" else e.get("player_name")
                rows.append(row(name, tm, pos, scoring, None, e.get("rank_ecr"),
                                {"ecr_pos_rank": e.get("pos_rank"), "ecr_rank_ave": num(e.get("rank_ave") or ""),
                                 "ecr_rank_min": num(e.get("rank_min") or ""), "ecr_rank_max": num(e.get("rank_max") or ""),
                                 "opponent": e.get("player_opponent"), "ecr_r2p_pts": num(e.get("r2p_pts") or "")}))
    note = ("Projections pages show only the top 10 rows per position to anonymous users (a registration fence "
            "hides the rest; not bypassed), so proj_pts is set only for those. rank = FantasyPros Week 6 expert "
            "consensus (ECR) positional rank from the public rankings pages (full lists, in-page ecrData). "
            f"{ecr_only} ECR-only rows have proj_pts null (stats.ecr_r2p_pts = ECR feed's r2p_pts, meaning unverified). "
            "QB/K/DST ECR is one list shared by all formats; RB/WR/TE ECR is per format.")
    if issues:
        note += " Issues: " + "; ".join(issues)
    return site("ok" if rows else "error", tpl, note, rows, urls)


SITES = {"fftoday": cap_fftoday, "cbs": cap_cbs, "fantasydata": cap_fantasydata, "draftsharks": cap_draftsharks,
         "ffcalc": cap_ffcalc, "startwho": cap_startwho, "fantasypros": cap_fantasypros}

# ---------------------------------------------------------------- validation
SPOT = ["Josh Allen", "Christian McCaffrey", "Ja'Marr Chase", "Puka Nacua", "Trey McBride"]


def norm_name(s):
    return re.sub(r"[^a-z]", "", (s or "").lower().replace("jr.", "").replace(" iii", "").replace(" ii", ""))


def validate(out):
    for name, s in out["sites"].items():
        print(f"\n== {name}: {s['status']} ({len(s['rows'])} rows)  {s['note'][:300]}")
        if not s["rows"]:
            continue
        cnt = Counter((r["pos"], r["scoring"]) for r in s["rows"])
        for sc in ("ppr", "half", "std"):
            line = "  ".join(f"{p}:{cnt[(p, sc)]}" for p in POSITIONS if cnt[(p, sc)])
            if line:
                print(f"  {sc:4} {line}")
        bad_team = Counter(r["team"] for r in s["rows"] if r["team"] not in TEAM_NAMES)
        bad_name = [r["player"] for r in s["rows"] if not r["player"] or re.search(r"[<>|]|\s{2}|^\W", r["player"])]
        null_pts = sum(r["proj_pts"] is None for r in s["rows"])
        if bad_team or bad_name or null_pts:
            print(f"  WARN bad teams {dict(bad_team)}; bad names {bad_name[:5]}; null proj {null_pts}")
        hi = defaultdict(float)
        for r in s["rows"]:
            hi[r["pos"]] = max(hi[r["pos"]], r["proj_pts"] or 0)
        print("  max proj by pos: " + ", ".join(f"{p}={hi[p]:.1f}" for p in POSITIONS if p in hi)
              + ("  WARN looks like season totals" if max(hi.values()) > 60 else ""))
        for who in SPOT:
            hits = [r for r in s["rows"] if norm_name(r["player"]) == norm_name(who)]
            if hits:
                print(f"  {who} ({hits[0]['team']} {hits[0]['pos']}): "
                      + ", ".join(f"{r['scoring']}={r['proj_pts']}" + (f"#{r['rank']}" if r['rank'] else "")
                                  for r in hits))
            else:
                print(f"  {who}: not listed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--sites", default=",".join(SITES))
    ap.add_argument("--save-raw", action="store_true", help="keep every fetched page under data/key/live/raw/<label>_<ts>/")
    a = ap.parse_args()
    global RAW
    if a.save_raw:
        RAW = OUT / "raw" / f"{a.label}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        RAW.mkdir(parents=True, exist_ok=True)
    out = {"captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "week": WEEK, "season": SEASON,
           "sites": {}}
    for name in a.sites.split(","):
        t0 = time.time()
        try:
            out["sites"][name] = SITES[name]()
        except Exception as e:  # one broken site must not sink the capture
            out["sites"][name] = site("error", "", f"{type(e).__name__}: {e}")
        print(f"[{name}] {out['sites'][name]['status']} {len(out['sites'][name]['rows'])} rows "
              f"({time.time() - t0:.0f}s)", file=sys.stderr)
    OUT.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT / f"week{WEEK}_{a.label}_{ts}.json"
    path.write_text(json.dumps(out, indent=1))
    validate(out)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
