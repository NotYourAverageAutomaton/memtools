#!/usr/bin/env python3
"""log-rotator — archive log.md when it grows past N lines, keeping the last lines as the new head.

The archive is log_<timestamp>.md beside it. Every tool here that reads the log (recall, applied, index,
graph) reads log.md AND every log_*.md, so rotating loses no history. If you add a reader of your own,
make it read the archives too, or rotation will silently erase what it counts.
Dry run unless --apply.
"""
import argparse
import shutil
from datetime import datetime
from pathlib import Path

from memtools_config import vault_root


def rotate(log_path: Path, max_lines: int, keep: int, apply: bool) -> int:
    if not log_path.exists():
        print(f"{log_path}: does not exist.")
        return 2
    lines = log_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if len(lines) <= max_lines:
        print(f"{log_path.name}: {len(lines)} lines (limit {max_lines}); nothing to do.")
        return 0
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    archive = log_path.parent / f"{log_path.stem}_{ts}{log_path.suffix}"
    if not apply:
        print(f"would archive {log_path.name} ({len(lines)} lines) -> {archive.name} and keep the last {keep}; re-run with --apply")
        return 0
    shutil.copy2(log_path, archive)
    with log_path.open("w", encoding="utf-8") as f:
        f.write(f"# {log_path.name}\n\n> Continuing from archived log: [[{archive.stem}]]\n\n")
        f.writelines(lines[-keep:])
    print(f"archived {log_path.name} -> {archive.name} ({len(lines)} lines); kept the last {keep}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", help="the notes folder (default: MEMTOOLS_VAULT or the current folder)")
    ap.add_argument("--max-lines", type=int, default=1000)
    ap.add_argument("--keep", type=int, default=100)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return rotate(vault_root() / "log.md", a.max_lines, a.keep, a.apply)


if __name__ == "__main__":
    raise SystemExit(main())
