#!/usr/bin/env python3
"""vault-lint — structural health check for a folder of markdown notes.

Always: broken [[links]], links split across a line break, bad `status:` values, active pages not reviewed
in --stale-days, and typed `source_refs` (page:, file:, task:, commit:, ext:, text:, session:) when present.
Opt-in under [lint] in memtools.toml: required frontmatter fields, required fields on active work pages,
`abandon_if`, and `maturity` rules. Exit 0 = healthy, 1 = issues. --report writes them to a file.
"""

import argparse
import re
import sys
from datetime import datetime, timedelta, date
from pathlib import Path

from memtools_config import layout, load_config, vault_root

VAULT = vault_root()
CONFIG = load_config(VAULT)
LAY = layout(CONFIG)
LCFG = CONFIG.get("lint", {}) or {}
STALE_DAYS = int(LCFG.get("stale_days", 90))
REQUIRED_FIELDS = list(LCFG.get("required_fields", []))
VALID_STATUS = set(LCFG.get("valid_status", ["draft", "active", "closed", "stale", "superseded"]))
ARC_FIELDS = list(LCFG.get("arc_fields", []))
REQUIRE_ABANDON_IF = bool(LCFG.get("require_abandon_if", False))
CHECK_MATURITY = bool(LCFG.get("maturity", False))
VALID_MATURITY = {"production", "validated", "experimental", "designed",
                  "hypothesis", "disputed", "retracted"}
MATURITY_NEEDS_EVIDENCE = {"production", "validated"}
REMOTE_HOSTS = set((CONFIG.get("hosts", {}) or {}).keys())

NOT_PAGE_DIRS = set(LAY["exclude_dirs"]) | {"raw", "scripts"}


def _page_dirs(root: Path) -> set:
    return {p.name for p in root.iterdir()
            if p.is_dir() and not p.name.startswith(".") and p.name not in NOT_PAGE_DIRS}


FRONTMATTER_DIRS = _page_dirs(VAULT)

SCAN_DIRS = FRONTMATTER_DIRS


SELF_OUTPUT = ("health-report-",)
SKIP_DIRS = set()


def find_pages(vault):
    pages = {}
    for d in SCAN_DIRS:
        root = vault / d
        if root.exists():
            for p in root.rglob("*.md"):
                if p.name.startswith(SELF_OUTPUT) or SKIP_DIRS & set(p.parts):
                    continue
                pages[p.stem] = p
    for p in vault.glob("*.md"):
        pages[p.stem] = p
    return pages


def parse_frontmatter(content):
    m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return None
    fm = {}
    for line in m.group(1).splitlines():
        m2 = re.match(r"^([\w_]+):\s*(.+)$", line)
        if m2:
            fm[m2.group(1)] = m2.group(2).strip()
    return fm


def strip_code(content):
    content = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
    content = re.sub(r"`[^`\n]*`", "", content)
    return content


PLANNED_REGISTER = str(LCFG.get("planned_register", "unwritten-pages.md"))

SOURCE_REF_CLASSES = {
    "page":    "a vault page stem — CHECKED: the file must exist",
    "task":    "task-store id, task:<n> — format checked",
    "commit":  "commit sha, optionally <host>-<sha> — format checked",
    "session":  "a dated session/run identifier — format checked",
    "file":    "a path to an artifact — CHECKED: the file must exist",
    "ext":     "external identifier (arxiv, github, doi, url) — format checked",
    "text":    "free prose provenance — deliberately unchecked, kept honest by being named",
}
_HEX = re.compile(r"^[0-9a-f]{7,40}$")
_PAGE_SHAPED = re.compile(r"^[a-z0-9]+(-[a-z0-9]+){2,}$")


def extract_source_refs(content):
    """Every source_refs value on a page, in EITHER YAML style."""
    m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return None
    fm = m.group(1)
    m2 = re.search(r"^source_refs:[ \t]*(.*)$", fm, re.M)
    if not m2:
        return None
    inline = m2.group(1).strip()
    if inline:
        return [x.strip().strip("'\"") for x in inline.strip("[]").split(",") if x.strip()]
    refs = []
    for line in fm[m2.end():].splitlines():
        if not line.strip():
            continue
        b = re.match(r"^[ \t]+-[ \t]*(.+)$", line)
        if not b:
            break
        refs.append(b.group(1).strip().strip("'\""))
    return refs


