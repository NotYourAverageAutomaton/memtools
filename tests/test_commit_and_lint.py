#!/usr/bin/env python3
"""Tests for vault-commit.sh (in a temp git repo with a local bare remote) and vault-lint.py.

usage: python3 tests/test_commit_and_lint.py      expect: N passed, 0 failed   (needs git and flock)
"""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  — {detail[:200]}" if detail and not cond else ""))

tmp = tempfile.mkdtemp(); H = os.path.join(tmp, "home"); os.makedirs(H)
ENV = {"PATH": "/usr/bin:/bin", "HOME": H, "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1"}
def sh(cmd, cwd, env=None, ok=True):
    r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, env={**ENV, **(env or {})}, timeout=60)
    if ok and r.returncode: raise SystemExit(f"setup failed: {cmd}\n{r.stderr}")
    return r
R = os.path.join(tmp, "notes"); B = os.path.join(tmp, "remote.git")
sh(f"git init -q --bare -b main {B}", tmp)
sh(f"git init -q -b main {R}", tmp)
sh("git config user.email owner@example.com && git config user.name Owner", R)
open(os.path.join(R, "a.md"), "w").write("# A\n")
sh(f"git add a.md && git commit -q -m init && git remote add origin {B} && git push -q origin main", R)
C = os.path.join(TOOLS, "vault-commit.sh")

r = sh(f"bash {C} -m x a.md", R, ok=False)
check("commit: refuses with no agent name (exit 2)", r.returncode == 2 and "MEMTOOLS_AGENT" in r.stderr, r.stderr)

open(os.path.join(R, "new.md"), "w").write("# New\n")
open(os.path.join(R, "theirs.md"), "w").write("# Someone else's half-done work\n")
sh("git add theirs.md", R)
r = sh(f"bash {C} -m 'add new note' new.md", R, env={"MEMTOOLS_AGENT": "agent-one"}, ok=False)
log = sh("git log -1 --format='%an|%s' && git show --name-only --format= HEAD", R).stdout
check("commit: a NEW file is committed (untracked files are staged)", r.returncode == 0 and "new.md" in log, r.stderr + log)
check("commit: the author is the agent", "agent-one|add new note" in log, log)
check("commit: another agent's staged file is NOT swept into the commit", "theirs.md" not in log, log)
st = sh("git status --porcelain", R).stdout
check("commit: and it is still staged for its owner", "A  theirs.md" in st, st)

r = sh(f"bash {C} -m again new.md", R, env={"MEMTOOLS_AGENT": "agent-one"}, ok=False)
check("commit: nothing changed -> exit 3, says NOTHING WAS COMMITTED", r.returncode == 3 and "NOTHING WAS COMMITTED" in r.stderr, r.stderr)

os.remove(os.path.join(R, "a.md"))
r = sh(f"bash {C} -m 'remove a' a.md", R, env={"MEMTOOLS_AGENT": "agent-one"}, ok=False)
files = sh("git ls-tree --name-only HEAD", R).stdout
check("commit: a deletion is committed", r.returncode == 0 and "a.md" not in files, files)

W = os.path.join(tmp, "other"); sh(f"git clone -q {B} {W} && cd {W} && git config user.email o@example.com && git config user.name O", tmp)
open(os.path.join(W, "remote-side.md"), "w").write("# pushed by someone else\n")
sh("git add remote-side.md && git commit -q -m remote && git push -q origin main", W)
open(os.path.join(R, "mine.md"), "w").write("# mine\n")
r = sh(f"bash {C} -m 'mine' --push mine.md", R, env={"MEMTOOLS_AGENT": "agent-one"}, ok=False)
remote_log = sh(f"git --git-dir={B} log --format=%s main", tmp).stdout
check("commit --push: rebases over a push made in between, then pushes", r.returncode == 0 and "mine" in remote_log and "remote" in remote_log, r.stderr + remote_log)

V = os.path.join(tmp, "lintnotes"); os.makedirs(os.path.join(V, "projects"))
open(os.path.join(V, "x.md"), "w").write("# X\nSee [[y]] and [[missing]].\nAlso [[split\nlink]].\n")
open(os.path.join(V, "y.md"), "w").write("---\nstatus: weird\n---\n# Y\n")
open(os.path.join(V, "projects/p.md"), "w").write("---\nstatus: active\nlast_reviewed: 2020-01-01\n---\n# P\n")
def lint(*args):
    r = subprocess.run([sys.executable, "-B", os.path.join(TOOLS, "vault-lint.py"), "--vault", V, *args],
                       capture_output=True, text=True, env=ENV, cwd=H, timeout=60)
    return r.returncode, r.stdout + r.stderr
rc, out = lint()
check("lint: a broken link is an issue", rc == 1 and "BROKEN-LINKS" in out and "missing" in out, out)
check("lint: a link split across a line break is an issue", "WRAPPED-LINK" in out, out)
check("lint: an unknown status is an issue", "BAD-STATUS" in out and "weird" in out, out)
check("lint: an active page not reviewed in 90 days is STALE", "STALE" in out and "p.md" in out, out)
check("lint: no required fields by default (a stranger's notes are not failed for our schema)", "MISSING-FM" not in out and "MISSING-FIELDS" not in out, out)
open(os.path.join(V, "memtools.toml"), "w").write('[lint]\nrequired_fields = ["status"]\narc_fields = ["next_action"]\n')
rc, out = lint()
check("lint: [lint] required_fields applies when set", "MISSING-FM" in out and "x.md" in out, out)
check("lint: [lint] arc_fields applies to active work", "ARC-NO-FIELD" in out and "next_action" in out, out)
rp = os.path.join(tmp, "report.md")
rc, out = lint("--report", rp)
check("lint --report: writes the issues to the file given", os.path.exists(rp) and "BROKEN-LINKS" in open(rp).read(), out)
check("nothing written to HOME", os.listdir(H) == [], str(os.listdir(H)))
print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
