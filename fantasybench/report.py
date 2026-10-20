"""fantasybench-report: re-grade every saved answer offline and print the results.

uv run fantasybench-report                 # re-grade, regenerate every number, print the tables
uv run fantasybench-report --out r.json    # also save the regenerated results

No API keys, no network, no model calls. Expands the repository into a scratch workspace in the layout the frozen scripts
read (fantasybench/workspace.py), so nothing in the repository is modified. Checks:
  1. every shipped file matches MANIFEST.sha256
  2. the grader is the frozen grader (sha256 4db98fdc39db...)
  3. re-grading every answer from the cached extractions and the shipped answer keys gives identical verdicts
  4. regenerating the metrics (scorer/metrics.py) gives the identical public-sample results (benchmark/results/results.json)
  5. rebuilding the past-game answer key from the shipped nflverse files gives the identical key
"""
import argparse, hashlib, json, shutil, subprocess, sys, tempfile
from pathlib import Path

from fantasybench import workspace as W

REPO = Path(__file__).resolve().parents[1]
FROZEN = "4db98fdc39db"

NAME = {"firecrawl_alex": "Alexandria (on Firecrawl)", "web": "Web search", "flaim": "Flaim"}


def sh(cmd, cwd):
    return subprocess.run([sys.executable, "-I", "-B", *cmd], cwd=cwd, capture_output=True, text=True)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def norm_key(path):
    """past-game key without verification notes, with checks, table rows and players in a fixed order (order inside a set can vary)"""
    out = {}
    for l in open(path):
        o = json.loads(l)
        o.pop("verified_against", None)  # past_games.jsonl is the key after verify_fact_key.py, which only adds these notes
        o["checks"] = sorted(({k: v for k, v in c.items() if k != "verified"} for c in o["checks"]), key=lambda c: json.dumps(c, sort_keys=True))
        for t in o["tables"]:
            t["rows"] = sorted(t["rows"], key=lambda r: json.dumps(r, sort_keys=True))
        o["players"] = sorted(o["players"], key=lambda p: p["name"])
        out[o["id"]] = o
    return out


def run_checks(out=None):
    """[(name, ok, detail)], and the regenerated results"""
    res = []
    lines = [l.split("  ", 1) for l in (REPO / "MANIFEST.sha256").read_text().splitlines() if l]
    bad = [p for h, p in lines if not (REPO / p).exists() or sha(REPO / p) != h]
    res.append(("1. shipped files match MANIFEST.sha256", not bad, f"changed: {bad[:5]}" if bad else ""))
    g = sha(REPO / f"{W.PKG}/scorer/grade.py")
    res.append(("2. grade.py is the frozen grader", g.startswith(FROZEN), g[:12]))
    with tempfile.TemporaryDirectory() as tmp:
        ws = W.build(REPO, Path(tmp) / "ws")
        err = ""
        for b in W.BATCHES:
            keys = W.live_keys(REPO, b)
            r = sh(["scripts/grade.py", "--run-id", b] + (["--live-pre", keys[0], "--live-post", keys[1]] if keys else []), ws)
            err += r.stderr[-400:] if r.returncode else ""
        # "named" lists the players a question names; names of equal length come out of a Python set, whose order varies
        # between processes. It is compared as a set; every verdict, score and per-player result must match exactly.
        fix = lambda g: {**g, "named": sorted(g["named"])} if isinstance(g.get("named"), list) else g
        old = [fix(g) for g in W.rows(REPO / W.RUNS / "grades.jsonl")]
        new = [fix(g) for g in W.regraded(ws)] if not err else []
        res.append(("3. re-grade every answer from cached extractions", old == new, f"{len(new):,} verdicts identical" if old == new else (err or "verdicts differ")))
        r = sh(["scripts/metrics.py"], ws)
        R = json.load(open(ws / "runs/results.json")) if r.returncode == 0 else None
        same = R is not None and R == json.load(open(REPO / W.RESULTS))["public_sample"]
        res.append(("4. metrics regenerate identically", same, "public sample in " + W.RESULTS if same else r.stderr[-300:]))
        if out and R is not None:
            shutil.copy2(ws / "runs/results.json", out)
        r = sh(["scripts/build_fact_key.py"], ws)
        built = norm_key(ws / "data/key/fact_key.jsonl") if r.returncode == 0 else {}
        # verify_fact_key.py (needs the Sleeper API) widened each disagreement it found to accept both values; replay those
        for d in json.load(open(REPO / W.PKG / "gt/answer_key/check_sleeper.json")):
            for c in built.get(d["id"], {}).get("checks", []):
                if (c["entity"], c["week"], c["field"], c["value"]) == (d["entity"], d["week"], d["field"], d["value"]):
                    c["accept"] = sorted({c["value"], round(float(d["sleeper"]), 2)})
            if d["id"] in built:
                built[d["id"]]["checks"].sort(key=lambda c: json.dumps(c, sort_keys=True))
        same = bool(built) and built == norm_key(REPO / W.PKG / "gt/answer_key/past_games.jsonl")
        res.append(("5. past-game key rebuilds from nflverse files", same, f"{len(built)} questions identical" if same else r.stderr[-300:]))
    return res, R


def main():
    ap = argparse.ArgumentParser(description="Re-grade every saved FantasyBench answer offline and print the results.")
    ap.add_argument("--out", help="save the regenerated results JSON here")
    a = ap.parse_args()
    checks, R = run_checks(a.out)
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    ok = all(c[1] for c in checks)
    if R:
        F = json.load(open(REPO / W.RESULTS))["full"]
        print(f"\nSuccess rate (questions answered right):\n")
        print(f"{'setup':<28}{'public sample (reproduced)':>28}{'full set (published)':>24}")
        print(f"{'':<28}{'n = ' + str(R['n_questions']):>28}{'n = ' + str(F['n_questions']):>24}")
        for s in ("firecrawl_alex", "web", "flaim"):
            print(f"{NAME[s]:<28}{R['setups'][s]['all']['rate']:>28.1%}{F['setups'][s]['all']['rate']:>24.1%}")
    print("\nALL CHECKS PASSED" if ok else "\nSOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
