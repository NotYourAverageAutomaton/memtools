#!/usr/bin/env python3
"""vault-graph — one self-contained HTML page showing your notes as a graph, for a person to look at.

Node size = times applied (from the log), colour = folder tier or topic tag, dashed lines = links that
triadic closure suggests are missing. No network access, no external scripts.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from memtools_config import layout, load_config, vault_root

VAULT = vault_root()
CONFIG = load_config(VAULT)
LAY = layout(CONFIG)

WIKILINK = re.compile(r"\[\[([^\]|#]+)")
EXCLUDE = {"index", "log", "MEMORY", "AGENTS", "README", "discoveries"}
EXCLUDE_DIRS = LAY["exclude_dirs"]

PALETTE = ["#00d47e", "#4aa8ff", "#ffb020", "#9b8cff", "#ff5c7a", "#00c8c8",
           "#c77dff", "#a3e635", "#f472b6", "#ffffff", "#8a8f98"]
TIER_BY_DIR = {d: d.split("/")[-1] for d in LAY["tiers"]}
TOPICS = [(name, {str(x).lower() for x in seeds})
          for name, seeds in ((CONFIG.get("graph", {}) or {}).get("topics", {}) or {}).items()]
TIER_COLOURS = {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(TIER_BY_DIR.values())}
TIER_COLOURS["other"] = "#5b6472"
TOPIC_COLOURS = {n: PALETTE[i % len(PALETTE)] for i, (n, _) in enumerate(TOPICS)}
TOPIC_COLOURS["unclustered"] = "#4a5058"


def tier_of(path: Path) -> str:
    rel = path.relative_to(VAULT).as_posix()
    for prefix, tier in TIER_BY_DIR.items():
        if rel.startswith(prefix + "/"):
            return tier
    return "other"


def load_pages() -> dict[str, Path]:
    pages = {}
    for p in sorted(VAULT.rglob("*.md")):
        if any(d in p.parts for d in EXCLUDE_DIRS):
            continue
        if p.stem in EXCLUDE or p.stem.startswith("health-report-"):
            continue
        pages[p.stem] = p
    return pages


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return {}, text
    fm, body = {}, text[m.end():]
    for line in m.group(1).splitlines():
        km = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if not km:
            continue
        k, v = km.group(1), km.group(2).strip()
        if v.startswith("[") and v.endswith("]"):
            fm[k] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        else:
            fm[k] = v.strip("'\"")
    return fm, body


def topic_of(tags: list[str]) -> str:
    tagset = {t.lower() for t in tags}
    for name, seeds in TOPICS:
        if tagset & seeds:
            return name
    return "unclustered"


def load_hooks() -> dict[str, str]:
    """One-line summaries, harvested from index.md's `[[name]] - hook` entries."""
    hooks = {}
    idx = VAULT / "index.md"
    if not idx.exists():
        return hooks
    for line in idx.read_text(errors="ignore").splitlines():
        m = re.match(r"^\s*-?\s*\[\[([^\]|#]+)\]\]\s*[-—–]\s*(.+)$", line)
        if m:
            hooks[m.group(1).strip()] = re.sub(r"\s+", " ", m.group(2)).strip()
    return hooks


