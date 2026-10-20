"""Expand the repository into the folder layout the frozen scripts read.

The answers were collected in three batches (past games, then live questions on Oct 14 and Oct 15), and the frozen scripts
(grade.py, standard_metrics.py, capture_live_key.py, run.mjs) read one folder per batch at fixed paths such as
scripts/grade.py, data/key/fact_key_verified.jsonl and runs/<batch>/answers/<q>/<setup>/result.json. The repository keeps
everything together in the fantasybench package: one question file, one answers file, one grades file. FILES and BATCHES
are the one map between the two layouts; the report command and the release builder both use them.
"""
import json, re, shutil
from pathlib import Path

PKG = "fantasybench"
# repository path -> path the frozen scripts read
FILES = {
    f"{PKG}/scorer/grade.py": "scripts/grade.py",
    f"{PKG}/scorer/metrics.py": "scripts/metrics.py",
    f"{PKG}/scorer/build_fact_key.py": "scripts/build_fact_key.py",
    f"{PKG}/scorer/verify_fact_key.py": "scripts/verify_fact_key.py",
    f"{PKG}/scorer/verify_scores_espn.py": "scripts/verify_scores_espn.py",
    f"{PKG}/harness/run.mjs": "scripts/run.mjs",
    f"{PKG}/harness/runner_lib.mjs": "scripts/runner_lib.mjs",
    f"{PKG}/harness/capture_live_key.py": "scripts/capture_live_key.py",
    f"{PKG}/harness/arms.json": "config/arms.json",
    f"{PKG}/harness/system-note.txt": "config/system-note.txt",
    f"{PKG}/harness/block_alexandria.py": "hooks/block_alexandria.py",
    f"{PKG}/gt/answer_key/past_games.jsonl": "data/key/fact_key_verified.jsonl",
    f"{PKG}/gt/answer_key/check_sleeper.json": "data/key/verify_sleeper.json",
    f"{PKG}/gt/answer_key/check_espn_scores.json": "data/key/verify_espn_scores.json",
    f"{PKG}/gt/answer_key/live/2026-10-14T1619Z.json": "data/key/live/week6_pre2_20261014T162038Z.json",
    f"{PKG}/gt/answer_key/live/2026-10-14T1716Z.json": "data/key/live/week6_post_20261014T171640Z.json",
    f"{PKG}/gt/answer_key/live/2026-10-15T1739Z.json": "data/key/live/week6_holdout-pre_20261015T174117Z.json",
    f"{PKG}/gt/answer_key/live/2026-10-15T1808Z.json": "data/key/live/week6_holdout-post_20261015T180851Z.json",
    f"{PKG}/gt/nflverse/SOURCES.json": "runs/ref_file_hashes.json",
}
NFLVERSE = (f"{PKG}/gt/nflverse", "data/ref")
QUESTIONS = f"{PKG}/gt/questions.jsonl"
RUNS = f"{PKG}/runs"
RESULTS = "benchmark/results/results.json"
LIVE = f"{PKG}/gt/answer_key/live"
# batch -> (its question file at run time, the two live captures that grade it)
BATCHES = {
    "full-fact": ("data/questions/questions_300.jsonl", None),
    "full-live": ("data/questions/questions_300.jsonl", ("2026-10-14T1619Z.json", "2026-10-14T1716Z.json")),
    "holdout-live": ("data/questions/holdout_live.jsonl", ("2026-10-15T1739Z.json", "2026-10-15T1808Z.json")),
}
LAST_RUN1 = 156  # live ids above this were the Oct 15 batch, numbered H001.. at run time


def public_id(i):
    return f"L{LAST_RUN1 + int(i[1:]):03d}" if i[0] == "H" else i


def run_id(i):
    return f"H{int(i[1:]) - LAST_RUN1:03d}" if i[0] == "L" and int(i[1:]) > LAST_RUN1 else i


def batch_of(i):
    """batch a public question id belongs to"""
    return {"F": "full-fact", "L": "full-live", "H": "holdout-live"}[run_id(i)[0]]


def rows(path):
    return [json.loads(l) for l in open(path)]


def put(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def build(repo, out):
    repo, out = Path(repo), Path(out)
    for rel, old in FILES.items():
        put(repo / rel, out / old)
    # the grader reads a list of live questions to leave unscored; the repository holds only scored questions
    (out / "data/questions").mkdir(parents=True, exist_ok=True)
    json.dump({"questions": {}}, open(out / "data/questions/live_not_projection.json", "w"))
    for f in sorted((repo / NFLVERSE[0]).iterdir()):
        if f.name != "SOURCES.json":
            put(f, out / NFLVERSE[1] / f.name)
    qfiles = {}
    for q in rows(repo / QUESTIONS):
        qfiles.setdefault(BATCHES[batch_of(q["id"])][0], []).append({**q, "id": run_id(q["id"])})
    for path, qs in qfiles.items():
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        with open(out / path, "w") as fh:
            fh.writelines(json.dumps(q, ensure_ascii=False) + "\n" for q in qs)
    info = json.load(open(repo / RUNS / "run_info.json"))
    extracts = {(e["id"], e["setup"]): e["extraction"] for e in rows(repo / RUNS / "extractions.jsonl")}
    expected = {b: [] for b in BATCHES}
    for a in rows(repo / RUNS / "answers.jsonl"):
        b, i = batch_of(a["id"]), run_id(a["id"])
        expected[b].append({"id": i, "arm": a["setup"]})
        d = out / "runs" / b / "answers" / i / a["setup"]
        d.mkdir(parents=True)
        for f in ("result", "meta", "trace"):
            if f in a:
                json.dump(a[f], open(d / f"{f}.json", "w"))
        if (a["id"], a["setup"]) in extracts:
            (out / "runs" / b / "extract").mkdir(exist_ok=True)
            json.dump(extracts[a["id"], a["setup"]], open(out / "runs" / b / "extract" / f"{i}__{a['setup']}.json", "w"))
    grades = {b: [] for b in BATCHES}
    for g in rows(repo / RUNS / "grades.jsonl"):
        grades[batch_of(g["id"])].append(json.dumps({**g, "id": run_id(g["id"])}, ensure_ascii=False) + "\n")
    for b, (qpath, _) in BATCHES.items():
        open(out / "runs" / b / "grades.jsonl", "w").writelines(grades[b])
        M = {"run_id": b, "model": info["model"], "effort": info["effort"], "arms": info["setups"],
             "questions": {"path": qpath}, "expected": expected[b]}
        json.dump(M, open(out / "runs" / b / "manifest.json", "w"), indent=1)
    return out


def live_keys(repo, batch):
    k = BATCHES[batch][1]
    return [str(Path(repo) / LIVE / f) for f in k] if k else []


def regraded(ws):
    """grades from a workspace, in repository order and with public ids"""
    return [{**g, "id": public_id(g["id"])} for b in BATCHES for g in rows(Path(ws) / "runs" / b / "grades.jsonl")]


def public_trace_paths(s):
    """run-time session paths inside trace inputs name the batch folder and run-time id; rewrite them to the public id"""
    return re.sub(r"(?:full-fact|full-live|holdout-live)-answers-([FLH]\d{3})", lambda m: "answers-" + public_id(m.group(1)), s)
