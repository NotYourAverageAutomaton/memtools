#!/usr/bin/env python3
"""vault-applied — read the applied-log: which notes earned promotion, which never get used.

Log lines look like:  ## [2026-11-01] applied | [[page]] -> what it changed
(and `violated | [[page]]` when a rule was known and not followed: logged, never counted as support).
PROMOTE = applied on 2 or more DISTINCT days.  PRUNE = never applied and older than --stale-days.
BROKEN = a citation to a page that does not exist. A worklist only: nothing is edited.
"""
import argparse
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from memtools_config import layout, load_config, vault_root

VAULT = vault_root()
CAND = layout(load_config(VAULT))["candidate_dir"]
APPLIED = re.compile(r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s*applied\s*\|\s*\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]\s*(.*)$")   # v1.4.1: alias and heading forms count

VIOLATED = re.compile(r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s*(?:violated|failed)\s*\|\s*\[\[([^\]]+)\]\]\s*(.*)$")
NO_PROMOTE = {"open_question", "self_model_hypothesis"}


def page_index(root: Path) -> dict:
    """slug -> path, for every page outside raw/ and .git."""
    idx = {}
    for p in root.rglob("*.md"):
        if ".git" in p.parts or "raw" in p.parts:
            continue
        idx.setdefault(p.stem, p)
    return idx


def declined(p: Path) -> str:
    """A page can record that promotion was CONSIDERED and refused, with the reason."""
    m = re.search(r"^promotion[_:\s]+declined:?\b(.*)$",
                  p.read_text(errors="ignore")[:1500], re.M)
    return (m.group(1).strip(" #") or "no reason recorded") if m else ""


def claim_type_of(p: Path) -> str:
    txt = p.read_text(encoding="utf-8", errors="ignore")[:1200]
    m = re.search(r"^claim_type:\s*(\S+)", txt, re.M)
    return m.group(1).strip() if m else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=str(VAULT), help="the notes folder (default: MEMTOOLS_VAULT or the current folder)")
    ap.add_argument("--stale-days", type=int, default=60)
    ap.add_argument("--today", default="", help="override for testing (YYYY-MM-DD)")
    a = ap.parse_args()
    root = Path(a.vault)
    today = datetime.strptime(a.today, "%Y-%m-%d").date() if a.today else date.today()

    pages = page_index(root)
    days = defaultdict(set)
    last = {}
    total = 0
    unlinked = []
    log_files = sorted(root.glob("log.md")) + sorted(root.glob("log_*.md"))
    seen_lines, log_lines = set(), []
    for f in log_files:
        for l in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            key = l.strip()
            if key and key in seen_lines:
                continue
            seen_lines.add(key)
            log_lines.append(l)
    violations = defaultdict(set)
    for line in log_lines:
        st = line.strip()
        v = VIOLATED.match(st)
        if v:
            violations[v.group(2).strip()].add(v.group(1))
            continue
        m = APPLIED.match(st)
        if not m:
            if st.startswith("##") and "applied |" in st:
                unlinked.append(st[:110])
            continue
        total += 1
        d, slug = m.group(1), m.group(2).strip()
        days[slug].add(d)
        last[slug] = max(last.get(slug, ""), d)

    inbound = defaultdict(int)
    for p in pages.values():
        for tgt in re.findall(r"\[\[([^\]]+)\]\]", p.read_text(encoding="utf-8", errors="ignore")):
            t = tgt.split("|")[0].strip()
            if t != p.stem:
                inbound[t] += 1

    promote, broken, held = [], [], []
    for slug, ds in days.items():
        p = pages.get(slug)
        if p is None:
            broken.append((slug, len(ds)))
            continue
        if CAND and CAND not in p.parts:
            continue
        ct = claim_type_of(p)
        if ct in NO_PROMOTE:
            continue
        if len(ds) >= 2:
            why = declined(p)
            if why:
                held.append((len(ds), slug, why))
                continue
            promote.append((len(ds), len(ds), slug, ct or "(untyped)", last[slug]))

    prune = []
    for slug, p in sorted(pages.items()):
        if (CAND and CAND not in p.parts) or slug in days:
            continue
        try:
            m = re.search(r"^last_reviewed:\s*(\d{4}-\d{2}-\d{2})", p.read_text(errors="ignore")[:900], re.M)
            age = (today - datetime.strptime(m.group(1), "%Y-%m-%d").date()).days if m else None
        except Exception:
            age = None
        if age is not None and age >= a.stale_days:
            prune.append((age, slug, inbound.get(slug, 0)))

    print(f"=== vault-applied: {total} logged applications -> "
          f"{sum(len(v) for v in days.values())} distinct page-days "
          f"across {len(days)} cited pages ===\n")

    if violations:
        print(f"  VIOLATED — logged as broken, NOT counted as corroboration ({len(violations)}):")
        for slug, ds in sorted(violations.items(), key=lambda kv: -len(kv[1])):
            ap = len(days.get(slug, ()))
            note = "known and not followed" if ap else "no applications at all"
            print(f"    {len(ds)}x broken / {ap} applied-days  {slug}  <- {note}")
        print("    A high broken:applied ratio does not mean the rule is wrong. It means the rule")
        print("    is not REACHABLE at the moment of decision.")
        print()

    where = f"in {CAND}/" if CAND else "all pages"
    print(f"  PROMOTION CANDIDATES — {where}, applied on >=2 distinct days ({len(promote)}):")
    if not promote:
        print("    none.")
    for n, _, slug, ct, lastd in sorted(promote, reverse=True):
        print(f"    {n:3d} days  {slug}")
        print(f"              claim_type: {ct} · last applied {lastd}")
    print()

    if held:
        print(f"  HELD BACK — over the bar, promotion declined on the record ({len(held)}):")
        for n, slug, why in sorted(held, reverse=True):
            print(f"    {n:3d} days  {slug}\n              {why[:110]}")
        print()

    print(f"  PRUNING CANDIDATES — {where}, never applied, >={a.stale_days}d old ({len(prune)}):")
    if not prune:
        print("    none.")
    for age, slug, ib in sorted(prune, reverse=True)[:25]:
        flag = "  <- AND nothing links to it" if ib == 0 else f"  ({ib} inbound)"
        print(f"    {age:4d}d  {slug}{flag}")
    if len(prune) > 25:
        print(f"    ... and {len(prune) - 25} more (shown: oldest 25)")
    print()

    if broken:
        print(f"  BROKEN CITATIONS — applied to a page that does not exist ({len(broken)}):")
        for slug, n in sorted(broken, key=lambda x: -x[1]):
            print(f"    {n:3d}x  [[{slug}]]")
        print()

    if unlinked:
        print(f"  UNLINKED APPLICATIONS — logged without [[wikilink]], so uncountable ({len(unlinked)}):")
        for u in unlinked:
            print(f"    {u}")
        print()

    top = sorted(((len(v), k) for k, v in days.items()), reverse=True)[:8]
    print("  MOST LOAD-BEARING (distinct days applied) — what the vault actually runs on:")
    for n, slug in top:
        tier = pages[slug].parent.name if slug in pages else "MISSING"
        print(f"    {n:3d}  {slug}  [{tier}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
