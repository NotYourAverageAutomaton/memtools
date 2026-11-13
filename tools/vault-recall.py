#!/usr/bin/env python3
"""vault-recall — ranked search over a folder of markdown notes: what do I already know about X?

Ranks by text fit (BM25) x tier weight x how often a page was applied (from the log), and follows
[[links]] one step. Usage:  vault-recall.py "what I want to know"  |  --open <stem>  |  --arcs
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from memtools_config import layout, load_config, vault_root

VAULT = vault_root()
CONFIG = load_config(VAULT)
LAY = layout(CONFIG)
LOG = VAULT / "log.md"
INDEX = VAULT / "index.md"
ARCS_DIR = LAY["arcs_dir"]
HOT_HEADING = LAY["hot_heading"]

WIKILINK = re.compile(r"\[\[([^\]|#]+)")
APPLIED = re.compile(r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s*applied\s*\|\s*\[\[([^\]]+)\]\]", re.M)

EXCLUDE_DIRS = LAY["exclude_dirs"]
EXCLUDE_STEMS = {"index", "log", "MEMORY", "AGENTS", "README", "discoveries"}
EXCLUDE_PREFIXES = ("health-report-",)

TIER_WEIGHT = LAY["tiers"]
STATUS_WEIGHT = {"active": 1.0, "closed": 0.90, "draft": 0.95, "stale": 0.6, "superseded": 0.25}

STOP = set("""a an and are as at be but by for from had has have how i if in into is it its of on or
that the their then there these they this to was were what when where which who will with you your
not no do does did can could should would may might must than them we us our my me it's dont don't
about after before over under again once here why all any both each few more most other some such
only own same so too very just also""".split())


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9_]+", text.lower()) if t not in STOP and len(t) > 1]


def stem(tok: str) -> str:
    """Crude suffix stripping. A real stemmer is a dependency; this catches the cases that"""
    for suf in ("ingly", "edly", "ments", "ment", "ings", "ing", "ions", "ion", "ies", "ied",
                "ers", "er", "ed", "es", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            return tok[: -len(suf)]
    return tok


def frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([a-z_]+):\s*(.*)$", line.strip())
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


def tier_of(rel: str) -> str:
    for t in TIER_WEIGHT:
        if rel.startswith(t + "/"):
            return t
    return ""


def load_corpus() -> dict[str, dict]:
    pages = {}
    for p in sorted(VAULT.rglob("*.md")):
        if any(d in p.parts for d in EXCLUDE_DIRS):
            continue
        if p.stem in EXCLUDE_STEMS or p.stem.startswith(EXCLUDE_PREFIXES):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(p.relative_to(VAULT))
        fm = frontmatter(text)
        body = text[text.find("\n---", 3) + 4:] if text.startswith("---") else text
        heads = " ".join(re.findall(r"^#{1,3}\s+(.*)$", body, re.M))
        title = re.search(r"^#\s+(.*)$", body, re.M)
        pages[p.stem] = {
            "stem": p.stem,
            "path": p,
            "rel": rel,
            "tier": tier_of(rel),
            "fm": fm,
            "tags": fm.get("tags", ""),
            "status": fm.get("status", "active"),
            "reviewed": fm.get("last_reviewed", ""),
            "title": title.group(1).strip() if title else p.stem.replace("-", " "),
            "body": body,
            "text": " ".join([p.stem.replace("-", " ")] * 3 + [heads] * 2
                             + [fm.get("tags", "")] * 2 + [body]),
            "links": set(WIKILINK.findall(body)),
        }
    return pages


def log_sources() -> list[Path]:
    """log.md AND every rotated archive."""
    return sorted(VAULT.glob("log.md")) + sorted(VAULT.glob("log_*.md"))


def applied_days(pages: dict) -> dict[str, int]:
    """Distinct days each page was cited as having driven a decision. THE signal."""
    seen = defaultdict(set)
    for src in log_sources():
        for day, page in APPLIED.findall(src.read_text(encoding="utf-8", errors="ignore")):
            seen[page].add(day)
    return {k: len(v) for k, v in seen.items()}


def bm25(pages: dict, qterms: list[str], k1: float = 1.4, b: float = 0.72) -> dict[str, float]:
    docs, doc_len = {}, {}
    for stem_, pg in pages.items():
        toks = [stem(t) for t in tokenize(pg["text"])]
        docs[stem_] = Counter(toks)
        doc_len[stem_] = len(toks) or 1
    avg = sum(doc_len.values()) / max(len(doc_len), 1)
    N = len(docs)
    df = Counter()
    for c in docs.values():
        for t in set(qterms):
            if c[t]:
                df[t] += 1
    out = {}
    for stem_, c in docs.items():
        s = 0.0
        for t in qterms:
            f = c[t]
            if not f:
                continue
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * doc_len[stem_] / avg))
        if s:
            out[stem_] = s
    return out


def freshness(reviewed: str) -> float:
    """Mild. A 2026-04 procedural rule can be the best answer in the vault, so age must"""
    if not reviewed:
        return 0.92
    try:
        age = (date.today() - datetime.strptime(reviewed, "%Y-%m-%d").date()).days
    except ValueError:
        return 0.92
    return max(0.80, 1.0 - age / 1400)


def rank(pages: dict, query: str, applied: dict, tier_filter: str | None,
         want: int, expand: bool = True) -> list[dict]:
    qterms = [stem(t) for t in tokenize(query)]
    if not qterms:
        return []
    base = bm25(pages, qterms)
    if not base:
        return []
    top = max(base.values())
    scored = []
    for stem_, raw in base.items():
        pg = pages[stem_]
        if tier_filter and not pg["tier"].endswith(tier_filter):
            continue
        days = applied.get(stem_, 0)
        use = 1.0 + 0.55 * math.log1p(days)
        title_toks = {stem(t) for t in tokenize(stem_.replace("-", " "))}
        frac = len(set(qterms) & title_toks) / max(len(set(qterms)), 1)
        name = 1.0 + 2.60 * frac * (raw / top) if frac >= 0.55 else 1.0
        score = ((raw / top) * TIER_WEIGHT.get(pg["tier"], 1.0) * use * name
                 * STATUS_WEIGHT.get(pg["status"], 1.0) * freshness(pg["reviewed"]))
        cov = len(set(qterms) & {stem(t) for t in tokenize(pg["text"])}) / max(len(set(qterms)), 1)
        scored.append({"stem": stem_, "score": score, "days": days, "via": None, "pg": pg, "cov": cov})
    scored.sort(key=lambda d: -d["score"])
    hits = scored[:want]

    if expand and hits:
        have = {h["stem"] for h in hits}
        extra = {}
        for h in hits[:5]:
            for tgt in h["pg"]["links"]:
                if tgt in pages and tgt not in have:
                    s = h["score"] * 0.30 * TIER_WEIGHT.get(pages[tgt]["tier"], 1.0)
                    if s > extra.get(tgt, {"score": 0})["score"]:
                        extra[tgt] = {"stem": tgt, "score": s, "days": applied.get(tgt, 0),
                                      "via": h["stem"], "pg": pages[tgt]}
        if extra:
            merged = hits + sorted(extra.values(), key=lambda d: -d["score"])[:max(2, want // 3)]
            merged.sort(key=lambda d: -d["score"])
            hits = merged[:want + 2]
    return hits


def excerpt(body: str, qterms: set[str], width: int = 3) -> str:
    """The passage that actually answers the query — highest query-term density, not the"""
    lines = [l.rstrip() for l in body.splitlines()]
    best, best_i = -1.0, 0
    for i in range(len(lines)):
        win = lines[i:i + width]
        toks = [stem(t) for t in tokenize(" ".join(win))]
        if not toks:
            continue
        hit = sum(1 for t in toks if t in qterms)
        dens = hit + 0.35 * len(set(t for t in toks if t in qterms))
        if dens > best:
            best, best_i = dens, i
    if best <= 0:
        for i, l in enumerate(lines):
            if l.strip() and not l.startswith("#"):
                best_i = i
                break
    win = [l for l in lines[best_i:best_i + width] if l.strip()]
    txt = " ".join(win)
    txt = re.sub(r"\s+", " ", txt).strip(" #*->|")
    return txt[:330] + ("…" if len(txt) > 330 else "")


def applies_when(body: str) -> str:
    m = re.search(r"^#{2,3}\s*Applies[- ]when.*?$(.*?)(?=^#{1,3}\s|\Z)", body,
                  re.M | re.S | re.I)
    if not m:
        m = re.search(r"^\s*[-*]?\s*\*\*Applies-when:?\*\*(.*?)(?=\n\n|\Z)", body, re.M | re.S | re.I)
    if not m:
        return ""
    txt = re.sub(r"\s+", " ", m.group(1)).strip(" -*·")
    return txt[:240] + ("…" if len(txt) > 240 else "")


def render(hits: list[dict], query: str, applied: dict) -> None:
    if not hits:
        print(f"no recall for: {query!r}\n"
              "  try fewer / more common terms, or --tier working to widen")
        return
    qterms = {stem(t) for t in tokenize(query)}
    print(f"=== vault-recall: {len(hits)} hits for {query!r} "
          f"— ranked by fit x tier x times-applied ===\n")
    best = max((h.get("cov", 0.0) for h in hits), default=0.0)
    if len(qterms) >= 2 and best < 0.5:
        n = len(qterms); k = round(best * n)
        print(f"!! LOW CONFIDENCE: the best page holds only {k} of your {n} words. "
              "The vault probably does not know this; treat these as loose associations.\n")
    for i, h in enumerate(hits, 1):
        pg = h["pg"]
        tier = pg["tier"].split("/")[-1]
        marks = [tier]
        if h["days"]:
            marks.append(f"applied {h['days']}d")
        if pg["status"] not in ("active", ""):
            marks.append(pg["status"].upper())
        if h["via"]:
            marks.append(f"via [[{h['via']}]]")
        print(f"[{i}] {h['score']:.2f}  {' · '.join(marks)}")
        print(f"    {pg['title']}")
        print(f"    {pg['rel']}")
        if pg["tags"]:
            print(f"    tags: {pg['tags'].strip('[]')}")
        print(f"    > {excerpt(pg['body'], qterms)}")
        aw = applies_when(pg["body"])
        if aw:
            print(f"    applies-when: {aw}")
        links = sorted(l for l in pg["links"] if l in APPLIED_SET)[:4]
        if links:
            print(f"    -> {' '.join('[[' + l + ']]' for l in links)}")
        print()
    print("read one in full:  vault-recall.py --open <page-stem>")


APPLIED_SET: set = set()


def show_arcs() -> int:
    """After a compaction, the first question is never 'what do I know' but 'where was I'."""
    if not INDEX.exists():
        print("no index.md", file=sys.stderr)
        return 1
    text = INDEX.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"^## .*" + re.escape(HOT_HEADING) + r".*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    print(m.group(1).strip() if m else f"no '{HOT_HEADING}' block found in index.md")
    print("\n--- arc pages claiming status:active, newest first ---")
    print("    (read the page before touching the arc: the page outranks memory)")
    rows = []
    for p in sorted((VAULT / ARCS_DIR).glob("*.md")):
        raw = p.read_text(encoding="utf-8", errors="ignore")
        fm_text = raw[: raw.find("\n---", 3)] if raw.startswith("---") else ""
        fm = frontmatter(raw)
        if fm.get("status") != "active":
            continue
        rev = fm.get("last_reviewed", "")
        try:
            age = (date.today() - datetime.strptime(rev, "%Y-%m-%d").date()).days
        except ValueError:
            age = 9999
        rows.append((age, p.stem, rev, "verify:" in fm_text))
    for age, stem_, rev, has_verify in sorted(rows):
        if age <= 7:
            flag = ""
        elif has_verify:
            flag = "  <- unreviewed, but carries verify: assertions (run vault-verify)"
        else:
            flag = "  <- unreviewed, and NO verify: assertions — correspondence UNCHECKED"
        print(f"  {age:4d}d  {stem_}   ({rev or '?'}){flag}")
    hot = sum(1 for age, _, _, _ in rows if age <= 7)
    print(f"\n  {len(rows)} pages claim active; {hot} touched in the last 7d. "
          f"Keep the live ones few.")

    closed = []
    for p in sorted((VAULT / ARCS_DIR).glob("*.md")):
        raw = p.read_text(encoding="utf-8", errors="ignore")
        fm = frontmatter(raw)
        if fm.get("status") != "closed":
            continue
        m = re.search(r"^closed:\s*(.*)$", raw[: raw.find("\n---", 3)], re.M)
        closed.append((p.stem, m.group(1).strip() if m else "(no reason stamped)"))
    if closed:
        print(f"\n--- closed arcs ({len(closed)}) — finished, not decayed; still true ---")
        for stem_, why in closed:
            print(f"  {stem_}\n      {why}")
    return 0


def main() -> int:
    global APPLIED_SET
    ap = argparse.ArgumentParser(description="recall from the vault — the read path")
    ap.add_argument("query", nargs="*", help="what you want to know")
    ap.add_argument("-n", type=int, default=8, help="how many hits (default 8)")
    ap.add_argument("--tier", help="restrict to a tier folder named in the config (suffix match)")
    ap.add_argument("--vault", help="the notes folder (default: MEMTOOLS_VAULT or the current folder)")
    ap.add_argument("--open", metavar="STEM", help="print a whole page")
    ap.add_argument("--arcs", action="store_true", help="what am I in the middle of?")
    ap.add_argument("--paths", action="store_true", help="bare paths, for chaining")
    ap.add_argument("--json", action="store_true", help="structured output")
    ap.add_argument("--no-expand", action="store_true", help="skip the link-graph step")
    a = ap.parse_args()
    if a.vault:
        global VAULT, LOG, INDEX
        VAULT = Path(a.vault).expanduser().resolve()
        LOG, INDEX = VAULT / "log.md", VAULT / "index.md"

    if a.arcs:
        return show_arcs()

    pages = load_corpus()
    APPLIED_SET = set(pages)

    if a.open:
        key = a.open.replace(".md", "").strip("[]")
        pg = pages.get(key) or next((p for k, p in pages.items() if key in k), None)
        if not pg:
            print(f"no such page: {a.open}", file=sys.stderr)
            near = [k for k in pages if any(t in k for t in tokenize(key))][:8]
            if near:
                print("did you mean: " + ", ".join(near), file=sys.stderr)
            return 1
        print(pg["path"].read_text(encoding="utf-8", errors="ignore"))
        return 0

    if not a.query:
        ap.print_help()
        return 2

    query = " ".join(a.query)
    applied = applied_days(pages)
    hits = rank(pages, query, applied, a.tier, a.n, expand=not a.no_expand)

    if a.json:
        print(json.dumps([{"stem": h["stem"], "path": h["pg"]["rel"], "score": round(h["score"], 3),
                           "tier": h["pg"]["tier"], "applied_days": h["days"],
                           "title": h["pg"]["title"], "via": h["via"]} for h in hits], indent=2))
    elif a.paths:
        for h in hits:
            print(VAULT / h["pg"]["rel"])
    else:
        render(hits, query, applied)
    return 0


if __name__ == "__main__":
    sys.exit(main())
