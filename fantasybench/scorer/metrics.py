"""Every published number: success rate, accuracy and median time per setup, and the head-to-head against Alexandria.

python3 scripts/metrics.py        (run inside a workspace from fantasybench/workspace.py; writes runs/results.json)

Success rate  share of questions the frozen grader marks right, with a 95% Wilson interval; also for past games and live.
Accuracy      checked values right / (right + wrong). A value is a player's projection or rank, a table value or a
              past-game stat; values that can't be checked against the saved sources are left out.
Median time   median seconds from question to final answer.
Head to head  Alexandria minus each other setup on the same questions: exact McNemar test, Holm-adjusted, and a 95%
              paired bootstrap interval (seed 7).
"""
import json, math, random, shutil, statistics, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUPS = ["firecrawl_alex", "web", "flaim"]
BATCHES = {"full-fact": [], "full-live": ["data/key/live/week6_pre2_20261014T162038Z.json", "data/key/live/week6_post_20261014T171640Z.json"],
           "holdout-live": ["data/key/live/week6_holdout-pre_20261015T174117Z.json", "data/key/live/week6_holdout-post_20261015T180851Z.json"]}
VERDICT = ("pass", "items", "right", "per_player")


def wilson(k, n, z=1.96):
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [(c - h) / d, (c + h) / d]


def mcnemar(b, c):
    n, k = b + c, min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def grades():
    """Grades with every judged value. grade.py keeps at most 80 detail rows per answer, which long tables exceed, so a
    scratch copy with only that cap removed re-grades from the cached extractions (no model calls); verdicts must match."""
    with tempfile.TemporaryDirectory() as t:
        ws = Path(t) / "ws"
        shutil.copytree(ROOT, ws)
        g = ws / "scripts/grade.py"
        src = g.read_text()
        assert src.count("judged[:80]") == 4, "unexpected grader"
        g.write_text(src.replace("judged[:80]", "judged"))
        out = []
        for run, keys in BATCHES.items():
            r = subprocess.run([sys.executable, "-I", "-B", "scripts/grade.py", "--run-id", run] +
                               (["--live-pre", keys[0], "--live-post", keys[1]] if keys else []), cwd=ws, capture_output=True, text=True)
            assert r.returncode == 0, r.stderr[-400:]
            old = [json.loads(l) for l in open(ROOT / "runs" / run / "grades.jsonl")]
            new = [json.loads(l) for l in open(ws / "runs" / run / "grades.jsonl")]
            assert [[o.get(k) for k in VERDICT] for o in old] == [[n.get(k) for k in VERDICT] for n in new], "verdicts changed"
            out += new
    return out


def right_wrong(g):
    """checked values right and wrong in one answer"""
    r = w = 0
    if g["bucket"] == "fact":
        for d in g["detail"]:
            if not isinstance(d, list) or (len(d) == 6 and d[5] == "optional"):
                continue
            if (d[5] if len(d) == 6 else d[-1]) is True:
                r += 1
            elif len(d) == 6 and d[4] is not None or len(d) != 6 and any(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in d[1:-1]):
                w += 1
    elif g.get("per_player"):
        r = sum(v == "right" for v in g["per_player"].values()); w = sum(v == "wrong" for v in g["per_player"].values())
    else:
        r = sum(1 for d in g["detail"] if len(d) > 2 and d[2] is True); w = sum(1 for d in g["detail"] if len(d) > 2 and d[2] is False)
    return r, w


def main():
    G = grades()
    by = {}
    for g in G:
        by.setdefault(g["id"], {})[g["arm"]] = g
    ids = sorted(i for i, d in by.items() if all(d[a]["pass"] is not None for a in SETUPS))
    part = {"all": ids, "past": [i for i in ids if by[i][SETUPS[0]]["bucket"] == "fact"], "live": [i for i in ids if by[i][SETUPS[0]]["bucket"] == "live"]}
    out = {"n_questions": len(ids), "n_past": len(part["past"]), "n_live": len(part["live"]), "setups": {}, "paired": {}}
    for a in SETUPS:
        s = {}
        for name, sel in part.items():
            k = sum(bool(by[i][a]["pass"]) for i in sel)
            s[name] = {"right": k, "n": len(sel), "rate": k / len(sel), "ci": wilson(k, len(sel))}
        rw = [right_wrong(by[i][a]) for i in ids]
        s["accuracy"] = sum(r for r, _ in rw) / max(1, sum(r + w for r, w in rw))
        s["median_time"] = statistics.median(by[i][a]["wall"] or 0 for i in ids)
        out["setups"][a] = s
    random.seed(7)
    rows = []
    for o in SETUPS[1:]:
        d = [int(bool(by[i][SETUPS[0]]["pass"])) - int(bool(by[i][o]["pass"])) for i in ids]
        boots = sorted(sum(random.choice(d) for _ in d) / len(d) for _ in range(10000))
        rows.append((o, sum(d) / len(d), [boots[250], boots[9750]], mcnemar(d.count(1), d.count(-1)), d.count(1), d.count(-1)))
    holm = 0.0
    for rank, (o, diff, ci, p, b, c) in enumerate(sorted(rows, key=lambda x: x[3])):
        holm = min(1.0, max(holm, p * (len(rows) - rank)))
        out["paired"][o] = {"diff": diff, "ci": ci, "p": p, "p_holm": holm, "alex_only": b, "other_only": c}
    json.dump(out, open(ROOT / "runs/results.json", "w"), indent=1)


if __name__ == "__main__":
    main()