_basenames = None


def _file_resolves(val, vault):
    """A file: ref resolves if it is vault-relative, an existing absolute path, or"""
    global _basenames
    if (vault / val).exists():
        return True
    p = Path(val).expanduser()
    if p.is_absolute() and p.exists():
        return True
    if "/" in val:
        return False
    if _basenames is None:
        _basenames = {}
        for f in vault.rglob("*"):
            if f.is_file() and ".git" not in f.parts:
                _basenames.setdefault(f.name, []).append(f)
    return len(_basenames.get(val, [])) == 1


def check_source_refs(refs, pages, vault):
    """Return (issues, advisories) for one page's source_refs value."""
    issues, advisories = [], []
    for ref in refs:
        if ":" not in ref:
            issues.append(f"untyped {ref!r} (prefix it: {'|'.join(sorted(SOURCE_REF_CLASSES))})")
            continue
        cls, _, val = ref.partition(":")
        cls, val = cls.strip(), val.strip()
        if cls not in SOURCE_REF_CLASSES:
            issues.append(f"unknown class {cls!r} in {ref!r}")
            continue
        if not val:
            issues.append(f"empty value in {ref!r}")
        elif cls == "page" and val not in pages:
            issues.append(f"page:{val} resolves to no file")
        elif cls == "file":
            host, _, rest = val.partition(":")
            if host in REMOTE_HOSTS and rest:
                pass
            elif not _file_resolves(val, vault):
                issues.append(f"file:{val} does not exist")
        elif cls == "task" and not re.match(r"^\d+$", val.lstrip("#")):
            issues.append(f"task:{val} is not a task number")
        elif cls == "commit" and not _HEX.match(val.split("-")[-1]):
            issues.append(f"commit:{val} has no sha-shaped tail")
        elif cls == "text" and _PAGE_SHAPED.match(val):
            advisories.append(f"text:{val} is page-shaped — did you mean page:?")
    return issues, advisories


def load_planned(vault):
    """Targets allow-listed in the unwritten-pages register (backticked or [[linked]])."""
    path = vault / PLANNED_REGISTER
    if not path.exists():
        return set()
    body = path.read_text(encoding="utf-8")
    return set(re.findall(r"[`\[]{1,2}([a-z0-9][a-z0-9-]{3,})[`\]]{1,2}", body))


def check_wrapped_links(content):
    """A wikilink split across a newline by hard-wrapping."""
    return [" ".join(m.split()) for m in
            re.findall(r"\[\[([^\]\n]*\n[^\]]*?)\]\]", strip_code(content))]


