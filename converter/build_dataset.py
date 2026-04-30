"""Build a Harbor dataset from a CSV of (full_name, sha) pairs.

Usage:
    python -m converter.build_dataset \
        --input converter/samples/repos_paper_full420.csv \
        --out   ./harbor-dataset-paper \
        --name  "repo2run/paper"

CSV format (UTF-8):
    full_name,sha[,success]   # 'success' column is optional metadata
    Benexl/FastAnime,677f4690fab4651163d0330786672cf1ba1351bf,Yes
    # blank lines and lines starting with '#' are skipped
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from . import convert as _convert


def _read_csv(input_path: Path) -> list[dict]:
    with input_path.open(newline="", encoding="utf-8") as fh:
        cleaned = (
            line for line in fh
            if line.strip() and not line.lstrip().startswith("#")
        )
        reader = csv.DictReader(cleaned)
        if reader.fieldnames is None:
            raise SystemExit(f"{input_path}: empty CSV")
        required = {"full_name", "sha"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise SystemExit(
                f"{input_path}: missing required columns: {sorted(missing)}"
            )
        rows = []
        for row in reader:
            full_name = (row.get("full_name") or "").strip()
            sha = (row.get("sha") or "").strip()
            if not full_name or not sha:
                continue
            rows.append({
                "full_name": full_name,
                "sha": sha,
                "success": (row.get("success") or "").strip(),
            })
    return rows


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _write_dataset_toml(out_dir: Path, name: str, task_names: list[str]) -> None:
    lines = [
        "[dataset]",
        f'name = "{_toml_escape(name)}"',
        'description = "Repo2Run task type packaged for Harbor: configure a '
        'Python repo at a base commit so pytest --collect-only succeeds."',
        "",
    ]
    for tn in task_names:
        lines.append("[[tasks]]")
        lines.append(f'name = "{_toml_escape(tn)}"')
        lines.append("")
    (out_dir / "dataset.toml").write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a Harbor dataset from a (full_name, sha) CSV.",
    )
    parser.add_argument("--input", type=Path, required=True,
                        help="CSV file with columns full_name, sha [, success].")
    parser.add_argument("--out", type=Path, required=True,
                        help="Output directory for the Harbor dataset.")
    parser.add_argument("--name", type=str, default="repo2run/sample",
                        help="Dataset name (Harbor expects org/name).")
    parser.add_argument("--strict", action="store_true",
                        help="Use strict criterion (full pytest pass) instead of "
                             "lenient (pytest --collect-only succeeds).")
    args = parser.parse_args(argv)

    rows = _read_csv(args.input)
    if not rows:
        print(f"No rows in {args.input}", file=sys.stderr)
        return 1

    out_root: Path = args.out
    tasks_dir = out_root / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    task_names: list[str] = []
    skipped = 0

    for row in rows:
        task_dir = tasks_dir / _convert._slug(row["full_name"])
        try:
            _convert.convert_one(row["full_name"], row["sha"], task_dir,
                                 strict=args.strict)
        except ValueError as e:
            print(f"  SKIP {row['full_name']}: {e}", file=sys.stderr)
            skipped += 1
            continue
        task_names.append(_convert.task_name(row["full_name"]))

    _write_dataset_toml(out_root, args.name, task_names)
    print(f"Wrote {len(task_names)} task(s); skipped {skipped}.")
    print(f"Manifest: {out_root / 'dataset.toml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
