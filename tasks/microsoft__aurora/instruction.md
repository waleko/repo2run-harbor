# Configure `microsoft/aurora` so pytest can collect tests

You are an expert skilled in environment configuration. Refer to repository
files such as `requirements.txt`, `setup.py`, `pyproject.toml`, `poetry.lock`,
`Pipfile`, `environment.yml`, `setup.cfg`, etc., and use dependency-prediction
tools like `pipreqs` to install third-party libraries so that the repository
can be successfully configured and pytest can collect its tests.

## Setup you start with

The repository **microsoft/aurora** has been cloned at commit `8b11659b91d06f87c2d22e541dbcd0092baf2157`
into `/repo`. The container is `python:3.10` with `poetry`, `pytest`,
`pytest-xdist`, and `pipdeptree` already installed. Internet access (PyPI,
apt, GitHub) is available.

## Success

Make this command exit with code **0** (tests collected) or **5** (no tests
in repo) when run from `/repo`:

```
pytest --collect-only -q --disable-warnings
```

Tests are not required to pass — only to be collectable. (This matches
Repo2Run's EBSR criterion in arXiv:2502.13681: tests should be runnable,
regardless of pass/fail.)

## Work process

1. **Read the directory structure** in `/repo`. Focus on configuration
   files: `setup.py`, `setup.cfg`, `pyproject.toml`, `Pipfile`,
   `Pipfile.lock`, `poetry.lock`, `environment.yml`, `tox.ini`, anything
   under `.github/`.

2. **Decide on the Python version.** The base image is 3.10. If the project
   requires a different version, switch via `apt-get` / `pyenv` / `uv python
   install`, or rebuild your tooling chain in the container.

3. **Try testing first (optional).** Running pytest immediately can reveal
   which dependencies are missing — the import errors guide installation.

4. **Install per the project manifest:**
   - `poetry.lock` present → `cd /repo && poetry install`
   - `setup.py` present → `pip install -e /repo`
   - `pyproject.toml` with `[project]` (PEP 621) → `pip install -e /repo`
   - `requirements*.txt` files → `pip install -r /repo/<file>`
   - No manifest → use `pipreqs /repo` to derive one, then install.

   Do not consider `requirements.txt` directly during the manifest-install
   step — only auto-install scripts in the repo (poetry, setup.py, PEP 621).

5. **Locate dependency-listing files** (`requirements.txt`,
   `requirements_dev.txt`, etc.) and install them with
   `pip install -r <path>`. Verify each file follows the
   `package_name [version_constraint]` format before installing.

6. **Run `pipreqs`** if no manifest exists or imports remain unresolved:
   `pipreqs /repo --savepath /tmp/req.txt`. Review and reconcile against
   any existing requirement files before installing.

7. **Resolve import-name vs install-name mismatches.** Examples: `import
   cv2` → `pip install opencv-python`; `import yaml` → `pip install pyyaml`.

8. **Set `PYTHONPATH`** with `export PYTHONPATH=/repo` if the project uses
   a non-standard layout — prefer this over editing source `__init__.py`
   files.

9. **Resolve version-constraint conflicts.** If two requirement files
   demand incompatible versions of the same package, pick the one
   compatible with the current Python version. `pip index versions
   <package> --python-version 3.10` lists what's available.

10. **Use `pipdeptree`** to inspect the installed dependency graph;
    `pipdeptree -p <package>` to see a single package's chain.

11. **Install missing system libraries** with
    `apt-get update -qq && apt-get install -y -qq <package>`.

12. **Iterate.** Re-run the success command from `/repo`; each error
    (ModuleNotFoundError, ImportError, missing system header) is a hint
    at what to install or configure next.

## Constraints

- **Do not modify or delete test files** to bypass collection or assertion
  errors. Fix the underlying configuration instead.
- Avoid `git clone` / `wget` of large external content into `/repo`; that
  changes the original repository.
- For modules not found, first check whether the missing name is a local
  module already in the project tree (e.g. `my_module.py` under `/repo`)
  before installing externally.
- Don't run things that open new shells (e.g. `hatch shell`) — your
  current shell session is the one that grades.
- Use `-q` / `-qq` quiet flags where supported (pip, apt) to minimize
  progress-bar noise.

[Project root Path]: /repo
