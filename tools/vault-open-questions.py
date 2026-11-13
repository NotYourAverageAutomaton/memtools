#!/usr/bin/env python3
"""vault-open-questions — open questions older than N days that still have no outcome.

A note is an open question if its frontmatter says `claim_type: open_question`. It is answered once it has an
`outcome:` (or resolved/answered/verdict) field or heading, or a closed/superseded/retracted status.
"""
import os, re, sys, datetime, argparse
from memtools_config import vault_root
VAULT = str(vault_root())
SKIP = {"raw", ".git", "node_modules", "__pycache__"}
OUT_FM = re.compile(r"^(outcome|resolved|answered|verdict)\s*:", re.M | re.I)
OUT_BODY = re.compile(r"^(#{1,4}\s*|\*\*)\s*(outcome|answered|resolved|verdict|adjudicated)\b", re.M | re.I)

def pages(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP]
        for f in files:
            if f.endswith(".md"):
                yield os.path.join(d, f)

def scan(root, days, today):
    due, done, total = [], 0, 0
    for p in pages(root):
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", t, re.S)
        if not m or not re.search(r"^claim_type:\s*open_question\b", m.group(1), re.M):
            continue
        total += 1
        fm, body = m.group(1), m.group(2)
        st = re.search(r"^status:\s*(\w+)", fm, re.M)
        if OUT_FM.search(fm) or OUT_BODY.search(body) or (st and st.group(1) in ("closed", "superseded", "retracted")):
            done += 1; continue
        d = re.search(r"^(?:created|last_reviewed):\s*(\d{4}-\d{2}-\d{2})", fm, re.M)
        age = (today - datetime.date.fromisoformat(d.group(1))).days if d else None
        if age is None or age >= days:
            due.append((age, os.path.relpath(p, root)))
    return total, done, sorted(due, key=lambda x: -(x[0] or 9999))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=14); ap.add_argument("--root", "--vault", dest="root", default=VAULT)
    a = ap.parse_args()
    if not os.path.isdir(a.root):
        print(f"UNKNOWN: no vault at {a.root}"); return 2
    total, done, due = scan(a.root, a.days, datetime.date.today())
    print(f"open_question pages: {total} · with an outcome: {done} · older than {a.days}d with NO outcome: {len(due)}")
    for age, rel in due:
        print(f"  {('?' if age is None else str(age)) + 'd':>6}  {rel}")
    return 1 if due else 0

if __name__ == "__main__":
    sys.exit(main())
