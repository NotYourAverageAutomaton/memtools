#!/usr/bin/env python3
"""Runs every tool on a small made-up notes folder in a temp directory and checks what each one says.

usage: python3 tests/test_tools_smoke.py      expect: N passed, 0 failed
Nothing outside the temp folder is read or written (HOME points into it too).
"""
import os, subprocess, sys, tempfile, textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  — {detail[:200]}" if detail and not cond else ""))

tmp = tempfile.mkdtemp(); V = os.path.join(tmp, "notes"); H = os.path.join(tmp, "home")
os.makedirs(os.path.join(V, "projects")); os.makedirs(os.path.join(V, "rules")); os.makedirs(H)
def w(rel, text):
    p = os.path.join(V, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(textwrap.dedent(text).lstrip())
w("a.md", "# Alpha\nThe sourdough starter needs feeding daily. See [[b]].\n")
w("b.md", "# Beta\nProof the dough four hours. See [[c]].\n")
w("c.md", "# Gamma\nBake at high heat.\n")
w("lonely.md", "# Lonely\nNothing links here.\n")
w("rules/feed-first.md", "# Feed first\nFeed the starter before anything else in the morning.\n")
w("projects/oven.md", "---\nstatus: active\nlast_reviewed: 2026-01-01\n---\n# Oven repair\nWaiting on a part.\n")
w("projects/garden.md", "---\nstatus: active\nlast_reviewed: 2026-01-01\n---\n# Garden\nPlant in May.\n")
w("q.md", "---\nclaim_type: open_question\ncreated: 2026-01-01\n---\n# Does rye proof faster?\n")
w("log.md", "# Log\n## [2026-11-01] applied | [[feed-first]] -> fed on time\n## [2026-11-02] applied | [[feed-first]] -> again\n")
w("index.md", "# My index\n\nHand-written top. Keep me.\n")
w("memtools.toml", '[layout]\nrules_dir = "rules"\narcs_dir = "projects"\n[layout.tiers]\nrules = 1.6\n')

def run(tool, *args):
    env = {"PATH": "/usr/bin:/bin", "HOME": H, "TMPDIR": tmp, "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([sys.executable, "-B", os.path.join(TOOLS, tool), *args, "--vault", V],
                       capture_output=True, text=True, env=env, cwd=H, timeout=60)
    return r.returncode, r.stdout + r.stderr

rc, out = run("vault-recall.py", "starter feeding")
check("recall: finds the note about feeding the starter", rc == 0 and ("feed-first" in out or "Alpha" in out), out)
rc, out = run("vault-recall.py", "--arcs")
check("recall --arcs: lists the active work from the configured folder", rc == 0 and "oven" in out and "garden" in out, out)

rc, out = run("vault-applied.py")
check("applied: a note applied on 2 days is a promotion candidate", rc == 0 and "feed-first" in out.split("PROMOTION")[1].split("PRUNING")[0], out)

rc, out = run("vault-discover.py")
check("discover: proposes the missing link a -> c (a->b, b->c)", rc == 0 and "**[[a]]** ⟷ **[[c]]** — via [[b]]" in out, out)
check("discover: names the orphan", "lonely" in out, out)
rc, out = run("vault-discover.py", "--write")
check("discover --write: writes discoveries.md in the notes folder", os.path.exists(os.path.join(V, "discoveries.md")), out)

rc, out = run("vault-index.py")
check("index: dry run writes nothing", rc == 0 and "Hand-written top" in open(os.path.join(V, "index.md")).read() and "Map" not in open(os.path.join(V, "index.md")).read(), out)
rc, out = run("vault-index.py", "--apply")
idx = open(os.path.join(V, "index.md")).read()
check("index --apply: keeps the hand-written top", idx.startswith("# My index") and "Keep me." in idx, idx[:200])
check("index --apply: lists the rules and the active work", "[[feed-first]]" in idx and "[[oven]]" in idx, idx)
w("memtools.toml", '[layout]\nrules_dir = "rules"\n[index]\nmax_lines = 5\n')
before = open(os.path.join(V, "index.md")).read()
rc, out = run("vault-index.py", "--apply")
check("index: refuses to write past max_lines", rc == 1 and open(os.path.join(V, "index.md")).read() == before, out)
w("memtools.toml", '[layout]\nrules_dir = "rules"\narcs_dir = "projects"\n')

rc, out = run("vault-open-questions.py")
check("open-questions: an old question with no outcome is due", rc == 1 and "q.md" in out, out)

rc, out = run("vault-close.py", "oven", "--why", "part arrived, repaired")
check("close: dry run changes nothing", "status: active" in open(os.path.join(V, "projects/oven.md")).read(), out)
rc, out = run("vault-close.py", "oven", "--why", "part arrived, repaired", "--apply")
t = open(os.path.join(V, "projects/oven.md")).read()
check("close --apply: status closed with the reason stamped", "status: closed" in t and "part arrived" in t, t)
check("close --apply: one log line", "close | [[oven]]" in open(os.path.join(V, "log.md")).read())

rc, out = run("vault-park.py", "garden")
check("park: refuses without a named condition", rc != 0 and "--until" in out, out)
outside = os.path.join(tmp, "outside.md"); open(outside, "w").write("---\nstatus: active\n---\n# Not a note\n")
rc, out = run("vault-park.py", "../../outside", "--until", "never", "--apply")
check("park: refuses a page name with a path in it (cannot write outside the notes folder)", rc != 0 and "status: active" in open(outside).read(), out)
rc, out = run("vault-park.py", "garden", "--until", "spring", "--apply")
t = open(os.path.join(V, "projects/garden.md")).read()
check("park --apply: parked with its condition", "until: spring" in t and "status: stale" in t, t)

rc, out = run("vault-graph.py", "--out", os.path.join(tmp, "g.html"))
g = open(os.path.join(tmp, "g.html")).read() if os.path.exists(os.path.join(tmp, "g.html")) else ""
check("graph: writes one self-contained page titled with the folder name", rc == 0 and "<title>notes" in g and "<script src" not in g, out)

rc, before_applied = run("vault-applied.py")
w("log.md", open(os.path.join(V, "log.md")).read() + "".join(f"filler line {i}\n" for i in range(30)))
rc, out = run("log-rotator.py", "--max-lines", "10", "--keep", "5")
check("log-rotator: dry run changes nothing", rc == 0 and not any(f.startswith("log_") for f in os.listdir(V)), out)
rc, out = run("log-rotator.py", "--max-lines", "10", "--keep", "5", "--apply")
arch = [f for f in os.listdir(V) if f.startswith("log_")]
check("log-rotator --apply: archives the log and keeps a short head", rc == 0 and len(arch) == 1 and len(open(os.path.join(V, "log.md")).read().splitlines()) < 12, out)
rc, after_applied = run("vault-applied.py")
check("log-rotator: applied history is unchanged after rotation (readers span the archives)",
      before_applied.split("PROMOTION")[1].split("PRUNING")[0] == after_applied.split("PROMOTION")[1].split("PRUNING")[0], after_applied)

stray = [f for f in os.listdir(H)]
check("nothing written to HOME", stray == [], str(stray))
print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
