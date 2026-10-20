# FantasyBench

**Can AI agents get fantasy football data right?** 272 real questions, graded against saved sources.

The questions come from real searches made by Firecrawl users' agents. Three agent setups answer each one, and a program
scores the answers against fixed answer keys. One NFL week (Week 6, 2026), one model (claude-opus-5-5), one try per question.

This repository ships a 137-question public sample (about half of every question type). The rest is held back so no
setup can be tuned to it.

| Questions | What is asked | Answer key |
|---|---|---|
| 150 past games | stats, snaps, injuries and scores, Weeks 1–5 | nflverse, cross-checked with Sleeper and ESPN |
| 122 live | Week 6 projections and rankings | pages from 7 projection sites, saved before and after the answers |

## Results

On the full set of 272 questions:

| setup | success rate | 95% CI | past games | live | accuracy | median time |
|---|---|---|---|---|---|---|
| Alexandria (on Firecrawl) | **80.5%** (219/272) | 75.4% – 84.8% | 82.7% | 77.9% | 98.8% | 47 s |
| Web search | **71.7%** (195/272) | 66.1% – 76.7% | 80.7% | 60.7% | 85.0% | 65 s |
| Flaim | **71.3%** (194/272) | 65.7% – 76.4% | 79.3% | 61.5% | 92.6% | 62 s |

- **Success rate:** share of questions answered fully right. **Accuracy:** share of checked values (a player's projection
  or rank, a table value, a past-game stat) that are correct; claims that can't be checked are left out.
- Alexandria vs web search: +8.8 points (95% CI +2.9 to +14.7, p = 0.005). Vs Flaim: +9.2 points (95% CI +3.7 to +15.1, p = 0.005). Exact McNemar test, Holm-adjusted.

Full numbers: `benchmark/results/results.json`.

## Install

```bash
git clone https://github.com/firecrawl/benchmark-fantasy && cd benchmark-fantasy
uv sync --extra dev        # Python 3.11+; creates .venv and installs everything, including pytest
uv run pytest -q
```

With pip instead: `pip install -e ".[dev]"`. Both install the `fantasybench-report` command; pip users can drop `uv run`.
No API keys are needed.

## Reproduce the public-sample results

```bash
uv run fantasybench-report
```

Offline and free, about a minute: no API keys, no network, no model calls. It re-grades every shipped answer with the
frozen grader against the shipped answer keys, regenerates the results for the 137 public-sample questions, and rebuilds the
past-game key from the nflverse files. Expected output:

```
[PASS] 1. shipped files match MANIFEST.sha256
[PASS] 2. grade.py is the frozen grader - 4db98fdc39db
[PASS] 3. re-grade every answer from cached extractions - 411 verdicts identical
[PASS] 4. metrics regenerate identically - public sample in benchmark/results/results.json
[PASS] 5. past-game key rebuilds from nflverse files - 75 questions identical
```

followed by the success rates on the public sample (Alexandria (on Firecrawl) 81.8%, Web search 66.4%, Flaim 68.6%) next to the full
set. Compare your own run with the public-sample numbers. `--out results.json` saves the regenerated results.

## Method

- **Questions:** real fantasy searches by Firecrawl users' agents, Sep 30 – Oct 13, 2026, with internal teams and duplicates
  removed; live questions are rewritten to Week 6. Of 371 questions answered, the 272 kept are those every setup's
  answer could be checked on: 13 that don't ask for projections and 86 where at least one answer had nothing
  checkable (for example it cited only sites we didn't save) were dropped.
- **Setups:** a fresh Claude Code session per answer, with the same model, effort and prompt; only the tools differ
  (`fantasybench/harness/arms.json`).
- **Grading:** a model copies values out of each answer; `fantasybench/scorer/grade.py` compares them with the key. A past-game question
  passes with at least 90% of its items right; a live question naming players passes when no checked player is wrong or
  missing; a live table question needs at least 3 checked values, 90% of them right. Live values
  count if they match either saved copy of the site, within 0.3 points.

## Dataset

272 questions on the full set; a 137-question public sample ships for reproduction. The sample takes about half of every
question type and collection batch, chosen by a hash of a fixed seed (`fantasybench-public-2026-10-16`).

| question type | full (held back) | public sample (shipped) |
|---|---|---|
| Injury report (Weeks 1-5) | 42 | 21 |
| Stats and usage (Weeks 1-5) | 108 | 54 |
| Projections and rankings (Week 6, collected Oct 14) | 83 | 42 |
| Projections and rankings (Week 6, collected Oct 15) | 39 | 20 |
| **Total** | **272** | **137** |

`benchmark/results/` the published results · `fantasybench/gt/` the 137 public-sample questions (F = past games, L = live), the
past-game key with its Sleeper and ESPN checks, the live projection captures by time, and the nflverse files ·
`fantasybench/runs/` every public-sample answer, extracted value and grade, one row per question and setup ·
`fantasybench/scorer/` the frozen grader and the metrics that produced every number above · `fantasybench/harness/` the
setups, prompt, Alexandria block hook, runner and capture scripts used to collect the answers · `fantasybench/tests/`
offline tests

## Licences

Code: MIT. nflverse data: see https://github.com/nflverse. Projection values were captured from the named sites on
Oct 14–15, 2026 for evaluation; raw pages aren't included.
