"""All published numbers in one file: runs/results.json (run inside a workspace from scripts/workspace.py).

python3 scripts/results.py [--arms web,firecrawl_alex,flaim]

Runs scripts/publish_metrics.py, then keeps the whole-eval numbers: pass rates with 95% intervals and paired tests on the
questions every setup could be graded on, scoring-label mix-ups over all live answers, and every requested item by outcome.
"""
import argparse, json, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
import standard_metrics as SM

NOTE = ("Items: live = requested players (named questions) or values (table questions); past games = checks or tables. "
        "Unresolved = could not be checked (site not saved, value behind a login, player in no saved table).")


BATCH_KEYS = {"full-fact": [], "full-live": ["data/key/live/week6_pre2_20261014T162038Z.json", "data/key/live/week6_post_20261014T171640Z.json"],
              "holdout-live": ["data/key/live/week6_holdout-pre_20261015T174117Z.json", "data/key/live/week6_holdout-post_20261015T180851Z.json"]}
VERDICT = ("pass", "acc", "items", "right", "pass_any_format", "per_player")


def full_detail_grades():
    """Grades with every judged value. The frozen grader saves at most 80 detail rows per answer (judged[:80]), so long
    tables lose rows that item counts need. Re-run a scratch copy of grade.py with only that cap removed, from the cached
    extractions (no model calls), and confirm every verdict and item count is unchanged."""
    with tempfile.TemporaryDirectory() as t:
        ws = Path(t) / "ws"
        shutil.copytree(ROOT, ws)
        g = ws / "scripts/grade.py"
        src = g.read_text()
        assert src.count("judged[:80]") == 4, "unexpected grader"
        g.write_text(src.replace("judged[:80]", "judged"))
        out = []
        for run, keys in BATCH_KEYS.items():
            r = subprocess.run([sys.executable, "-I", "-B", "scripts/grade.py", "--run-id", run] +
                               (["--live-pre", keys[0], "--live-post", keys[1]] if keys else []), cwd=ws, capture_output=True, text=True)
            assert r.returncode == 0, r.stderr[-400:]
            old = [json.loads(l) for l in open(ROOT / "runs" / run / "grades.jsonl")]
            new = [json.loads(l) for l in open(ws / "runs" / run / "grades.jsonl")]
            assert [[o.get(k) for k in VERDICT] for o in old] == [[n.get(k) for k in VERDICT] for n in new], "verdicts changed"
            out += new


def counts(g):
    r, w, m = SM.value_counts(g)
    u = 0
    if g["bucket"] == "live":
        pp = g.get("per_player")
        u = sum(1 for v in pp.values() if v == "unverifiable") if pp else sum(1 for d in g["detail"] if len(d) > 2 and d[2] is None)
    return r, w, m, u


def item_outcomes(arms, G):
    out = {}
    for arm in arms:
        t = dict(right=0, wrong=0, missing=0, unresolved=0)
        for g in (x for x in G if x["arm"] == arm):
            r, w, m, u = counts(g)
            t["right"] += r; t["wrong"] += w; t["missing"] += m; t["unresolved"] += u
        out[arm] = t
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="web,firecrawl_alex,flaim")
    arms = ap.parse_args().arms.split(",")
    r = subprocess.run([sys.executable, "-I", "-B", "scripts/publish_metrics.py", "--arms", ",".join(arms), "--tag", "_release"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    P = json.load(open(ROOT / "runs/publish_metrics_release.json"))
    labels = {a: {k: sum(P["label_errors"][run][a][k] for run in P["label_errors"]) for k in ("right", "label", "wrong")} for a in arms}
    G = full_detail_grades()
    by = {}
    for g in G:
        by.setdefault(g["id"], {})[g["arm"]] = g
    common = [i for i, d in by.items() if all(d.get(a, {}).get("pass") is not None for a in arms)]
    C = P["combined"]
    for a in arms:  # item precision / recall / F1 from every judged value (publish_metrics.py reads the capped detail)
        r = w = m = 0
        for i in common:
            r_, w_, m_, _ = counts(by[i][a]); r += r_; w += w_; m += m_
        prec, rec = r / max(1, r + w), r / max(1, r + w + m)
        C["setups"][a].update(precision=prec, recall=rec, f1=2 * prec * rec / max(1e-9, prec + rec))
    out = {"compared": arms, **C, "scoring_labels": labels, "item_outcomes": {"note": NOTE, "setups": item_outcomes(arms, G)}}
    json.dump(out, open(ROOT / "runs/results.json", "w"), indent=1)


if __name__ == "__main__":
    main()
