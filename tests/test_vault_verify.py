#!/usr/bin/env python3
"""Controls for vault-verify.py: the checker gets checked. Linux (uses ss). Nothing leaves the machine.

usage: python3 tests/test_vault_verify.py      expect: N passed, 0 failed
"""
import importlib.util, os, socket, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
sys.path.insert(0, TOOLS)
spec = importlib.util.spec_from_file_location("vv", os.path.join(TOOLS, "vault-verify.py"))
vv = importlib.util.module_from_spec(spec); spec.loader.exec_module(vv)

MIN_CONTROLS = 38   # raise it when you add controls; a lower count means controls were removed
PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  — {detail}" if detail and not cond else ""))

# 1. An unreachable host is UNKNOWN for every kind: never a pass, never a fail.
vv.HOSTS["__unreachable__"] = "nobody@no-such-host.invalid"
SAMPLE_ARG = {
    "git_tracked": "~/x::y", "git_push_blocked": "~/x::origin", "git_unpushed": "~/x::owner",
    "gh_private": "owner/repo", "gh_owner_private": "owner::2026-01-01::",
    "grep_in": "~/x::pat", "grep_absent": "~/x::pat",
    "cron_present": "somejob", "scheduled_present": "somejob",
    "sha256": "~/x == " + "0" * 64,
}
for kind, fn in sorted(vv.KINDS.items()):
    st, msg = fn(SAMPLE_ARG.get(kind, "~/definitely/not/here"), "__unreachable__")
    check(f"{kind}: unreachable host -> UNKNOWN", st == "UNKNOWN", f"got {st}: {msg[:60]}")

check("an unregistered kind is not silently accepted", "no_such_kind" not in vv.KINDS)

# 2. A malformed claim is an authoring error (UNKNOWN), not a failure in the world.
for kind, bad in (("git_tracked", "no-separator"), ("git_push_blocked", "no-separator"),
                  ("bind_not_wildcard", "not-a-port"), ("cmd_absent", ""), ("sha256", "no-separator")):
    st, _ = vv.KINDS[kind](bad, "")
    check(f"{kind}: malformed arg -> UNKNOWN", st == "UNKNOWN", f"got {st}")

# 3. Both answers are reachable (the suite is not vacuous).
st, _ = vv.KINDS["cmd_absent"]("definitely_not_a_real_command_xyz", "")
check("cmd_absent: an absent command PASSES", st == "PASS")
st, _ = vv.KINDS["cmd_absent"]("sh", "")
check("cmd_absent: a present command FAILS", st == "FAIL", f"got {st}")
tmp = tempfile.mkdtemp()
there = os.path.join(tmp, "here.txt"); open(there, "w").write("needle\n")
st, _ = vv.KINDS["file_exists"](there, "")
check("file_exists: a file that is there PASSES", st == "PASS", f"got {st}")
st, _ = vv.KINDS["file_exists"](os.path.join(tmp, "not-here.txt"), "")
check("file_exists: a file that is not there FAILS", st == "FAIL", f"got {st}")
st, _ = vv.KINDS["grep_in"](f"{there}::needle", "")
check("grep_in: a pattern that is there PASSES", st == "PASS", f"got {st}")
st, _ = vv.KINDS["grep_absent"](f"{there}::needle", "")
check("grep_absent: a pattern that is there FAILS", st == "FAIL", f"got {st}")
st, _ = vv.KINDS["grep_absent"](os.path.join(tmp, "nofile")+"::needle", "")
check("grep_absent: a missing file is UNKNOWN (absence proves nothing)", st == "UNKNOWN", f"got {st}")

# 4. Binds: the listen address decides, measured with sockets this test opens itself.
def bound(kind, addr):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM if kind == "tcp" else socket.SOCK_DGRAM)
    s.bind((addr, 0))
    if kind == "tcp": s.listen(1)
    return s, s.getsockname()[1]
s1, p1 = bound("tcp", "0.0.0.0")
st, msg = vv.KINDS["bind_not_wildcard"](str(p1), "")
check("bind_not_wildcard: a TCP port on all interfaces FAILS", st == "FAIL", f"got {st}: {msg[:60]}")
s2, p2 = bound("udp", "0.0.0.0")
st, msg = vv.KINDS["bind_not_wildcard"](str(p2), "")
check("bind_not_wildcard: sees UDP too", st == "FAIL" and "udp" in msg, f"got {st}: {msg[:60]}")
s3, p3 = bound("tcp", "127.0.0.1")
st, msg = vv.KINDS["bind_not_wildcard"](str(p3), "")
check("bind_not_wildcard: a loopback-only port PASSES", st == "PASS", f"got {st}: {msg[:60]}")
for s in (s1, s2, s3): s.close()
st, msg = vv.KINDS["bind_not_wildcard"](str(p3), "")
check("bind_not_wildcard: an unused port passes WITH a caveat",
      st == "PASS" and "NOT a verified bind" in msg, f"got {st}: {msg[:50]}")

# 5. A claim cannot run a command: arguments are quoted.
marker = os.path.join(tmp, "INJECTED")
for kind, arg in (("file_exists", f"/nope; touch {marker}"), ("grep_in", f"/nope::x' ; touch {marker} ; '"),
                  ("cmd_absent", f"x; touch {marker}"), ("sha256", f"/nope; touch {marker} == " + "0" * 64)):
    vv.KINDS[kind](arg, "")
    check(f"{kind}: shell text in a claim is not executed", not os.path.exists(marker))

total = len(PASS) + len(FAIL)
if total < MIN_CONTROLS:
    FAIL.append("count"); print(f"  FAIL only {total} controls ran; MIN_CONTROLS is {MIN_CONTROLS}")
print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
