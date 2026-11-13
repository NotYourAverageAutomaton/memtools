#!/usr/bin/env python3
"""vault-close — mark a piece of work finished, in one call: status, a closure note, and a log line.

Close on evidence that the work ended, never on age. --check runs the page's own verify: claims first.
`closed` (finished, still true) is not `stale` (may have decayed). --list shows pages claiming active.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from memtools_config import layout, load_config, vault_root

VAULT = vault_root()
LAY = layout(load_config(VAULT))
LOG = VAULT / "log.md"
VERIFY = Path(__file__).resolve().parent / "vault-verify.py"
ARCS = VAULT / LAY["arcs_dir"]


def _all_pages():
    return [p for p in VAULT.rglob("*.md") if not any(d in p.parts for d in LAY["exclude_dirs"])]


def find_page(name: str) -> Path | None:
    key = name.replace(".md", "").strip("[]")
    exact = [p for p in _all_pages() if p.stem == key]
    if exact:
        return exact[0]
    near = [p for p in _all_pages() if key in p.stem]
    if len(near) == 1:
        return near[0]
    if near:
        print("ambiguous — did you mean:", file=sys.stderr)
        for p in near[:8]:
            print(f"  {p.stem}", file=sys.stderr)
    return None


def frontmatter_block(text: str) -> tuple[str, str]:
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    return (text[: end + 4], text[end + 4:]) if end != -1 else ("", text)


def run_assertions(page: Path) -> bool:
    """A page that makes checkable claims must have them checked before we call it finished."""
    fm, _ = frontmatter_block(page.read_text(encoding="utf-8", errors="ignore"))
    if "verify:" not in fm:
        print("  no verify: assertions on this page — nothing to check")
        print("  (that is itself worth noting: liveness rests on your judgement, not a check)")
        return True
    print("  running the page's own verify: assertions …")
    try:
        r = subprocess.run([sys.executable, str(VERIFY), "--vault", str(VAULT)], capture_output=True, text=True, timeout=180)
    except Exception as e:
        print(f"  could not run vault-verify: {e}", file=sys.stderr)
        return False
    rows = [l for l in r.stdout.splitlines() if page.name in l or f"{page.stem}.md" in l]
    for l in rows:
        print(f"    {l.strip()}")
    failed = [l for l in rows if l.strip().startswith(("FAIL", "UNKNOWN"))] or \
             [l for l in rows if "->" in l and " ok " not in l]
    if failed:
        print("  ⚠ assertions did not all pass — resolve before closing", file=sys.stderr)
        return False
    if rows:
        print("  ✅ assertions PASS — the systems this page describes are still real.")
        print("     Closing means THE WORK is finished, not that the system is gone.")
    return True


def close(page: Path, why: str, when: str, reopen: bool, apply: bool) -> int:
    text = page.read_text(encoding="utf-8")
    fm, body = frontmatter_block(text)
    if not fm:
        print(f"{page.name}: no frontmatter", file=sys.stderr)
        return 1

    cur = re.search(r"^status:\s*(\S+)", fm, re.M)
    cur = cur.group(1) if cur else "?"
    target = "active" if reopen else "closed"
    if cur == target:
        print(f"{page.stem}: already {target}")
        return 0
    if not reopen and cur in ("superseded",):
        print(f"{page.stem}: is `superseded` (replaced), which is a stronger claim than closed.\n"
              f"  Closing would say the content is still true. Confirm that first.", file=sys.stderr)
        return 1

    new_fm = re.sub(r"^status:\s*\S+", f"status: {target}", fm, count=1, flags=re.M)
    new_fm = re.sub(r"^closed:.*\n?", "", new_fm, flags=re.M)
    if not reopen:
        new_fm = new_fm.rstrip("\n-\n").rstrip("\n")
        if new_fm.endswith("---"):
            new_fm = new_fm[:-3].rstrip("\n")
        new_fm += f"\nclosed: {when} — {why}\n---\n"

    verb = "REOPENED" if reopen else "CLOSED"
    note = (f"\n> **{verb} {when}.** {why}\n"
            f"> `status: {cur}` → `{target}`. "
            + ("Back in progress; the record below resumes." if reopen else
               "The work is FINISHED — this record is complete and true, not decayed. "
               "(`closed` ≠ `stale`: nothing here needs re-checking.)")
            + "\n")

    m = re.search(r"^#\s+.*$", body, re.M)
    new_body = (body[: m.end()] + "\n" + note + body[m.end():]) if m else note + body

    if not apply:
        print(f"DRY RUN — {page.relative_to(VAULT)}")
        print(f"  status: {cur} -> {target}")
        print(f"  closed: {when} — {why}" if not reopen else "  (closed: stamp removed)")
        print(f"\n{note}")
        print("re-run with --apply")
        return 0

    page.write_text(new_fm.rstrip("\n") + "\n\n" + new_body.lstrip("\n"), encoding="utf-8")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"\n## [{when}] {'reopen' if reopen else 'close'} | [[{page.stem}]] — {why}\n")
    print(f"{verb}: {page.relative_to(VAULT)}  (logged to log.md)")
    print("next, if you keep an index: vault-index.py --apply")
    return 0


def list_candidates() -> int:
    """Deliberately NOT a ranked to-close list. Age is not activity; this shows what a human"""
    print("arc pages claiming status: active — CLOSE ON EVIDENCE, NEVER ON AGE\n")
    rows = []
    for p in sorted(ARCS.glob("*.md")):
        t = p.read_text(encoding="utf-8", errors="ignore")
        fm, _ = frontmatter_block(t)
        if not re.search(r"^status:\s*active", fm, re.M):
            continue
        rev = re.search(r"^last_reviewed:\s*(\S+)", fm, re.M)
        rev = rev.group(1) if rev else ""
        try:
            age = (date.today() - datetime.strptime(rev, "%Y-%m-%d").date()).days
        except ValueError:
            age = 9999
        rows.append((age, p.stem, rev, "verify:" in fm))
    for age, stem, rev, hv in sorted(rows):
        mark = "checkable (verify:)" if hv else "NO verify: — liveness is unverified"
        print(f"  {age:>4}d  {stem:<40} {mark}")
    print(f"\n  {len(rows)} active. A page with verify: assertions can be checked mechanically")
    print("  (`--check`); one without can only be judged. Neither is settled by the date.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="close an arc when the work is finished")
    ap.add_argument("page", nargs="?")
    ap.add_argument("--why", help="one line: why is this finished")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--check", action="store_true", help="run the page's verify: assertions first")
    ap.add_argument("--reopen", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--vault", help="the notes folder (default: MEMTOOLS_VAULT or the current folder)")
    a = ap.parse_args()

    if a.list:
        return list_candidates()
    if not a.page:
        ap.print_help()
        return 2
    if not a.why:
        print("--why is required: a close without a stated reason is indistinguishable from "
              "neglect six weeks later.", file=sys.stderr)
        return 2

    page = find_page(a.page)
    if not page:
        print(f"no such page: {a.page}", file=sys.stderr)
        return 1
    if a.check and not a.reopen and not run_assertions(page):
        return 1
    return close(page, a.why.strip(), a.date, a.reopen, a.apply)


if __name__ == "__main__":
    sys.exit(main())
