#!/usr/bin/env python3
"""Controls for the six fixes of v1.4.1, from an outside review of v1.4. Linux. Nothing leaves the machine; every arm works in a temp folder.

usage: python3 tests/test_review_fixes.py [tools dir]          expect: 6 passed, 0 failed (arm 4 holds four checks)
       python3 tests/test_review_fixes.py <v1.4 tools> --control  expect: every arm FAILS (proves each arm can tell old from new)
"""
import importlib.util, os, subprocess, sys, tempfile, textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
CONTROL = "--control" in sys.argv
TOOLS = os.path.abspath(args[0]) if args else os.path.join(os.path.dirname(HERE), "tools")
PASS, FAIL = [], []

def check(name, fn):
    try:
        ok, why = fn()
    except Exception as e:
        ok, why = False, "%s: %s" % (type(e).__name__, e)
    (PASS if ok else FAIL).append(name)
    print("  %s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  - " + why[:160]))

def load(tool, vault):
    """Import a tool as a module with its notes folder set to `vault`, from a different working folder."""
    os.environ["MEMTOOLS_VAULT"] = vault
    sys.path.insert(0, TOOLS)
    spec = importlib.util.spec_from_file_location(tool.replace("-", "_") + str(id(vault)), os.path.join(TOOLS, tool + ".py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m

def run(tool, vault, *a):
    env = dict(os.environ, MEMTOOLS_VAULT=vault, HOME=vault, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, os.path.join(TOOLS, tool + ".py"), *a, "--vault", vault], capture_output=True, text=True, env=env, cwd="/")

def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True); open(path, "w").write(textwrap.dedent(text).lstrip("\n"))

# 1. git_unpushed: a malformed dirty exemption must be UNKNOWN (it was "ERROR", which no tally counted, so the run said 0 problems)
def a1():
    v = tempfile.mkdtemp(); vv = load("vault-verify", v)
    st, _ = vv._git_unpushed(v + "::owner::::repo=bad*glob", "")
    return st == "UNKNOWN", "got %r" % st
check("verify git_unpushed: a malformed dirty exemption is UNKNOWN, not an uncounted ERROR", a1)

# 2. grep_absent and git_tracked anchor a relative path to the notes folder, run from another folder
def a2():
    v = tempfile.mkdtemp(); w(os.path.join(v, "notes", "x.md"), "needle\n")
    r = os.path.join(v, "repo"); os.makedirs(r); open(os.path.join(r, "f.txt"), "w").write("x\n")
    g = lambda *c: subprocess.run(["git", "-C", r, *c], capture_output=True, text=True, env=dict(os.environ, HOME=v))
    g("init", "-q"); g("add", "f.txt"); g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "t")
    os.chdir("/"); vv = load("vault-verify", v)
    s1, m1 = vv._grep_absent("notes/x.md::needle", "")
    s2, m2 = vv._git_tracked("repo::f.txt", "")
    return (s1, s2) == ("FAIL", "PASS"), "grep_absent %s (%s) / git_tracked %s (%s)" % (s1, m1[:50], s2, m2[:50])
check("verify grep_absent and git_tracked: a relative path is anchored to the notes folder", a2)

# 3. the applied-log reads [[page|alias]] and [[page#heading]] as page, in recall, index and applied
def a3():
    v = tempfile.mkdtemp(); out = []
    for tool in ("vault-recall", "vault-index", "vault-applied"):
        m = load(tool, v).APPLIED
        for line in ("## [2026-11-13] applied | [[page|alias]] -> x", "## [2026-11-13] applied | [[page#part]] -> x"):
            got = m.findall(line) if hasattr(m, "findall") else []
            out.append((tool, got[0][1] if got else None))
    return all(p == "page" for _, p in out), str(out)
check("recall, index, applied: [[page|alias]] and [[page#heading]] count as page", a3)

# 4. vault-close keeps a trailing "-" of the last frontmatter field, and its --check does not read another page's row as its own
def a4():
    v = tempfile.mkdtemp()
    w(os.path.join(v, "projects", "x.md"), """
        ---
        status: active
        last_reviewed: 2026-11-01
        note: keep the dashes --
        ---
        body
        """)
    r = run("vault-close", v, "x", "--why", "done", "--apply")
    t = open(os.path.join(v, "projects", "x.md")).read()
    ok1 = "note: keep the dashes --" in t and "status: closed" in t
    w(os.path.join(v, "projects", "y.md"), """
        ---
        status: active
        verify:
          - file_exists: projects/y.md
        ---
        """)
    w(os.path.join(v, "projects", "old-y.md"), """
        ---
        status: active
        verify:
          - file_exists: projects/definitely-not-here.md
        ---
        """)
    r2 = run("vault-close", v, "y", "--why", "done", "--check")
    ok2 = r2.returncode == 0 and "old-y.md" not in r2.stdout
    w(os.path.join(v, "projects", "y.md"), """
        ---
        status: active
        verify:
          - file_exists: projects/gone.md
        ---
        """)
    r3 = run("vault-close", v, "y", "--why", "done", "--check")
    ok3 = r3.returncode != 0 and "did not all pass" in r3.stderr
    # a refusal (home_host set to a host this is not) prints no page rows: that must not read as a pass
    w(os.path.join(v, "projects", "y.md"), """
        ---
        status: active
        verify:
          - file_exists: projects/y.md
        ---
        """)
    open(os.path.join(v, "memtools.toml"), "w").write('[verify]\nhome_host = "no-such-host-for-this-test"\n')
    r4 = run("vault-close", v, "y", "--why", "done", "--check")
    ok4 = r4.returncode != 0 and "nothing was checked" in r4.stderr
    return ok1 and ok2 and ok3 and ok4, "dashes kept=%s; another page not read as its own=%s; own failing claim refused=%s; a refused verify is not a pass=%s" % (ok1, ok2, ok3, ok4)
check("vault-close: trailing dashes kept; --check reads only its own page, and refuses its own failing claim (found writing this control)", a4)

# 5. vault-park --unpark clears the PARKED next_action it wrote
def a5():
    v = tempfile.mkdtemp()
    w(os.path.join(v, "projects", "z.md"), """
        ---
        status: active
        next_action: do the thing
        ---
        body
        """)
    run("vault-park", v, "z", "--until", "the part arrives", "--apply"); run("vault-park", v, "z", "--unpark", "--apply")
    t = open(os.path.join(v, "projects", "z.md")).read()
    return "PARKED" not in t.split("\n---", 1)[0] and "status: active" in t, t[:200]
check("vault-park --unpark: the PARKED next_action is gone", a5)

# 6. a QUOTED claim keeps " #" inside it; an unquoted one still loses a trailing " # comment"
def a6():
    v = tempfile.mkdtemp()
    p = os.path.join(v, "p.md")
    w(p, """
        ---
        verify:
          - "grep_in: notes/x.md::a #b"
          - grep_in: notes/x.md::c # a comment
        ---
        """)
    vv = load("vault-verify", v); from pathlib import Path
    args = [c[2] for c in vv.parse_claims(Path(p))]
    return args == ["notes/x.md::a #b", "notes/x.md::c"], str(args)
check("verify parse_claims: a quoted claim keeps ' #'; an unquoted trailing comment is still dropped", a6)

print("\n  %d passed, %d failed" % (len(PASS), len(FAIL)))
if CONTROL:
    print("  CONTROL run: every arm must FAIL on the old tools ->", "OK" if not PASS else "NOT OK: passed on old code: %s" % PASS)
    sys.exit(0 if not PASS else 1)
sys.exit(1 if FAIL else 0)
