#!/usr/bin/env python3
"""vault-park — park a piece of work that is not finished and not being worked, with a named condition.

A park without a condition is neglect with a date, so --until is required. --unpark reverses it. Dry run unless --apply.
"""
import argparse
import datetime as dt
import os
import re
import sys

from memtools_config import layout, load_config, vault_root

VAULT = str(vault_root())
PROJ = os.path.join(VAULT, layout(load_config(vault_root()))["arcs_dir"])
LOG = os.path.join(VAULT, "log.md")


def split_fm(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise SystemExit("no frontmatter block")
    return m.group(1), text[m.end():]


def set_field(fm, key, value):
    """Replace a frontmatter field whether it is single-line or a `>-` / `|` block."""
    lines = fm.split("\n")
    out, i, done = [], 0, False
    while i < len(lines):
        l = lines[i]
        if re.match(rf"^{key}:", l):
            done = True
            out.append(f"{key}: {value}")
            i += 1
            if re.match(rf"^{key}:\s*[>|]", l):
                while i < len(lines) and (lines[i].startswith("  ") or lines[i] == ""):
                    i += 1
            continue
        out.append(l); i += 1
    if not done:
        out.insert(1, f"{key}: {value}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--until", help="the named condition that un-parks this arc")
    ap.add_argument("--unpark", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--vault", help="the notes folder (default: MEMTOOLS_VAULT or the current folder)")
    a = ap.parse_args()
    stem = a.page[:-3] if a.page.endswith(".md") else a.page
    if os.sep in stem or "/" in stem or stem.startswith(".") or not stem:
        raise SystemExit("give the page name only (no folders, no ..): it is looked up in the work folder")
    path = os.path.join(PROJ, stem + ".md")
    if not os.path.exists(path):
        raise SystemExit(f"no arc page {path}")
    today = dt.date.today().isoformat()
    text = open(path, encoding="utf-8").read()
    fm, body = split_fm(text)
    if a.unpark:
        fm = set_field(fm, "status", "active")
        fm = "\n".join(l for l in fm.split("\n") if not l.startswith("parked:"))
        fm = set_field(fm, "last_reviewed", today)
        logline = f"## [{today}] unpark | [[{stem}]] — back to active"
        note = f"\n\n## [{today}] UNPARKED\n"
    else:
        if not a.until:
            raise SystemExit("--until is required: a park without a named condition is just neglect with a date")
        cond = a.until.replace('"', "'")
        fm = set_field(fm, "status", "stale")
        fm = set_field(fm, "parked", f'"{today} — until: {cond}"')
        fm = set_field(fm, "next_action", f'"PARKED {today}. Un-park when: {cond}"')
        fm = set_field(fm, "last_reviewed", today)
        logline = f"## [{today}] park | [[{stem}]] — until: {cond}"
        note = f"\n\n## [{today}] PARKED — not finished, not being worked\n\nUn-park when: **{cond}**\n"
    new = f"---\n{fm}\n---\n{body.rstrip()}{note}"
    print(("APPLY " if a.apply else "DRY  ") + logline)
    if a.apply:
        open(path, "w", encoding="utf-8").write(new)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("\n" + logline + "\n")
        print("written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