def load_applied(pages: dict[str, Path]) -> tuple[Counter, dict[str, list]]:
    """`## [YYYY-MM-DD] applied | [[page]] -> outcome` — the only honest ranking signal."""
    counts: Counter = Counter()
    detail: dict[str, list] = defaultdict(list)
    logs = sorted(VAULT.glob("log.md")) + sorted(VAULT.glob("log_*.md"))
    seen_lines = set()
    lines = []
    for f in logs:
        for l in f.read_text(errors="ignore").splitlines():
            k = l.strip()
            if k and k in seen_lines:
                continue
            seen_lines.add(k)
            lines.append(l)
    for line in lines:
        m = re.match(r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s*applied\s*\|\s*(.+)$", line)
        if not m:
            continue
        when, rest = m.group(1), m.group(2)
        named = [x.strip() for x in WIKILINK.findall(rest)]
        outcome = re.split(r"->|→", rest, maxsplit=1)
        outcome = re.sub(r"\s+", " ", outcome[1]).strip() if len(outcome) > 1 else ""
        for n in named:
            if n in pages:
                counts[n] += 1
                detail[n].append({"date": when, "outcome": outcome})
    return counts, detail


def applied_days(detail: dict[str, list]) -> Counter:
    """⚠ DISTINCT DAYS, NOT LOG LINES — THEY ARE NOT THE SAME NUMBER AND BOTH READ AS "times"""
    return Counter({k: len({a["date"] for a in v}) for k, v in detail.items()})


def triadic(out: dict[str, set], top: int) -> list[tuple[str, str, list[str]]]:
    """A->B, B->C, no A->C. Lifted from vault-discover.py — same gaps, same ranking."""
    bridges: dict[tuple[str, str], set] = defaultdict(set)
    for a, bs in out.items():
        for b in bs:
            for c in out.get(b, ()):
                if c == a or c in out[a]:
                    continue
                if a in out.get(c, set()):
                    continue
                key = (a, c) if a < c else (c, a)
                bridges[key].add(b)
    ranked = sorted(bridges.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [(a, c, sorted(bs)) for (a, c), bs in ranked[:top]]


def days_old(s: str) -> int | None:
    try:
        return (date.today() - datetime.strptime(s, "%Y-%m-%d").date()).days
    except (ValueError, TypeError):
        return None


def collect(top_gaps: int) -> dict:
    pages = load_pages()
    hooks = load_hooks()
    applied, applied_detail = load_applied(pages)

    out: dict[str, set] = {}
    src_edges: dict[str, set] = {}
    meta: dict[str, dict] = {}
    inbound: Counter = Counter()

    for stem, path in pages.items():
        text = path.read_text(errors="ignore")
        fm, body = parse_frontmatter(text)
        links = {m.strip() for m in WIKILINK.findall(body)}
        links = {l for l in links if l in pages and l != stem}
        out[stem] = links
        for l in links:
            inbound[l] += 1

        refs = fm.get("source_refs") or []
        if isinstance(refs, str):
            refs = [refs] if refs else []
        src_edges[stem] = {r for r in refs if r in pages and r != stem}

        tags = fm.get("tags") or []
        if isinstance(tags, str):
            tags = [tags] if tags else []
        meta[stem] = {
            "tier": tier_of(path),
            "tags": tags,
            "topic": topic_of(tags),
            "status": fm.get("status", "?"),
            "page_type": fm.get("page_type", "?"),
            "confidence": fm.get("confidence", "?"),
            "reviewed": fm.get("last_reviewed", ""),
            "age": days_old(fm.get("last_reviewed", "")),
            "extrefs": [r for r in refs if r not in pages],
            "path": path.relative_to(VAULT).as_posix(),
            "hook": hooks.get(stem, ""),
        }

    days = applied_days(applied_detail)
    gaps = triadic(out, top_gaps)

    nodes = []
    for stem in sorted(pages):
        m = meta[stem]
        nodes.append({
            "id": stem, "tier": m["tier"], "topic": m["topic"], "tags": m["tags"],
            "status": m["status"], "ptype": m["page_type"], "conf": m["confidence"],
            "age": m["age"], "reviewed": m["reviewed"], "path": m["path"],
            "hook": m["hook"], "applied": days.get(stem, 0), "cites": applied.get(stem, 0),
            "aps": applied_detail.get(stem, []),
            "inb": inbound.get(stem, 0), "outb": len(out[stem]),
            "extrefs": m["extrefs"],
            "orphan": inbound.get(stem, 0) == 0,
            "deadend": len(out[stem]) == 0,
        })

    edges = []
    seen = set()
    for a, bs in out.items():
        for b in bs:
            edges.append({"s": a, "t": b, "k": "link"})
            seen.add((a, b))
    for a, bs in src_edges.items():
        for b in bs:
            if (a, b) not in seen and (b, a) not in seen:
                edges.append({"s": a, "t": b, "k": "src"})
    for a, c, bs in gaps:
        edges.append({"s": a, "t": c, "k": "gap", "via": bs})

    tag_counts = Counter(t for s in pages for t in meta[s]["tags"])
    return {
        "nodes": nodes, "edges": edges,
        "tiers": sorted({n["tier"] for n in nodes}),
        "topics": sorted({n["topic"] for n in nodes}),
        "toptags": [t for t, _ in tag_counts.most_common(40)],
        "tierColours": TIER_COLOURS, "topicColours": TOPIC_COLOURS,
        "generated": f"{datetime.now():%Y-%m-%d %H:%M}",
        "stats": {
            "pages": len(nodes),
            "links": sum(1 for e in edges if e["k"] == "link"),
            "srcs": sum(1 for e in edges if e["k"] == "src"),
            "gaps": sum(1 for e in edges if e["k"] == "gap"),
            "orphans": sum(1 for n in nodes if n["orphan"]),
            "deadends": sum(1 for n in nodes if n["deadend"]),
            "applied": sum(n["applied"] for n in nodes),
            "cites": sum(n["cites"] for n in nodes),
            "everApplied": sum(1 for n in nodes if n["applied"] > 0),
        },
    }


def solve_layout(data: dict, iters: int = 420) -> dict:
    keep = {n["id"] for n in data["nodes"] if n["applied"] > 0}
    nodes = [n for n in data["nodes"] if n["id"] in keep]
    idx = {n["id"]: i for i, n in enumerate(nodes)}
    edges = [(idx[e["s"]], idx[e["t"]]) for e in data["edges"]
             if e["k"] != "gap" and e["s"] in keep and e["t"] in keep]

    deg = [1.0] * len(nodes)
    for a, b in edges:
        deg[a] += 1; deg[b] += 1
    dsq = [math.sqrt(d) for d in deg]

    mx = max((n["applied"] for n in nodes), default=1) or 1
    rad = [3.0 + math.sqrt(n["applied"]) * 3.4 for n in nodes]

    rng = random.Random(0x5EED)
    P = [[math.cos(i * 2.399) * (40 + 9 * math.sqrt(i)) + rng.uniform(-3, 3),
          math.sin(i * 2.399) * (40 + 9 * math.sqrt(i)) + rng.uniform(-3, 3)]
         for i in range(len(nodes))]
    V = [[0.0, 0.0] for _ in nodes]
    alpha = 1.0

    for _ in range(iters):
        for i in range(len(nodes)):
            ax, ay = P[i]
            for j in range(i + 1, len(nodes)):
                bx, by = P[j]
                dx, dy = bx - ax, by - ay
                d2 = dx * dx + dy * dy
                if d2 < 1e-4:
                    dx, dy, d2 = rng.uniform(-.5, .5), rng.uniform(-.5, .5), .01
                if d2 > 160000:
                    continue
                d = math.sqrt(d2)
                f = 2100.0 / d2
                fx, fy = dx / d * f, dy / d * f
                V[i][0] -= fx; V[i][1] -= fy
                V[j][0] += fx; V[j][1] += fy
                mind = rad[i] + rad[j] + 4
                if d < mind:
                    push = (mind - d) / d * .5
                    P[i][0] -= dx * push; P[i][1] -= dy * push
                    P[j][0] += dx * push; P[j][1] += dy * push
            V[i][0] -= P[i][0] * .0022; V[i][1] -= P[i][1] * .0022
        for a, b in edges:
            dx, dy = P[b][0] - P[a][0], P[b][1] - P[a][1]
            d = max(1.0, math.hypot(dx, dy))
            f = (d - 70) * 0.05 / d
            fa, fb = f / dsq[a], f / dsq[b]
            V[a][0] += dx * fa; V[a][1] += dy * fa
            V[b][0] -= dx * fb; V[b][1] -= dy * fb
        for i in range(len(nodes)):
            V[i][0] *= .82; V[i][1] *= .82
            sp = math.hypot(V[i][0], V[i][1])
            if sp > 60:
                V[i][0] = V[i][0] / sp * 60; V[i][1] = V[i][1] / sp * 60
            P[i][0] += V[i][0] * alpha; P[i][1] += V[i][1] * alpha
            if not (math.isfinite(P[i][0]) and math.isfinite(P[i][1])):
                P[i] = [rng.uniform(-200, 200), rng.uniform(-200, 200)]; V[i] = [0.0, 0.0]
        alpha *= .992

    xs0 = min(P[i][0] - rad[i] for i in range(len(nodes)))
    xs1 = max(P[i][0] + rad[i] for i in range(len(nodes)))
    ys0 = min(P[i][1] - rad[i] for i in range(len(nodes)))
    ys1 = max(P[i][1] + rad[i] for i in range(len(nodes)))
    span = max(xs1 - xs0, ys1 - ys0) or 1.0
    ox, oy = (xs0 + xs1) / 2, (ys0 + ys1) / 2

    out_nodes = []
    for i, n in enumerate(nodes):
        out_nodes.append({
            "id": n["id"],
            "x": round((P[i][0] - ox) / span, 5),
            "y": round((P[i][1] - oy) / span, 5),
            "r": round(rad[i] / span, 6),
            "a": n["applied"],
            "t": n["tier"],
            "c": TIER_COLOURS.get(n["tier"], "#8a8f98"),
            "hook": (n["hook"] or "")[:150],
        })
    return {
        "generated": data["generated"],
        "stats": data["stats"],
        "maxApplied": mx,
        "nodes": out_nodes,
        "edges": sorted({(min(a, b), max(a, b)) for a, b in edges}),
    }

def render(data: dict) -> str:
    payload = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    import html as _h
    return HTML.replace("__TITLE__", _h.escape(VAULT.name)).replace("__DATA__", payload)


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ — notes graph</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:#0e1116;color:#e6e9ef;
  font:13px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;overflow:hidden}
#app{display:flex;flex-direction:column;height:100%}
header{padding:10px 16px;border-bottom:1px solid #232833;display:flex;
  align-items:baseline;gap:16px;flex-wrap:wrap;background:#11151c}
h1{font-size:15px;margin:0;font-weight:650;letter-spacing:.2px}
.sub{color:#7d8695;font-size:12px}
.sub b{color:#b9c2d0;font-weight:600}
#bar{display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:8px 16px;
  border-bottom:1px solid #232833;background:#11151c}
.grp{display:flex;gap:6px;align-items:center}
.grp>label{color:#7d8695;font-size:11px;text-transform:uppercase;letter-spacing:.6px}
select,input[type=search]{background:#1a1f28;color:#e6e9ef;border:1px solid #2c333f;
  border-radius:5px;padding:4px 7px;font:inherit;font-size:12px}
input[type=search]{width:190px}
input[type=range]{width:110px;accent-color:#4aa8ff}
.chk{display:flex;align-items:center;gap:4px;color:#b9c2d0;font-size:12px;cursor:pointer;
  user-select:none}
.chk input{accent-color:#4aa8ff;margin:0}
button{background:#1a1f28;color:#b9c2d0;border:1px solid #2c333f;border-radius:5px;
  padding:4px 9px;font:inherit;font-size:12px;cursor:pointer}
button:hover{background:#232a35;color:#e6e9ef}
#main{flex:1;position:relative;overflow:hidden}
/* width/height are REQUIRED: <canvas> is a replaced element with an intrinsic 300x150,
   so inset:0 alone leaves it at 300x150 and the graph draws into a corner. */
canvas{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab}
canvas.drag{cursor:grabbing}
#panel{position:absolute;top:0;right:0;width:340px;height:100%;background:#141922f2;
  border-left:1px solid #232833;padding:16px;overflow-y:auto;transform:translateX(100%);
  transition:transform .16s ease;backdrop-filter:blur(6px)}
#panel.open{transform:none}
#panel h2{font-size:14px;margin:0 0 3px;line-height:1.35;word-break:break-word}
#panel .hook{color:#9aa4b2;font-size:12px;margin:0 0 12px;font-style:italic;cursor:pointer}
#panel .hook .rest{display:none}
#panel .hook.open .rest{display:inline}
#panel .hook.open .ell{display:none}
#panel .hook .ell{color:#7fb8ff;font-style:normal}
.kv{display:grid;grid-template-columns:76px 1fr;gap:3px 8px;font-size:12px;margin-bottom:12px}
.kv dt{color:#7d8695}
.kv dd{margin:0;color:#d5dae3;word-break:break-word}
.sec{border-top:1px solid #232833;padding-top:10px;margin-top:10px}
.sec h3{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:#7d8695;
  margin:0 0 7px;font-weight:600}
.pill{display:inline-block;background:#1c222c;border:1px solid #2c333f;border-radius:10px;
  padding:1px 7px;margin:0 3px 3px 0;font-size:11px;color:#9aa4b2}
.lnk{display:block;color:#7fb8ff;cursor:pointer;padding:1px 0;font-size:12px;
  text-decoration:none;word-break:break-word}
.lnk:hover{color:#a9d1ff;text-decoration:underline}
.ap{border-left:2px solid #00d47e;padding:2px 0 2px 8px;margin-bottom:7px;font-size:12px;
  color:#c3cad5}
.ap b{color:#00d47e;font-weight:600;font-size:11px}
#close{position:absolute;top:10px;right:12px;background:none;border:none;color:#7d8695;
  font-size:18px;padding:0 4px;line-height:1}
#legend{position:absolute;left:12px;bottom:12px;background:#141922e6;border:1px solid #232833;
  border-radius:7px;padding:9px 11px;font-size:11px;max-width:230px;backdrop-filter:blur(6px)}
#legend .row{display:flex;align-items:center;gap:6px;margin:2px 0;color:#9aa4b2}
#legend .sw{width:9px;height:9px;border-radius:50%;flex:none}
#legend .lbl{font-weight:600;color:#b9c2d0;margin:6px 0 3px;font-size:10px;
  text-transform:uppercase;letter-spacing:.5px}
#legend .lbl:first-child{margin-top:0}
#tip{position:absolute;pointer-events:none;background:#0b0e13f2;border:1px solid #2c333f;
  border-radius:5px;padding:5px 8px;font-size:12px;display:none;max-width:280px;z-index:5}
#tip b{color:#fff}#tip span{color:#8a93a2}
#hint{position:absolute;right:12px;bottom:12px;color:#5b6472;font-size:11px}
</style></head><body><div id="app">
<header>
  <h1>__TITLE__</h1>
  <div class="sub" id="stats"></div>
</header>
<div id="bar">
  <div class="grp"><label>colour</label><select id="colourBy">
    <option value="tier">tier</option><option value="topic">topic (derived)</option>
    <option value="status">status</option><option value="ptype">page_type</option>
    <option value="age">recency</option><option value="applied">applied (ramp)</option>
  </select></div>
  <div class="grp"><label>size</label><select id="sizeBy">
    <option value="applied">days applied</option><option value="cites">log citations</option><option value="inb">inbound links</option>
    <option value="outb">outbound links</option><option value="deg">total degree</option>
    <option value="flat">uniform</option>
  </select></div>
  <div class="grp"><label>edges</label>
    <label class="chk"><input type="checkbox" id="eLink" checked>links</label>
    <label class="chk"><input type="checkbox" id="eSrc" checked>source_refs</label>
    <label class="chk"><input type="checkbox" id="eGap" checked>gaps</label>
  </div>
  <div class="grp"><label>tag</label><select id="tagSel"><option value="">all</option></select></div>
  <div class="grp"><label>applied ≥</label><input type="range" id="minApplied" min="0" max="10" value="0">
    <span id="minAppliedV" style="color:#b9c2d0;width:14px;display:inline-block">0</span></div>
  <div class="grp">
    <label class="chk"><input type="checkbox" id="orphOnly">orphans only</label>
    <label class="chk"><input type="checkbox" id="labels">all labels</label>
  </div>
  <input type="search" id="search" placeholder="search pages…">
  <button id="reset">reset view</button>
</div>
<div id="main">
  <canvas id="cv"></canvas>
  <div id="legend"></div>
  <div id="tip"></div>
  <div id="hint">drag to pan · scroll to zoom · drag a node to pin · click for detail</div>
  <div id="panel"><button id="close">×</button><div id="pbody"></div></div>
</div></div>
<script>
const D = __DATA__;
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const N = D.nodes, E = D.edges;
const byId = new Map(N.map(n => [n.id, n]));
const TIERC = D.tierColours, TOPICC = D.topicColours;
const PALETTE = ['#4aa8ff','#00d47e','#ffb020','#ff5c7a','#9b8cff','#00c8c8','#ff9f40',
                 '#c77dff','#6ee7b7','#f472b6','#8a8f98','#e2e8f0'];

/* ---- adjacency, for neighbour highlighting and the detail panel ---- */
const adj = new Map(N.map(n => [n.id, {out:[], in:[], src:[], gap:[]}]));
for (const e of E) {
  if (e.k === 'link') { adj.get(e.s).out.push(e.t); adj.get(e.t).in.push(e.s); }
  else if (e.k === 'src') { adj.get(e.s).src.push(e.t); adj.get(e.t).src.push(e.s); }
  else { adj.get(e.s).gap.push({o:e.t, via:e.via}); adj.get(e.t).gap.push({o:e.s, via:e.via}); }
}

/* ---- layout state: deterministic seeding so the map is the same map each open ---- */
let seed = 12345;
const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
for (const n of N) {
  const a = rnd() * Math.PI * 2, r = 120 + rnd() * 340;
  n.x = Math.cos(a) * r; n.y = Math.sin(a) * r; n.vx = 0; n.vy = 0; n.fixed = false;
}
/* ⚠ DEGREE IS COMPUTED, NOT ASSUMED. The spring normalisation divides by it, and an undefined
   field would quietly fall back to 1 — leaving the divergence exactly as it was while the code
   read as if it had been fixed. */
for (const n of N) {
  const a = adj.get(n.id);
  n.deg = a ? (a.out.length + a.in.length + a.src.length + a.gap.length) : 0;
}
const BG = '#0e1116';                                  // must track html,body background
let view = {x:0, y:0, k:1}, alpha = 1, sel = null, hover = null, drag = null, panning = null;

/* ---- controls ---- */
const $ = id => document.getElementById(id);
const ui = {colourBy:$('colourBy'), sizeBy:$('sizeBy'), eLink:$('eLink'), eSrc:$('eSrc'),
  eGap:$('eGap'), tagSel:$('tagSel'), minApplied:$('minApplied'), orphOnly:$('orphOnly'),
  labels:$('labels'), search:$('search')};
for (const t of D.toptags) { const o = document.createElement('option');
  o.value = t; o.textContent = t; ui.tagSel.appendChild(o); }
$('stats').innerHTML = `<b>${D.stats.pages}</b> pages · <b>${D.stats.links}</b> links · ` +
  `<b>${D.stats.srcs}</b> source_refs · <b>${D.stats.gaps}</b> gaps · ` +
  `<b>${D.stats.orphans}</b> orphans · <b>${D.stats.everApplied}</b> ever applied ` +
  `(${D.stats.applied} day-applications, ${D.stats.cites} citations) · <span>${D.generated}</span>`;

const maxApplied = Math.max(1, ...N.map(n => n.applied));
ui.minApplied.max = maxApplied;

/* ---- colour + size encodings ---- */
const catCache = new Map();
function categoricalColour(field) {
  if (!catCache.has(field)) {
    const vals = [...new Set(N.map(n => String(n[field] ?? '?')))].sort();
    catCache.set(field, new Map(vals.map((v, i) => [v, PALETTE[i % PALETTE.length]])));
  }
  return catCache.get(field);
}
function ramp(t, from, to) {
  const c = (a, b) => Math.round(a + (b - a) * Math.max(0, Math.min(1, t)));
  return `rgb(${c(from[0],to[0])},${c(from[1],to[1])},${c(from[2],to[2])})`;
}
function colourOf(n) {
  const m = ui.colourBy.value;
  if (m === 'tier') return TIERC[n.tier] || '#8a8f98';
  if (m === 'topic') return TOPICC[n.topic] || '#4a5058';
  if (m === 'age') { const a = n.age == null ? 400 : n.age;
    return ramp(Math.min(a, 180) / 180, [0, 212, 126], [90, 96, 108]); }
  if (m === 'applied') { if (!n.applied) return '#3a4049';
    return ramp(n.applied / maxApplied, [60, 90, 140], [255, 176, 32]); }
  return categoricalColour(m).get(String(n[m] ?? '?')) || '#8a8f98';
}
function sizeOf(n) {
  const m = ui.sizeBy.value;
  if (m === 'flat') return 4.5;
  const v = m === 'deg' ? n.inb + n.outb : n[m];
  return 3.4 + Math.sqrt(v) * 2.5;
}

/* ---- filtering ---- */
let visible = new Set(), visEdges = [];
function applyFilters() {
  const tag = ui.tagSel.value, min = +ui.minApplied.value, orph = ui.orphOnly.checked;
  const q = ui.search.value.trim().toLowerCase();
  visible = new Set();
  for (const n of N) {
    if (tag && !n.tags.includes(tag)) continue;
    if (n.applied < min) continue;
    if (orph && !n.orphan) continue;
    n.match = q ? (n.id.toLowerCase().includes(q) || (n.hook || '').toLowerCase().includes(q)) : false;
    visible.add(n.id);
  }
  visEdges = E.filter(e => {
    if (e.k === 'link' && !ui.eLink.checked) return false;
    if (e.k === 'src' && !ui.eSrc.checked) return false;
    if (e.k === 'gap' && !ui.eGap.checked) return false;
    return visible.has(e.s) && visible.has(e.t);
  });
  buildLegend(); alpha = Math.max(alpha, .45); requestAnimationFrame(tick);
}
for (const k of ['colourBy','sizeBy','tagSel','minApplied','orphOnly','labels','search',
                 'eLink','eSrc','eGap']) {
  ui[k].addEventListener('input', () => {
    $('minAppliedV').textContent = ui.minApplied.value; applyFilters();
  });
}

/* ---- legend reflects the CURRENT encoding, not a fixed key ---- */
function buildLegend() {
  const m = ui.colourBy.value, L = [];
  L.push('<div class="lbl">colour · ' + m + '</div>');
  if (m === 'age') L.push(row('#00d47e', 'reviewed recently') + row('#5a606c', '180+ days'));
  else if (m === 'applied') L.push(row('#3a4049', 'never applied') + row('#ffb020', `${maxApplied} days applied`));
  else {
    const src = m === 'tier' ? TIERC : m === 'topic' ? TOPICC : null;
    const vals = [...new Set([...visible].map(id => String(byId.get(id)[m] ?? '?')))].sort();
    const cm = src ? null : categoricalColour(m);
    for (const v of vals) L.push(row(src ? (src[v] || '#8a8f98') : cm.get(v), v));
  }
  L.push('<div class="lbl">shape</div>');
  L.push('<div class="row"><span class="sw" style="border:1.5px solid #7d8695"></span>orphan (hollow) — nothing links in</div>');
  L.push('<div class="lbl">size · ' + ui.sizeBy.value + '</div>');
  L.push('<div class="lbl">edges</div>');
  if (ui.eLink.checked) L.push('<div class="row"><span style="width:14px;height:1px;background:#39414d"></span>wikilink</div>');
  if (ui.eSrc.checked) L.push('<div class="row"><span style="width:14px;height:1px;background:#2f6b52"></span>source_ref</div>');
  if (ui.eGap.checked) L.push('<div class="row"><span style="width:14px;border-top:1px dashed #6b5a2f"></span>triadic gap — not yet drawn</div>');
  $('legend').innerHTML = L.join('');
}
const row = (c, t) => `<div class="row"><span class="sw" style="background:${c}"></span>${t}</div>`;

/* ---- force simulation. 243 nodes: brute-force repulsion is ~30k pairs, fine at 60fps ---- */
function step() {
  const nodes = N.filter(n => visible.has(n.id));
  const k = 0.045, rep = 900;
  /* Radius cached ONCE per step, not per pair: the collision test below is O(n^2) and
     sizeOf() reads the DOM every call. 650 nodes is 210k pairs a frame. */
  for (const n of nodes) n._r = sizeOf(n);
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y, d2 = dx*dx + dy*dy;
      if (d2 < 1e-4) { dx = (rnd()-.5); dy = (rnd()-.5); d2 = .01; }
      if (d2 > 90000) continue;                       // ignore far pairs: cheap cutoff
      const f = rep / d2, d = Math.sqrt(d2);
      const fx = dx / d * f, fy = dy / d * f;
      a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
      /* ⚠ REPULSION IS A POINT CHARGE — IT DOES NOT KNOW HOW BIG A NODE IS. rep/d2 pushes a
         30px hub exactly as hard as a 3px leaf, so the 72 procedural rules, which are the
         biggest AND the most connected nodes here, settled inside one another and rendered as
         a single green amoeba (observed 2026-09-22). Size = times-applied is the one signal
         this view has that Obsidian cannot show; nodes that merge destroy it.
         This is a POSITIONAL correction, not a force: a spring would be absorbed by the very
         degree-normalisation that holds the hubs central, and the overlap would survive. */
      const min = a._r + b._r + 3;
      if (d < min) {
        const push = (min - d) / d * .5;
        a.x -= dx * push; a.y -= dy * push; b.x += dx * push; b.y += dy * push;
      }
    }
    a.vx -= a.x * 0.004; a.vy -= a.y * 0.004;         // gravity: holds loose components in
  }
  for (const e of visEdges) {
    const a = byId.get(e.s), b = byId.get(e.t);
    const dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.max(1, Math.hypot(dx, dy));
    const rest = e.k === 'gap' ? 150 : 62;             // gaps sit looser: they aren't real yet
    /* ⚠ DIVIDE THE SPRING BY DEGREE, OR THE HUBS TEAR THE LAYOUT APART. Each edge added one
       impulse to its endpoints, so a node of degree 50 took 50 of them in a single step: at
       k=0.045 that is a push ~2.25x its own displacement, which damping of .82 cannot absorb.
       The result was not a bad layout, it was NUMERICAL DIVERGENCE — measured 2026-09-22, the
       first node sat at x=1.5e58 and every node was drawn astronomically off-screen. The page
       rendered its header, controls and legend perfectly and the canvas was simply empty,
       which is why this looked for three guesses like a sizing or filtering bug.
       Normalising by sqrt(degree) is the standard fix and keeps hubs central. */
    const f = (d - rest) * k * (e.k === 'gap' ? .18 : 1) / d;
    const fa = f / Math.sqrt(a.deg || 1), fb = f / Math.sqrt(b.deg || 1);
    a.vx += dx * fa; a.vy += dy * fa; b.vx -= dx * fb; b.vy -= dy * fb;
  }
  for (const n of nodes) {
    if (n.fixed) { n.vx = n.vy = 0; continue; }
    n.vx *= .82; n.vy *= .82;
    /* A hard speed limit as well as the degree fix: a belt for the braces. Any integrator that
       CAN diverge eventually will, and a graph that silently renders nothing is the worst
       failure mode available — it looks like an empty vault rather than a broken layout. */
    const sp = Math.hypot(n.vx, n.vy);
    if (sp > 60) { n.vx = n.vx / sp * 60; n.vy = n.vy / sp * 60; }
    n.x += n.vx * alpha; n.y += n.vy * alpha;
    if (!isFinite(n.x) || !isFinite(n.y)) {            // never let a NaN persist into the draw
      n.x = (rnd() - .5) * 400; n.y = (rnd() - .5) * 400; n.vx = n.vy = 0;
    }
  }
  alpha *= .992;
}

/* ---- render ---- */
function resize() {
  const r = cv.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  cv.width = r.width * dpr; cv.height = r.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); W = r.width; H = r.height;
}
let W = 0, H = 0;
const tx = n => (n.x + view.x) * view.k + W / 2;
const ty = n => (n.y + view.y) * view.k + H / 2;

function draw() {
  ctx.clearRect(0, 0, W, H);
  const focus = sel || hover;
  const near = focus ? new Set([focus.id,
    ...adj.get(focus.id).out, ...adj.get(focus.id).in, ...adj.get(focus.id).src,
    ...adj.get(focus.id).gap.map(g => g.o)]) : null;

  for (const e of visEdges) {
    const a = byId.get(e.s), b = byId.get(e.t);
    const lit = focus && (e.s === focus.id || e.t === focus.id);
    const dim = focus && !lit;
    ctx.beginPath();
    ctx.setLineDash(e.k === 'gap' ? [4, 4] : []);
    ctx.strokeStyle = e.k === 'gap' ? (lit ? '#d9a441' : dim ? '#2a2620' : '#6b5a2f')
                    : e.k === 'src' ? (lit ? '#4fd39a' : dim ? '#1e2a25' : '#2f6b52')
                    : (lit ? '#7fb8ff' : dim ? '#1b1f27' : '#39414d');
    ctx.lineWidth = lit ? 1.6 : 1;
    ctx.moveTo(tx(a), ty(a)); ctx.lineTo(tx(b), ty(b)); ctx.stroke();
  }
  ctx.setLineDash([]);

  const showAll = ui.labels.checked, cands = [];
  for (const id of visible) {
    const n = byId.get(id), x = tx(n), y = ty(n), r = Math.max(2, sizeOf(n) * Math.sqrt(view.k));
    if (x < -60 || y < -30 || x > W + 60 || y > H + 30) continue;   // offscreen: skip
    const dim = focus && !near.has(n.id);
    ctx.globalAlpha = dim ? .18 : 1;
    ctx.beginPath(); ctx.arc(x, y, r, 0, 6.284);
    if (n.orphan) { ctx.strokeStyle = colourOf(n); ctx.lineWidth = 1.6; ctx.stroke(); }
    else {
      ctx.fillStyle = colourOf(n); ctx.fill();
      /* A rim IN THE GROUND COLOUR. Collision keeps centres apart; this keeps the edges
         legible in the one case collision cannot fix — a dense same-tier cluster, where
         flat discs of one colour fuse into a blob no matter how little they overlap. */
      if (r > 2.6) { ctx.strokeStyle = BG; ctx.lineWidth = Math.min(2, .9 + r * .06); ctx.stroke(); }
    }
    if (n.match) { ctx.strokeStyle = '#fff'; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, r + 3.5, 0, 6.284); ctx.stroke(); }
    if (sel === n) { ctx.strokeStyle = '#fff'; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, r + 4, 0, 6.284); ctx.stroke(); }
    ctx.globalAlpha = 1;
    const want = showAll || n.match || focus === n ||
      (focus && near.has(n.id)) || (!focus && (n.applied >= 3 || r > 9));
    if (want && view.k > .45) cands.push({n, x, y, r, dim, pri: focus === n ? 1e6 : r});
  }
  /* Labels collide into an unreadable smear at the core otherwise. Draw biggest-first and
     drop any that would overlap one already placed — so the densest area still shows its
     most-applied pages instead of a grey mush. "all labels" opts out of the suppression. */
  const placed = [];
  cands.sort((a, b) => b.pri - a.pri);
  for (const c of cands) {
    const txt = c.n.id.length > 42 ? c.n.id.slice(0, 40) + '…' : c.n.id;
    const f = Math.min(13, 10 + view.k);
    ctx.font = `${focus === c.n ? 600 : 400} ${f}px ui-sans-serif,sans-serif`;
    const w = ctx.measureText(txt).width, lx = c.x + c.r + 4, ly = c.y + 3.5;
    const box = [lx, ly - f, lx + w, ly + 3];
    if (!showAll && placed.some(p => !(box[2] < p[0] || box[0] > p[2] ||
                                       box[3] < p[1] || box[1] > p[3]))) continue;
    placed.push(box);
    ctx.globalAlpha = c.dim ? .25 : 1;
    ctx.fillStyle = focus === c.n ? '#fff' : '#95a0b0';
    ctx.fillText(txt, lx, ly);
    ctx.globalAlpha = 1;
  }
}

/* Fit the settled layout to the viewport: without it the graph opens off-centre with
   loose components wasting half the canvas. */
function fit() {
  const ns = [...visible].map(id => byId.get(id));
  if (!ns.length) return;
  /* Percentile box, not min/max: two loose orphans flung to the edge would otherwise
     dictate the zoom and shrink the actual cluster to a third of the canvas. */
  const q = (arr, p) => { const s = [...arr].sort((a, b) => a - b);
    return s[Math.max(0, Math.min(s.length - 1, Math.round(p * (s.length - 1))))]; };
  const xs = ns.map(n => n.x), ys = ns.map(n => n.y);
  const x0 = q(xs, .02), x1 = q(xs, .98), y0 = q(ys, .02), y1 = q(ys, .98);
  const pad = 60;
  view.k = Math.max(.12, Math.min(2.2,
    Math.min((W - pad * 2) / Math.max(1, x1 - x0), (H - pad * 2) / Math.max(1, y1 - y0))));
  view.x = -(x0 + x1) / 2; view.y = -(y0 + y1) / 2;
}
function tick() { step(); draw(); if (alpha > .003) requestAnimationFrame(tick); }

/* ---- interaction ---- */
function pick(mx, my) {
  let best = null, bd = 1e9;
  for (const id of visible) {
    const n = byId.get(id), d = Math.hypot(tx(n) - mx, ty(n) - my);
    const r = Math.max(6, sizeOf(n) * Math.sqrt(view.k) + 4);
    if (d < r && d < bd) { bd = d; best = n; }
  }
  return best;
}
cv.addEventListener('mousemove', ev => {
  const r = cv.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top;
  if (drag) { drag.x = (mx - W/2) / view.k - view.x; drag.y = (my - H/2) / view.k - view.y;
    drag.fixed = true; alpha = Math.max(alpha, .3); requestAnimationFrame(tick); return; }
  if (panning) { view.x += (mx - panning.x) / view.k; view.y += (my - panning.y) / view.k;
    panning = {x:mx, y:my}; requestAnimationFrame(draw); return; }
  const n = pick(mx, my); const tip = $('tip');
  if (n !== hover) { hover = n; requestAnimationFrame(draw); }
  if (n) {
    tip.style.display = 'block'; tip.style.left = (mx + 14) + 'px';
    tip.style.top = (my + 12) + 'px';
    tip.innerHTML = `<b>${n.id}</b><br><span>${n.tier} · applied ${n.applied}× · ` +
      `in ${n.inb} / out ${n.outb}${n.orphan ? ' · ORPHAN' : ''}</span>` +
      (n.hook ? `<br>${n.hook.slice(0, 130)}` : '');
  } else tip.style.display = 'none';
});
cv.addEventListener('mousedown', ev => {
  const r = cv.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top;
  const n = pick(mx, my);
  if (n) drag = n; else { panning = {x:mx, y:my}; cv.classList.add('drag'); }
});
addEventListener('mouseup', () => { drag = null; panning = null; cv.classList.remove('drag'); });
cv.addEventListener('click', ev => {
  const r = cv.getBoundingClientRect();
  const n = pick(ev.clientX - r.left, ev.clientY - r.top);
  if (n) showPanel(n); else { sel = null; $('panel').classList.remove('open');
    requestAnimationFrame(draw); }
});
cv.addEventListener('wheel', ev => {
  ev.preventDefault();
  const r = cv.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top;
  const wx = (mx - W/2) / view.k - view.x, wy = (my - H/2) / view.k - view.y;
  view.k = Math.max(.12, Math.min(6, view.k * (ev.deltaY < 0 ? 1.13 : 1/1.13)));
  view.x = (mx - W/2) / view.k - wx; view.y = (my - H/2) / view.k - wy;
  requestAnimationFrame(draw);
}, {passive:false});
$('reset').onclick = () => {
  for (const n of N) n.fixed = false;                 // release anything pinned by dragging
  alpha = .8; for (let i = 0; i < 160; i++) step();
  fit(); requestAnimationFrame(tick);
};
$('close').onclick = () => { sel = null; $('panel').classList.remove('open');
  requestAnimationFrame(draw); };

function showPanel(n) {
  sel = n;
  const a = adj.get(n.id);
  const link = id => `<a class="lnk" onclick="jump('${id.replace(/'/g, "\\'")}')">${id}</a>`;
  let h = `<h2>${n.id}</h2>`;
  if (n.hook) {
    // Index hooks run to 1400+ chars for the keystone pages, which buries the metadata
    // and the applied-log below the fold. Show the first sentence-ish, click to expand.
    const CUT = 300;
    h += n.hook.length > CUT
      ? `<p class="hook" onclick="this.classList.toggle('open')">${n.hook.slice(0, CUT)}` +
        `<span class="rest">${n.hook.slice(CUT)}</span><span class="ell"> … more</span></p>`
      : `<p class="hook">${n.hook}</p>`;
  }
  h += `<dl class="kv">
    <dt>tier</dt><dd>${n.tier}</dd>
    <dt>topic</dt><dd>${n.topic}</dd>
    <dt>applied</dt><dd>${n.applied}×</dd>
    <dt>links</dt><dd>${n.inb} in · ${n.outb} out</dd>
    <dt>status</dt><dd>${n.status} · ${n.conf} confidence</dd>
    <dt>type</dt><dd>${n.ptype}</dd>
    <dt>reviewed</dt><dd>${n.reviewed || '—'}${n.age != null ? ` (${n.age}d ago)` : ''}</dd>
    <dt>file</dt><dd>${n.path}</dd></dl>`;
  if (n.tags.length) h += `<div class="sec"><h3>tags</h3>` +
    n.tags.map(t => `<span class="pill">${t}</span>`).join('') + `</div>`;
  if (n.aps.length) h += `<div class="sec"><h3>what it has driven (${n.aps.length})</h3>` +
    n.aps.map(x => `<div class="ap"><b>${x.date}</b><br>${x.outcome || '—'}</div>`).join('') + `</div>`;
  if (a.out.length) h += `<div class="sec"><h3>links out (${a.out.length})</h3>` +
    a.out.sort().map(link).join('') + `</div>`;
  if (a.in.length) h += `<div class="sec"><h3>linked from (${a.in.length})</h3>` +
    a.in.sort().map(link).join('') + `</div>`;
  if (a.src.length) h += `<div class="sec"><h3>source_ref (${a.src.length})</h3>` +
    a.src.sort().map(link).join('') + `</div>`;
  if (a.gap.length) h += `<div class="sec"><h3>triadic gaps (${a.gap.length})</h3>` +
    a.gap.map(g => `${link(g.o)}<span style="color:#7d8695;font-size:11px">` +
      `&nbsp;&nbsp;via ${g.via.slice(0,3).join(', ')}</span>`).join('') + `</div>`;
  if (n.extrefs.length) h += `<div class="sec"><h3>external refs (${n.extrefs.length})</h3>` +
    `<div style="color:#7d8695;font-size:11px;margin-bottom:5px">raw sources and KB papers — ` +
    `the bridge edges, not yet drawn</div>` +
    n.extrefs.map(r => `<span class="pill">${r}</span>`).join('') + `</div>`;
  $('pbody').innerHTML = h; $('panel').classList.add('open');
  requestAnimationFrame(draw);
}
window.jump = id => {
  const n = byId.get(id); if (!n) return;
  if (!visible.has(id)) { ui.tagSel.value = ''; ui.minApplied.value = 0;
    ui.orphOnly.checked = false; applyFilters(); }
  view.x = -n.x; view.y = -n.y; showPanel(n);
};
addEventListener('resize', () => { resize(); requestAnimationFrame(draw); });
resize(); applyFilters();
for (let i = 0; i < 320; i++) step();      // pre-settle so it opens as a map, not a hairball
fit();
requestAnimationFrame(tick);
</script></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="vault-graph.html", help="the HTML page to write (default: ./vault-graph.html)")
    ap.add_argument("--vault", help="the notes folder (default: MEMTOOLS_VAULT or the current folder)")
    ap.add_argument("--gaps", type=int, default=60,
                    help="how many triadic gaps to draw (ranked by bridge count)")
    ap.add_argument("--stats", action="store_true", help="print findings, render nothing")
    ap.add_argument("--layout", metavar="JSON", nargs="?", const="vault-layout.json",
        help="also solve the layout of the ever-applied subgraph and write it as JSON")
    args = ap.parse_args()

    data = collect(args.gaps)
    s = data["stats"]
    print(f"pages {s['pages']} · links {s['links']} · source_ref edges {s['srcs']} · "
          f"gaps {s['gaps']} · orphans {s['orphans']} · dead-ends {s['deadends']}")
    print(f"applied: {s['applied']} distinct days ({s['cites']} log citations) "
          f"across {s['everApplied']} pages "
          f"({s['pages'] - s['everApplied']} never applied)")
    if args.stats:
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data))
    print(f"→ {out}  ({out.stat().st_size / 1024:.0f} KB, self-contained)")

    if args.layout:
        t0 = time.time()
        lay = solve_layout(data)
        lp = Path(args.layout)
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(json.dumps(lay, separators=(",", ":")))
        print(f"→ {lp}  ({len(lay['nodes'])} applied pages, {len(lay['edges'])} edges, "
              f"solved in {time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