def check_broken_links(content, pages, planned=frozenset()):
    """-> (broken, planned_refs). `broken` is unregistered and must be fixed."""
    broken, plan = [], []
    for raw in re.findall(r"\[\[(.*?)\]\]", strip_code(content)):
        stem = raw.split("|")[0].split("#")[0].strip()
        if not stem or stem in pages:
            continue
        (plan if stem in planned else broken).append(stem)
    return broken, plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", help="the notes folder (default: MEMTOOLS_VAULT or the current folder)")
    ap.add_argument("--report", metavar="FILE", help="also write the issues to this markdown file")
    a = ap.parse_args()
    if not VAULT.exists():
        print(f"no notes folder at {VAULT}", file=sys.stderr)
        return 2

    pages = find_pages(VAULT)
    stale_cutoff = datetime.now() - timedelta(days=STALE_DAYS)
    planned = load_planned(VAULT)
    issues, notes, warns = [], [], []
    arcs_dir = LAY["arcs_dir"]

    print(f"health check: {len(pages)} pages.")

    for stem, path in sorted(pages.items()):
        rel = path.relative_to(VAULT)
        content = path.read_text(encoding="utf-8", errors="ignore")

        broken, planned_refs = check_broken_links(content, pages, planned)
        if broken:
            issues.append(f"BROKEN-LINKS   {rel}: {broken}")
        if planned_refs:
            notes.append(f"PLANNED        {rel}: {sorted(set(planned_refs))}")
        wrapped = check_wrapped_links(content)
        if wrapped:
            issues.append(f"WRAPPED-LINK   {rel}: {wrapped} (split across a line break: it resolves nowhere; rejoin it)")

        top_dir = rel.parts[0] if len(rel.parts) > 1 else None
        fm = parse_frontmatter(content)
        if fm is None:
            if REQUIRED_FIELDS:
                issues.append(f"MISSING-FM     {rel}: no frontmatter block")
            continue

        missing = [f for f in REQUIRED_FIELDS if f not in fm]
        if missing:
            issues.append(f"MISSING-FIELDS {rel}: {missing}")
        st = fm.get("status")
        if st and st not in VALID_STATUS:
            issues.append(f"BAD-STATUS     {rel}: status={st!r} (valid: {'|'.join(sorted(VALID_STATUS))})")
        if st == "closed" and "closed" not in fm:
            warns.append(f"CLOSED-NO-DATE {rel}: status=closed has no `closed: YYYY-MM-DD — why` line (vault-close.py writes one)")

        srefs = extract_source_refs(content)
        if srefs:
            sr_issues, sr_notes = check_source_refs(srefs, pages, VAULT)
            if sr_issues:
                issues.append(f"SOURCE-REFS    {rel}: {'; '.join(sr_issues)}")
            for n in sr_notes:
                notes.append(f"REF-SHAPE      {rel}: {n}")

        if top_dir == arcs_dir and st == "active":
            for field in ARC_FIELDS:
                if not str(fm.get(field, "")).strip():
                    issues.append(f"ARC-NO-FIELD   {rel}: active work needs `{field}:`")
            ab = str(fm.get("abandon_if", "")).strip()
            if REQUIRE_ABANDON_IF and not ab:
                issues.append(f"ARC-NO-ABANDON {rel}: active work needs `abandon_if:` (the checkable condition that would end it)")
            for m in re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", ab):
                try:
                    if datetime.strptime(m, "%Y-%m-%d").date() < date.today():
                        warns.append(f"ABANDON-DUE    {rel}: abandon_if date {m} has passed; read it and close or keep it")
                except ValueError:
                    pass

        mat = str(fm.get("maturity", "")).strip().lower()
        if CHECK_MATURITY and mat:
            if mat not in VALID_MATURITY:
                issues.append(f"MATURITY-BAD   {rel}: maturity={mat!r} not in {sorted(VALID_MATURITY)}")
            else:
                has_evidence = bool(str(fm.get("evidence", "")).strip()) or "evidence:" in content or \
                    bool(str(fm.get("source_refs", "")).strip())
                if mat in MATURITY_NEEDS_EVIDENCE and not has_evidence:
                    issues.append(f"MATURITY-NOEV  {rel}: maturity={mat} needs an evidence pointer")
                if mat == "disputed" and not str(fm.get("disputed_by", "")).strip():
                    issues.append(f"MATURITY-NODIS {rel}: maturity=disputed needs `disputed_by:`")
                if mat == "retracted" and not str(fm.get("retracted", "")).strip():
                    issues.append(f"MATURITY-NORET {rel}: maturity=retracted needs a `retracted:` line (why)")

        if st == "active" and "last_reviewed" in fm:
            try:
                if datetime.strptime(fm["last_reviewed"], "%Y-%m-%d") < stale_cutoff:
                    issues.append(f"STALE          {rel}: last_reviewed={fm['last_reviewed']} (over {STALE_DAYS} days, still active)")
            except ValueError:
                issues.append(f"BAD-DATE       {rel}: last_reviewed={fm['last_reviewed']}")

    if notes:
        print(f"\n{len(notes)} planned forward reference(s) (registered in {PLANNED_REGISTER}):")
        for n in notes:
            print("  " + n)
    if warns:
        print(f"\n{len(warns)} warning(s):")
        for w in warns:
            print("  " + w)
    if not issues:
        print("\nhealthy: no issues.")
        return 0
    print(f"\n{len(issues)} issue(s):")
    for i in issues:
        print(i)
    if a.report:
        rp = Path(a.report).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(f"# Health report {datetime.now():%Y-%m-%d}\n\npages: {len(pages)} | issues: {len(issues)}\n\n"
                      + "\n".join(f"- `{i}`" for i in issues) + "\n", encoding="utf-8")
        print(f"\nreport written to {rp}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
