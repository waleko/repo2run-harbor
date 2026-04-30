"""Convert one (full_name, sha) pair into a dynamic Harbor task.

No oracle solution. The agent gets a clean baseline (python:3.10 + tools +
repo cloned at sha); the verifier runs `pytest --collect-only` and grades.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "templates"

_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


@dataclass(frozen=True)
class Criterion:
    name: str
    pytest_args: str


LENIENT = Criterion("ebsr-lenient", "--collect-only -q --disable-warnings")
STRICT = Criterion("ebsr-strict", "--disable-warnings")


def _slug(full_name: str) -> str:
    owner, repo = full_name.split("/", 1)
    return f"{owner}__{repo}"


def task_name(full_name: str) -> str:
    return f"repo2run/{_slug(full_name)}"


def convert_one(full_name: str, sha: str, out_task_dir: Path,
                strict: bool = False) -> None:
    if not _FULL_NAME_RE.match(full_name):
        raise ValueError(f"full_name must be 'owner/repo', got: {full_name!r}")
    if not _SHA_RE.match(sha):
        raise ValueError(f"sha must be 7-40 hex chars, got: {sha!r}")

    owner, repo = full_name.split("/", 1)
    crit = STRICT if strict else LENIENT
    subs = {
        "__OWNER__": owner,
        "__REPO__": repo,
        "__SLUG__": _slug(full_name),
        "__SHA__": sha,
        "__SHORT_SHA__": sha[:7],
        "__PYTEST_ARGS__": crit.pytest_args,
        "__CRITERION_NAME__": crit.name,
    }

    out_task_dir = Path(out_task_dir)
    if out_task_dir.exists():
        shutil.rmtree(out_task_dir)
    (out_task_dir / "environment").mkdir(parents=True)
    (out_task_dir / "tests").mkdir()

    _write(out_task_dir / "task.toml", _render("task.toml.tmpl", subs))
    _write(out_task_dir / "instruction.md", _render("instruction.md.tmpl", subs))
    _write(out_task_dir / "environment" / "Dockerfile",
           _render("env.Dockerfile.tmpl", subs))
    test_sh = out_task_dir / "tests" / "test.sh"
    _write(test_sh, _render("test.sh.tmpl", subs))
    test_sh.chmod(0o755)


def _render(template_name: str, subs: dict[str, str]) -> str:
    text = (TEMPLATES_DIR / template_name).read_text()
    for placeholder, value in subs.items():
        text = text.replace(placeholder, value)
    return text


def _write(path: Path, content: str) -> None:
    path.write_text(content)
