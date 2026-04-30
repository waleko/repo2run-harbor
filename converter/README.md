# harbor_export — Repo2Run → Harbor dataset converter

Package Repo2Run's task type ("configure a Python repo at a base commit so
`pytest --collect-only` succeeds") as a [Harbor](https://github.com/harbor-framework/harbor)
dataset. Each Harbor task asks an LLM agent to perform the configuration
work; Harbor's verifier dynamically checks the result. There is no oracle
solution / no golden patch — Repo2Run's published source tree does not
ship pre-computed agent traces.

## What gets generated

For each `(full_name, sha)` row in your CSV, this writes:

```
<out>/tasks/{owner}__{repo}/
├── task.toml          # Harbor schema 1.2; metadata in [metadata]
├── instruction.md     # Distilled from build_agent/agents/configuration.py
├── environment/
│   └── Dockerfile     # python:3.10 + poetry/pytest/pipdeptree + clone @ sha
└── tests/
    └── test.sh        # pytest --collect-only ; rc 0/5 -> reward.txt = 1
```

Plus `<out>/dataset.toml` indexing all tasks.

There is **no** `solution/` directory: the verifier judges whatever the
agent leaves behind in `/repo` after its session. Repo2Run's own success
metric (EBSR, "tests can be executed regardless of pass/fail") is the
default; pass `--strict` to require every test to pass.

## Build the dataset

```bash
python -m harbor_export.build_dataset \
    --input  harbor_export/samples/repos.csv \
    --out    ./harbor-dataset \
    --name   "repo2run/sample"
```

CSV format:

```csv
full_name,sha
Benexl/FastAnime,677f4690fab4651163d0330786672cf1ba1351bf
# lines starting with '#' and blank lines are ignored
```

To grow the dataset: append rows. The paper's 420-repo benchmark is not
included in this source tree (paper §4.1); when you have that list, drop
it at `harbor_export/samples/repos_paper.csv` and re-run with
`--input harbor_export/samples/repos_paper.csv`.

## Strict vs lenient

| Flag             | pytest invocation                              | Source                       |
|------------------|------------------------------------------------|------------------------------|
| (default)        | `pytest --collect-only -q --disable-warnings`  | Paper §3 EBSR, README §3.    |
| `--strict`       | `pytest --disable-warnings`                    | All tests must actually pass.|

In both modes, exit code **0** or **5** counts as success
(rc=5 = "no tests in repo," which Repo2Run also treats as pass).

## Local sanity check

Before paying for an agent run, build the env and run `test.sh` against
the *unconfigured* repo. You should get `reward = 0` (because nothing's
installed yet — that's the whole point of the task):

```bash
TASK=./harbor-dataset/tasks/Benexl__FastAnime
docker build -t repo2run-fastanime-env "$TASK/environment"
docker run --rm \
    -v "$PWD/$TASK/tests:/tests:ro" \
    repo2run-fastanime-env bash /tests/test.sh
# expect: 'pytest exit code: ...' followed by 'FAIL (reward=0)'
```

## Run it through Harbor

### Local Docker

```bash
pip install harbor-framework
harbor run -p ./harbor-dataset/tasks/Benexl__FastAnime \
    -a claude-code -m claude-sonnet-4-5 \
    --env docker
```

### e2b sandbox

```bash
# Required:
#   E2B_API_KEY      — for the e2b sandbox runtime
#   ANTHROPIC_API_KEY (or whichever your agent needs)
export $(grep -v '^#' .env | xargs)
export E2B_API_KEY=...

harbor run -p ./harbor-dataset/tasks/Benexl__FastAnime \
    -a claude-code -m claude-sonnet-4-5 \
    --env e2b
```

Harbor's `E2BEnvironment` consumes the same `task.toml` /
`environment/Dockerfile` / `tests/test.sh` — no extra task fields are
needed for e2b. `[environment].cpus` / `memory_mb` / `allow_internet` in
`task.toml` are honored by both the Docker and e2b environments.

## What's intentionally omitted

- **No oracle/golden patch.** Repo2Run produces these *by running its
  own agent*, which would take ~30–60 min per repo plus LLM cost; the
  user opted to skip and have Harbor judge agent runs dynamically.
- **No Repo2Run-specific tools** (`runtest`, `waitinglist`,
  `runpipreqs`, `change_python_version`, …) inside the env. The Harbor
  agent gets a vanilla python:3.10 + poetry/pytest container. Reproducing
  Repo2Run's tool affordances would require baking
  `build_agent/tools/*.py` into the image — out of scope.
- **No paper-scale dataset.** Only the FastAnime sample ships here. Bring
  your own list of `(full_name, sha)` pairs to scale up.

## Implementation notes

- `convert.py` — `convert_one(full_name, sha, out_task_dir, strict=False)`
  does string-substitution on templates in `templates/`. Placeholders are
  `__OWNER__`, `__REPO__`, `__SHA__`, `__SHORT_SHA__`,
  `__PYTEST_ARGS__`, `__PYTEST_INVOCATION__`, `__SUCCESS_RC0_DESC__`,
  `__TASK_NOTE__`, `__CRITERION_NAME__`, `__CRITERION_DESC__`.
- `build_dataset.py` — thin CLI: read CSV, call `convert_one` per row,
  write `dataset.toml`.
- `templates/env.Dockerfile.tmpl` mirrors `build_agent/utils/sandbox.py:111-140`.
- `templates/test.sh.tmpl` mirrors `build_agent/tools/runtest.py:48-90`
  and `build_agent/tools/poetryruntest.py`.
- `templates/instruction.md.tmpl` is a distilled version of
  `build_agent/agents/configuration.py:101-224`, trimmed of Repo2Run's
  custom-tool affordances.
