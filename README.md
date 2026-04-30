# repo2run-harbor

[Repo2Run](https://arxiv.org/abs/2502.13681) (NeurIPS 2025 spotlight) is a
benchmark for **Python repository environment configuration** — given a
GitHub repository at a base commit, an LLM agent must install all
dependencies and patch any source so that `pytest --collect-only` succeeds
inside the cloned repo. The paper introduces 420 such repos and reports
EBSR (test-runnable rate) for several baselines including a custom GPT-4o
agent at 86.0%.

This repo packages those 420 tasks as a [Harbor](https://github.com/harbor-framework/harbor)
dataset so any Harbor-supported agent (`terminus-2`, `claude-code`,
`codex`, `aider`, `mini-swe-agent`, `openhands`, …) on any Harbor
environment (`docker`, `e2b`, `daytona`, `modal`, `runloop`, `gke`) can be
benchmarked against it.

## What's here

```
.
├── README.md                              ← (this file)
├── dataset.toml                           ← Harbor dataset manifest
├── tasks/                                 ← 414 Harbor task directories
│   └── {owner}__{repo}/
│       ├── task.toml                      ← Harbor schema 1.2 config
│       ├── instruction.md                 ← task statement (~100 lines)
│       ├── environment/Dockerfile         ← python:3.10 + tools + clone @ sha
│       └── tests/test.sh                  ← runs pytest, writes 1/0 to /logs/verifier/reward.txt
└── converter/                             ← regenerate the dataset from source
    ├── convert.py
    ├── build_dataset.py
    ├── templates/
    └── samples/
        ├── repos.csv                      ← single-row sample (FastAnime)
        ├── repos_paper_full420.csv        ← all 420 (414 with full SHA, 6 unrecoverable)
        └── repos_paper_unresolved.txt     ← the 6 deleted-repo / force-pushed cases
```

**Why 414 and not 420.** 6 of the paper's repos can't be reproduced today:
3 GitHub repos have been deleted, 3 commits were force-pushed away. They
are listed in `converter/samples/repos_paper_unresolved.txt`.

**No oracle / no golden patch.** Each task is dynamic: agent gets the
unconfigured baseline, verifier judges the result. The original Repo2Run
paper produces oracle solutions by running its custom agent (which we
intentionally don't replicate — Harbor agents bring their own scaffolding).

## Quick start: run on e2b

Prereqs:

```bash
# Harbor with e2b extra
uv tool install --reinstall 'harbor[e2b]'

# .env in the repo root
cat <<'ENV' > .env
E2B_API_KEY=e2b_...
OPENAI_API_KEY=sk-...           # if your agent uses OpenAI models
ANTHROPIC_API_KEY=sk-ant-...    # if your agent uses Claude models
ENV
```

Then:

```bash
git clone https://github.com/waleko/repo2run-harbor.git
cd repo2run-harbor

# One task
harbor run --env-file ./.env \
    -p ./tasks/Benexl__FastAnime \
    -a terminus-2 -m openai/gpt-5.5 --ak temperature=1 \
    --env e2b

# 10 tasks in parallel
harbor run --env-file ./.env \
    -p ./tasks \
    -a terminus-2 -m openai/gpt-5.5 --ak temperature=1 \
    --env e2b \
    --n-attempts 1 --n-concurrent 4 -l 10

# Subset by name
harbor run --env-file ./.env \
    -p ./tasks \
    -i 'Benexl__FastAnime' -i 'apple__ml-mdm' \
    -a terminus-2 -m openai/gpt-5.5 --ak temperature=1 \
    --env e2b

# Whole dataset
harbor run --env-file ./.env \
    -p ./tasks \
    -a terminus-2 -m openai/gpt-5.5 --ak temperature=1 \
    --env e2b \
    --n-attempts 1 --n-concurrent 8
```

### Other agents and environments

Any Harbor agent works. Examples:

```bash
# Claude with the claude-code harness
harbor run --env-file ./.env -p ./tasks \
    -a claude-code -m claude-sonnet-4-5 \
    --env e2b -l 10

# OpenAI Codex CLI
harbor run --env-file ./.env -p ./tasks \
    -a codex -m openai/gpt-5.5 \
    --env e2b -l 10

# mini-swe-agent (lightweight, model-agnostic)
harbor run --env-file ./.env -p ./tasks \
    -a mini-swe-agent -m openai/gpt-4o-mini \
    --env e2b -l 10

# Local Docker instead of e2b
harbor run --env-file ./.env -p ./tasks \
    -a terminus-2 -m openai/gpt-5.5 --ak temperature=1 \
    --env docker -l 10
```

### Inspecting results

```bash
cat jobs/<timestamp>/result.json | jq '.stats'
harbor view jobs       # interactive web UI for trajectories + recordings
ls jobs/<timestamp>/<task_slug>/{agent,verifier}/
```

## How the verifier works

`tests/test.sh` (mirrors `build_agent/tools/runtest.py` from upstream
Repo2Run):

1. Sources `/tmp/repo2run-env-snapshot` — see "Automatic env-var
   persistence" below.
2. Re-prepends `/root/.local/bin` to `PATH` (so `poetry run pytest` is
   findable even if the snapshot trampled `PATH`).
3. Detects poetry vs plain pip via `poetry.lock` / `[tool.poetry]`.
4. Runs `pytest --collect-only -q --disable-warnings` from `/repo`.
5. Exit code **0** (tests collected) or **5** (no tests in repo) → `1`
   in `/logs/verifier/reward.txt`. Anything else → `0`.

Pass `--strict` to the converter and rebuild for a stricter bar that
requires actual test passes (`pytest --disable-warnings` returning 0/5).

## Automatic env-var persistence

`environment/Dockerfile` installs a `PROMPT_COMMAND` in
`/etc/bash.bashrc` that snapshots `declare -px` (every exported var)
into `/tmp/repo2run-env-snapshot` after every interactive command. The
verifier sources that snapshot, so any `export X=Y` the agent runs in
its session is replayed into the test phase — automatically, no agent
awareness required. This matches Repo2Run's pexpect-shell semantics
where agent `export`s become `ENV` in the final reproducible image.

Caveat: only fires for *interactive* bash sessions (terminus-2 ✓,
claude-code ✓, mini-swe-agent's per-command subshells ✗ — but those
agents don't keep exports across commands anyway).

## Filter syntax (Harbor 0.6.x)

Harbor's `--include-task-name` (`-i`) / `--exclude-task-name` (`-x`)
match the **directory name** under `tasks/`, not the `[task].name` from
each `task.toml`. Use the slug:

```bash
-i 'Benexl__FastAnime'          # right
-i 'repo2run/Benexl__FastAnime' # wrong — silently matches nothing then errors
```

Glob patterns work: `-i 'apple__*'`, `-i '*__pytest-*'`.

## E2B sandbox-lifetime caveat

Harbor 0.6.x hardcodes the e2b sandbox `timeout=86_400` (24h) at
`harbor/environments/e2b.py:_create_sandbox`. e2b's free / hobby tier
caps sandboxes at **1h**, so creating one with the longer timeout
returns:

```
e2b.exceptions.SandboxException: 400: Timeout cannot be greater than 1 hours
```

Workaround: edit your local Harbor install — change `timeout=86_400` →
`timeout=3_600` in that file. (Or upgrade your e2b tier.)

## Smoke-test results

`terminus-2` + `openai/gpt-5.5` on e2b, 10 tasks sampled from the
beginning of the dataset:

| Task | Reward |
|---|---|
| FlagOpen/FlagGems | 1 |
| Mindinventory/MindSQL | 1 |
| modern-python/that-depends | 1 |
| NLPJCL/RAG-Retrieval | 1 |
| NVlabs/nvTorchCam | 1 |
| ServiceNow/WorkArena | 1 |
| vllm-project/llm-compressor | 1 |
| volfpeter/htmy | 1 |
| zipnn/zipnn | 1 |
| metavoiceio/metavoice-src | (un-graded — agent still working when killed) |

**9/9 graded trials passed** (`gpt-5.5` is a strong model on this task).
Total OpenAI cost ~$2.6 across 12 sandbox-trials.

## Provenance

- **Paper:** Hu, Peng, Wang, Xu, Gao. *Repo2Run: Automated Building
  Executable Environment for Code Repository at Scale*. arXiv:2502.13681
  (NeurIPS 2025).
- **Repo list:** parsed from the paper's appendix
  (`tables/benchmark.tex`); short SHAs expanded to full SHAs via the
  GitHub API. CSV in `converter/samples/repos_paper_full420.csv`.
- **Upstream Repo2Run framework:**
  [bytedance/Repo2Run](https://github.com/bytedance/Repo2Run) (build
  agent + sandbox + tools).
- **Author release of solutions:**
  [kinesiatricssxilm14/Repo2Run](https://github.com/kinesiatricssxilm14/Repo2Run)
  — `success_dockerfile/<owner>/<repo>/` with per-instance Dockerfile,
  patches, and trajectory metadata for the 325 successfully-configured
  cases. (Not used here since the dataset is dynamic, but useful for
  comparison.)

## Regenerating the dataset

```bash
# From the .tex source (gh login required for SHA expansion):
python3 - <<'PY'
import re, subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

TEX = Path('/path/to/Repo2Run paper/tables/benchmark.tex').read_text()
TEX = re.sub(r'\\(?:quad|qquad|allowbreak|hspace\{[^}]*\}|kern[^a-zA-Z]?[^ ]*|/)', '', TEX)
TEX = TEX.replace('\\_', '_')
ROW = re.compile(r'([\w\-./_]+/\s*[\w\-./_]+)\s*&\s*([0-9a-f]{6,})\s*&\s*(Yes|No)\b', re.I)

def expand(fn, sha, ok):
    fn = re.sub(r'\s+', '', fn)
    r = subprocess.run(['gh','api',f'repos/{fn}/commits/{sha}','--jq','.sha'],
                       capture_output=True, text=True, timeout=30)
    return (fn, r.stdout.strip() if r.returncode == 0 else None, ok)

with ThreadPoolExecutor(max_workers=16) as ex:
    futs = [ex.submit(expand, *m) for m in ROW.findall(TEX)]
    rows = [f.result() for f in as_completed(futs)]

rows = sorted([(fn,sha,ok) for fn,sha,ok in rows if sha], key=lambda r: r[0].lower())
out = Path('converter/samples/repos_paper_full420.csv')
out.write_text('full_name,sha,success\n' + '\n'.join(','.join(r) for r in rows) + '\n')
PY

# Then build:
python -m converter.build_dataset \
    --input  converter/samples/repos_paper_full420.csv \
    --out    .  \
    --name   "repo2run/paper"
```

## License

Tasks reference public GitHub repositories under their respective
licenses. Converter code: MIT.
