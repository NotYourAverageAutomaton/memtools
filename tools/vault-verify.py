#!/usr/bin/env python3
"""vault-verify — check the machine-readable claims in your notes against the running system.

Each note may carry a `verify:` list in its frontmatter (see README). This runs every claim and reports
PASS, FAIL or UNKNOWN. An unknown kind or an unreachable host is UNKNOWN, never a pass.
Exit 0 = all hold, 1 = something FAILED, 2 = something could not be checked.
SECURITY: claims are checked with shell commands. Arguments are quoted, but only run this on notes you trust.
"""
import argparse
import os
import json
import shlex
import re
import socket
import subprocess
import sys
from pathlib import Path

from memtools_config import load_config, vault_root

VAULT = vault_root()
CONFIG = load_config(VAULT)
HOSTS = dict(CONFIG.get("hosts", {}))


def q(s: str) -> str:
    """Quote one argument for the shell; a leading ~/ or $HOME/ still means the home directory."""
    for pre in ("~/", "$HOME/"):
        if s.startswith(pre):
            return '"$HOME"/' + shlex.quote(s[len(pre):])
    if s in ("~", "$HOME"):
        return '"$HOME"'
    return shlex.quote(s)


def sh(cmd: str, host: str = "", timeout: int = 45) -> tuple:
    """-> (ok, out). Never raises; a failure to RUN is distinct from a failed assertion."""
    try:
        if host:
            full = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", HOSTS[host], cmd]
        else:
            full = ["bash", "-lc", cmd]
        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        if host and r.returncode == 255:
            return False, f"__RUNFAIL__ ssh could not reach {host}: {(r.stderr or '').strip()[:120]}"
        return r.returncode == 0, (r.stdout or "").strip()
    except Exception as e:
        return False, f"__RUNFAIL__ {e}"


def _local(arg):
    """Anchor a RELATIVE local path to the vault root."""
    if arg.startswith(("/", "~/", "$HOME/")) or arg in ("~", "$HOME"):
        return arg
    return str(VAULT / arg)


def _file_exists(arg, host):
    if not host:
        arg = _local(arg)
    ok, out = sh(f"test -e {q(arg)} && echo YES || echo NO", host)
    if out.startswith("__RUNFAIL__") or not ok:
        return "UNKNOWN", out or "could not run"
    if out == "YES":
        return "PASS", ""
    ok2, out2 = sh(f"p=$(dirname {q(arg)}); if [ -e \"$p\" ]; then if [ -x \"$p\" ]; then echo ABSENT; "
                   f"else echo BLOCKED; fi; else echo NOPARENT; fi", host)
    v = (out2 or "").strip()
    if v == "ABSENT":
        return "FAIL", f"{arg} does not exist"

    okp, outp = sh(f"if sudo -n true 2>/dev/null; then sudo -n test -e {q(arg)} && echo YES || echo NO; else echo NOSUDO; fi", host)
    vp = (outp or "").strip()
    if okp and not vp.startswith("__RUNFAIL__"):
        if vp.endswith("YES"):
            return "PASS", f"(visible only with sudo — {arg} is not readable by this ssh user)"
        if vp.endswith("NO"):
            return "FAIL", f"{arg} does not exist (checked with sudo; the unprivileged look was blocked)"

    if v == "BLOCKED":
        return "UNKNOWN", (f"cannot see {arg}: its directory is not traversable by this user, "
                           f"and sudo -n is not available here either")
    if v == "NOPARENT":
        return "UNKNOWN", (f"cannot see {arg}: its parent directory is not visible either — "
                           f"missing, or blocked further up, and sudo -n is not available here. "
                           f"Not the same as absent.")
    return "UNKNOWN", out2 or "could not tell absent from unreadable"


def _sha256(arg, host):
    """`sha256: <path> == <hex>` — the file is byte-identical to what the page claims."""
    if "==" not in arg:
        return "UNKNOWN", ("sha256 needs '<path> == <hex>' — a malformed claim is an authoring "
                           "error in the note, not a failure in the world")
    path, want = [x.strip() for x in arg.split("==", 1)]
    if not host:
        path = _local(path)
    ok, out = sh(f"sha256sum {q(path)} 2>/dev/null | cut -d' ' -f1", host)
    if out.startswith("__RUNFAIL__") or not ok:
        return "UNKNOWN", out or "could not run"
    got = out.strip()
    if not got:
        return "FAIL", f"{path} missing or unreadable — cannot compare"
    if got == want:
        return "PASS", ""
    return "FAIL", f"{path} DRIFTED from the canonical: {got[:16]}… != {want[:16]}…"


def _file_absent(arg, host):
    st, msg = _file_exists(arg, host)
    if st == "UNKNOWN":
        return st, msg
    return ("PASS", "") if st == "FAIL" else ("FAIL", f"{arg} EXISTS but was asserted absent")


def _cron_present(arg, host):
    ok, out = sh("crontab -l 2>/dev/null", host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    return ("PASS", "") if arg in out else ("FAIL", f"no crontab line matching '{arg}'")


def _scheduled_present(arg, host):
    """Is this job still SCHEDULED — by cron OR by a systemd timer?"""
    _, cron = sh("crontab -l 2>/dev/null", host)
    if not cron.startswith("__RUNFAIL__") and arg in cron:
        return "PASS", "cron"
    _, units = sh("grep -rh '^ExecStart' ~/.config/systemd/user/*.service 2>/dev/null", host)
    if not units.startswith("__RUNFAIL__") and arg in units:
        return "PASS", "systemd"
    if cron.startswith("__RUNFAIL__") and units.startswith("__RUNFAIL__"):
        return "UNKNOWN", f"neither crontab nor systemd could be read: {cron} / {units}"
    return "FAIL", f"not in crontab and no systemd unit ExecStart matching '{arg}'"


def _proc_loaded(arg, host):
    """proc_loaded: <pattern-matching-the-process>::<file-it-imports>"""
    if "::" not in arg:
        return "UNKNOWN", "proc_loaded needs <proc-pattern>::<file>"
    pat, path = [x.strip() for x in arg.split("::", 1)]
    if not host:
        path = _local(path)
    safe = "[" + pat[0] + "]" + pat[1:]
    ok, out = sh(
        f"P=$(pgrep -f -o -- {q(safe)} 2>/dev/null); "
        f'if [ -n "$P" ]; then date -d "$(ps -o lstart= -p $P)" +%s; else echo NOPROC; fi; '
        f"echo ---; stat -c %Y {q(path)} 2>/dev/null || echo NOFILE", host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    parts = (out or "").split("---")
    if len(parts) != 2:
        return "UNKNOWN", f"could not read process/file times: {out[:120]!r}"
    a, b = parts[0].strip(), parts[1].strip()
    if a == "NOPROC":
        return "FAIL", f"no running process matching {pat!r} — nothing can be running this code"
    if b == "NOFILE" or not b:
        return "UNKNOWN", f"{path} has no mtime (missing?)"
    try:
        e_start, e_file = int(a.splitlines()[-1]), int(b.splitlines()[-1])
    except Exception:
        return "UNKNOWN", f"unparseable epochs {a[:40]!r}/{b[:40]!r} — UNKNOWN, never a pass"
    if e_start >= e_file:
        return "PASS", ""
    return "FAIL", (f"process started {(e_file - e_start)//60} min BEFORE {path} was written — "
                    f"the running body does not have this code; a restart is what makes it true")


def _grep_in(arg, host):
    """grep_in: <path>::<pattern>"""
    if "::" not in arg:
        return "UNKNOWN", "grep_in needs <path>::<pattern>"
    path, pat = [x.strip() for x in arg.split("::", 1)]
    if not host:
        path = _local(path)
    ok, out = sh(f"grep -c -- {q(pat)} {q(path)} 2>/dev/null || echo 0", host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    try:
        n = int((out or "0").splitlines()[-1])
    except Exception:
        n = 0
    return ("PASS", "") if n > 0 else ("FAIL", f"{pat!r} not found in {path}")


def _grep_absent(arg, host):
    """grep_absent: <path>::<pattern> — the CONTENT inverse of file_absent."""
    if "::" not in arg:
        return "UNKNOWN", "grep_absent needs <path>::<pattern>"
    path, pat = [x.strip() for x in arg.split("::", 1)]
    ok, out = sh(f"if [ -e {q(path)} ]; then grep -c -- {q(pat)} {q(path)} 2>/dev/null; true; "
                 f"else echo __NOFILE__; fi", host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    last = (out or "").splitlines()[-1] if out else ""
    if last == "__NOFILE__":
        return "UNKNOWN", f"{path} does not exist — absence of the pattern proves nothing"
    try:
        n = int(last)
    except Exception:
        return "UNKNOWN", f"could not read a count from {last!r}"
    return ("PASS", "") if n == 0 else ("FAIL", f"{pat!r} is BACK in {path} ({n} hit(s))")


def _gh_private(arg, host):
    """gh_private: owner/repo — the repo must be verifiably PRIVATE."""
    ok, out = sh(f"gh repo view {q(arg)} --json isPrivate -q .isPrivate 2>/dev/null", host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    v = (out or "").strip()
    if v == "true":
        return "PASS", ""
    if v == "false":
        return "FAIL", f"{arg} is PUBLIC but was asserted private"
    return "UNKNOWN", f"could not determine visibility of {arg} (got {v!r})"


def _git_tracked(arg, host):
    """git_tracked: <repo>::<path> — the file must be UNDER VERSION CONTROL and clean."""
    if "::" not in arg:
        return "UNKNOWN", f"git_tracked needs <repo>::<path>, got {arg!r}"
    repo, _, rel = arg.partition("::")
    repo, rel = repo.strip(), rel.strip()
    ok, out = sh(f"cd {q(repo)} 2>/dev/null && git rev-parse --is-inside-work-tree 2>&1", host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    if (out or "").strip() != "true":
        return "FAIL", f"{repo} is not a git repository — nothing in it is versioned"
    ok, out = sh(f"cd {q(repo)} && git ls-files --error-unmatch -- {q(rel)} 2>&1", host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    if not ok or rel not in (out or ""):
        return "FAIL", f"{rel} is NOT tracked in {repo} — it lives on one disk"
    ok2, dirty = sh(f"cd {q(repo)} && git status --porcelain -- {q(rel)} 2>&1", host)
    if dirty.startswith("__RUNFAIL__"):
        return "UNKNOWN", dirty
    if (dirty or "").strip():
        return "FAIL", (f"{rel} is tracked but UNCOMMITTED ({dirty.strip()[:40]!r}) — "
                        f"history holds a different version than the disk")
    return "PASS", ""


def _git_push_blocked(arg, host):
    """git_push_blocked: <repo>::<remote> — pushing to that remote must be IMPOSSIBLE."""
    if "::" not in arg:
        return "UNKNOWN", f"git_push_blocked needs <repo>::<remote>, got {arg!r}"
    repo, _, remote = arg.partition("::")
    repo, remote = repo.strip(), remote.strip()
    ok, out = sh(f"cd {q(repo)} 2>/dev/null && git rev-parse --is-inside-work-tree 2>&1", host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    if (out or "").strip() != "true":
        return "FAIL", f"{repo} is not a git repository"
    ok, out = sh(f"cd {q(repo)} && git remote get-url --push {q(remote)} 2>&1", host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    url = (out or "").strip()
    if not ok or "No such remote" in url or not url:
        return "PASS", f"no '{remote}' remote — no push path exists (NOT a verified sentinel)"
    if re.match(r'^(https?://|git://|ssh://|[^/\s]+@[^/\s]+:)', url):
        return "FAIL", f"push to '{remote}' is LIVE -> {url}"
    return "PASS", ""


def _gh_owner_private(arg, host):
    """gh_owner_private: <owner>::<comma-separated repos that are DELIBERATELY public>"""
    parts = [x.strip() for x in arg.split("::")]
    owner = parts[0]
    since = parts[1] if len(parts) > 1 and parts[1] else "0000-00-00"
    allowed = {a.strip() for a in (parts[2] if len(parts) > 2 else "").split(",") if a.strip()}
    ok, out = sh(f"gh repo list {q(owner)} --limit 400 --json name,visibility,isFork,createdAt", host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    if not ok or not (out or "").strip():
        return "UNKNOWN", f"could not list repos for {owner} — NOT a pass ({(out or '')[:80]})"
    try:
        repos = json.loads(out)
    except Exception as e:
        return "UNKNOWN", f"unparseable gh output: {e}"
    leaked = sorted(r["name"] for r in repos
                    if r.get("visibility") == "PUBLIC" and not r.get("isFork")
                    and (r.get("createdAt") or "")[:10] >= since
                    and r["name"] not in allowed)
    if leaked:
        return "FAIL", f"PUBLIC non-fork repo(s) not declared public: {', '.join(leaked)}"
    stale = sorted(a for a in allowed
                   if not any(r["name"] == a and r.get("visibility") == "PUBLIC" for r in repos))
    if stale:
        return "PASS", f"allowlist entries no longer public (prune them): {', '.join(stale)}"
    return "PASS", ""


def _git_unpushed(arg, host):
    """git_unpushed: <root-dir>[::<owner-substring>] — no repo under root may hold unpushed work."""
    parts = [x.strip() for x in arg.split("::")]
    root = parts[0]
    owner = parts[1] if len(parts) > 1 else ""
    grace = 2700
    dirty_grace = 21600
    excluded = {x.strip() for x in (parts[2] if len(parts) > 2 else "").split(",") if x.strip()}
    dign = {}
    for spec in (parts[3] if len(parts) > 3 else "").split(","):
        if "=" in spec:
            repo, paths = spec.split("=", 1)
            dign[repo.strip()] = [x.strip() for x in paths.split(";") if x.strip()]
    for repo, paths in dign.items():
        for x in paths:
            if any(c in x for c in "*?[]'\"\\ ") or x.endswith("/"):
                return "ERROR", f"dirty exemption {repo}={x!r}: explicit file paths only (no globs, dirs, quotes or spaces)"
    dcase = "".join(f'{r}) set -- . ' + " ".join(f"':(exclude,literal){x}'" for x in ps) + ' ;; ' for r, ps in dign.items())
    dsel = f'case "$(basename "$r")" in {dcase}*) set -- ;; esac; ' if dign else 'set -- ; '
    cmd = (
        f'find {q(root)} -maxdepth 3 -name .git -prune 2>/dev/null | while read -r g; do '
        f'  r="${{g%/.git}}"; '
        f'  u=$(git -C "$r" remote get-url origin 2>/dev/null); '
        f'  if [ -z "$u" ]; then u=$(git -C "$r" remote 2>/dev/null | head -1 | xargs -r -I{{}} git -C "$r" remote get-url {{}} 2>/dev/null); fi; '
        f'  [ -n "$u" ] || continue; '
        f'  case "$u" in *{q(owner)}*) ;; *) continue ;; esac; '
        f'  orig=$(basename "$u" .git); '
        f'  br=$(git -C "$r" rev-parse --abbrev-ref HEAD 2>/dev/null); '
        f'  rem=$(git -C "$r" rev-parse --abbrev-ref --symbolic-full-name @{{u}} 2>/dev/null | cut -d/ -f1); '
        f'  [ -n "$rem" ] && rsha=$(git -C "$r" ls-remote --heads "$rem" "$br" 2>/dev/null | cut -f1) || rsha=""; '
        f'  if [ -n "$rsha" ]; then a=$(git -C "$r" rev-list --count "$rsha"..HEAD 2>/dev/null || echo 0); '
        f'  else a=$(git -C "$r" rev-list --count @{{u}}..HEAD 2>/dev/null); fi; '
        f'  if [ -z "$a" ]; then echo "NOUPSTREAM $(basename "$r")@$orig"; '
        f'  elif [ "$a" -gt 0 ]; then '
        f'    oldest=$(git -C "$r" rev-list @{{u}}..HEAD | tail -1); '
        f'    t=$(git -C "$r" log -1 --format=%ct "$oldest" 2>/dev/null); '
        f'    age=$(( $(date +%s) - ${{t:-0}} )); '
        f'    if [ "$age" -gt {grace} ]; then echo "AHEAD:$a:$age $(basename "$r")@$orig"; fi; '
        f'  fi; '
        + dsel +
        f'  d=$(git -C "$r" status --porcelain -uall -- "$@" 2>/dev/null | wc -l); '
        f'  un=$(git -C "$r" status --porcelain -uall -- "$@" 2>/dev/null | grep -c "^??"); '
        f'  if [ "$d" -gt 0 ]; then '
        f'    dt=$(git -C "$r" status --porcelain -uall -- "$@" 2>/dev/null | sed "s/^...//" | sed "s/.* -> //" | tr -d \'"\' | '
        f'         while read -r f; do [ -e "$r/$f" ] && stat -c %Y "$r/$f" 2>/dev/null; done | sort -n | head -1); '
        f'    dage=$(( $(date +%s) - ${{dt:-0}} )); '
        f'    if [ -n "$dt" ] && [ "$dage" -gt {dirty_grace} ]; then echo "DIRTY:$d:$dage:$un $(basename "$r")@$orig"; fi; '
        f'  fi; '
        f'done'
    )
    ok, out = sh(cmd, host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    lines = [l for l in (out or "").strip().split("\n") if l.strip()]
    def _excluded(l):
        tag = l.split(" ", 1)[-1]
        return tag in excluded or tag.split("@", 1)[0] in excluded
    lines = [l for l in lines if not _excluded(l)]
    ahead = [l for l in lines if l.startswith("AHEAD:")]
    noups = [l.split(" ", 1)[1] for l in lines if l.startswith("NOUPSTREAM")]
    dirty = [l for l in lines if l.startswith("DIRTY:")]

    def _fmt(l, unit, div):
        meta, name = l.split(" ", 1)
        n, age = meta.split(":")[1:3]
        return f"{name} ({n} {unit}, oldest {int(age)//div}{'h' if div>60 else 'min'} old)"

    problems = []
    if dirty:
        def _fmtd(l):
            meta, name = l.split(" ", 1)
            n, age, un = meta.split(":")[1:4]
            mod = int(n) - int(un)
            bits = []
            if int(un): bits.append(f"{un} untracked")
            if mod:     bits.append(f"{mod} modified")
            return f"{name} ({', '.join(bits)}, oldest {int(age)//3600}h)"
        problems.append("uncommitted work — untracked files have NO git object at all: "
                        + ", ".join(_fmtd(l) for l in dirty))
    if noups:
        problems.append("no upstream configured, cannot be pushed at all: " + ", ".join(noups))
    if ahead:
        problems.append("unpushed work older than the push cycle — on ONE disk: "
                        + ", ".join(_fmt(l, "commits", 60) for l in ahead))
    if problems:
        return "FAIL", " | ".join(problems)
    return "PASS", ""


def _cmd_absent(arg, host):
    """cmd_absent: <command> — this executable must NOT be on PATH."""
    cmd = arg.strip()
    if not cmd:
        return "UNKNOWN", "cmd_absent needs a command name"
    ok, out = sh(f"command -v {q(cmd)} >/dev/null 2>&1 && echo PRESENT || echo ABSENT", host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    v = (out or "").strip()
    if v == "ABSENT":
        return "PASS", ""
    if v == "PRESENT":
        return "FAIL", f"`{cmd}` IS on PATH — a bound that assumed its absence no longer holds"
    return "UNKNOWN", f"could not determine whether {cmd} is present (got {v!r})"


def _bind_not_wildcard(arg, host):
    """bind_not_wildcard: <port> — this port must NOT be listening on 0.0.0.0 / :: / *."""
    port = arg.strip()
    if not port.isdigit():
        return "UNKNOWN", f"bind_not_wildcard needs a port number, got {port!r}"
    ok, out = sh("ss -tulnH 2>/dev/null | awk '{print $1, $5}'", host)
    if out.startswith("__RUNFAIL__"):
        return "UNKNOWN", out
    rows = [r.strip() for r in (out or "").split("\n") if r.strip()]
    if not rows:
        return "UNKNOWN", "ss returned nothing — cannot tell bound from unbound"
    parsed = []
    for r in rows:
        parts = r.split()
        proto = parts[0] if parts else ""
        addrs = [x for x in parts[1:] if ":" in x]
        for a in addrs:
            if a.endswith(":*"):
                continue
            parsed.append((proto, a))
    mine = [(pr, a) for pr, a in parsed if a.rsplit(":", 1)[-1] == port]
    if not mine:
        return "PASS", f"nothing is listening on :{port} on tcp or udp (NOT a verified bind)"
    wild = [(pr, a) for pr, a in mine if a.rsplit(":", 1)[0] in ("0.0.0.0", "*", "[::]", "::")]
    if wild:
        return "FAIL", (":{p} is bound to {b} — answers on EVERY interface".format(
            p=port, b=", ".join(f"{pr} {a}" for pr, a in wild)))
    return "PASS", ""


KINDS = {
    "bind_not_wildcard": _bind_not_wildcard,
    "cmd_absent": _cmd_absent,
    "git_unpushed": _git_unpushed,
    "gh_owner_private": _gh_owner_private,
    "git_push_blocked": _git_push_blocked,
    "gh_private": _gh_private,
    "git_tracked": _git_tracked,
    "file_exists": _file_exists,
    "file_absent": _file_absent,
    "flag_absent": _file_absent,
    "flag_present": _file_exists,
    "cron_present": _cron_present,
    "scheduled_present": _scheduled_present,
    "sha256": _sha256,
    "proc_loaded": _proc_loaded,
    "grep_in": _grep_in,
    "grep_absent": _grep_absent,
}


def parse_claims(path: Path):
    """-> list of (host, kind, arg). Only reads the frontmatter block."""
    txt = path.read_text(encoding="utf-8", errors="ignore")
    if not txt.startswith("---"):
        return []
    end = txt.find("\n---", 3)
    fm = txt[3:end if end > 0 else 400]
    if "verify:" not in fm:
        return []
    out = []
    inblock = False
    for line in fm.splitlines():
        if re.match(r"^\s*verify:\s*$", line):
            inblock = True
            continue
        if inblock:
            m = re.match(r"^\s*-\s*(.+?)\s*$", line)
            if not m:
                if line.strip() and not line.startswith(" "):
                    break
                continue
            item = m.group(1)
            item = re.sub(r"\s+#.*$", "", item).strip().strip('"\'')
            host = ""
            for h in HOSTS:
                if item.startswith(h + ":"):
                    host, item = h, item[len(h) + 1:].strip()
                    break
            if ":" not in item:
                out.append((host, "__BAD__", item))
                continue
            kind, arg = item.split(":", 1)
            out.append((host, kind.strip(), arg.strip()))
    return out


def main() -> int:
    global VAULT
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=str(VAULT), help="the notes folder (default: MEMTOOLS_VAULT or the current folder)")
    ap.add_argument("--quiet", action="store_true", help="only print problems")
    ap.add_argument("--any-host", action="store_true",
                    help="run even on a host other than the configured home_host")
    a = ap.parse_args()
    root = Path(a.vault)
    VAULT = root

    home = (CONFIG.get("verify", {}) or {}).get("home_host", "")
    if home and not a.any_host and socket.gethostname().lower() != home.lower():
        print("=== vault-verify: REFUSED — claims without a host prefix are meant for the configured "
              "home_host, and this is a different host. Run it there, or pass --any-host. ===")
        return 2

    pages = [p for p in root.rglob("*.md") if ".git" not in p.parts and "raw/" not in str(p)]
    results = []
    for p in sorted(pages):
        for host, kind, arg in parse_claims(p):
            rel = str(p.relative_to(root))
            if kind == "__BAD__" or kind not in KINDS:
                results.append((rel, host, kind, arg, "UNKNOWN",
                                f"unknown assertion kind '{kind}' — NOT a pass"))
                continue
            st, msg = KINDS[kind](arg, host)
            results.append((rel, host, kind, arg, st, msg))

    fails = [r for r in results if r[4] == "FAIL"]
    unknown = [r for r in results if r[4] == "UNKNOWN"]
    passes = [r for r in results if r[4] == "PASS"]

    print(f"=== vault-verify: {len(results)} claims across "
          f"{len({r[0] for r in results})} pages ===")
    if not results:
        print("  UNKNOWN: NO CLAIMS DECLARED — zero pages carried a `verify:` block.")
        print("  This vault asserts nothing checkable about the running system, which is NOT the")
        print("  same as being correct. If you expected claims, the vault path is probably wrong.")
        return 2
    print(f"  PASS {len(passes)} · FAIL {len(fails)} · UNKNOWN {len(unknown)}\n")
    for label, rows in (("FAILED — the vault says something untrue", fails),
                        ("UNKNOWN — could not check, NOT a clean bill of health", unknown)):
        if rows:
            print(f"  {label}:")
            for rel, host, kind, arg, st, msg in rows:
                where = f"{host}:" if host else ""
                print(f"    {rel}\n        {where}{kind}: {arg}\n        -> {msg}")
            print()
    if not a.quiet and passes:
        print("  holding:")
        for rel, host, kind, arg, st, msg in passes:
            print(f"    ok  {(host + ':') if host else ''}{kind}: {arg}   ({rel})")
    return 1 if fails else (2 if unknown else 0)


if __name__ == "__main__":
    raise SystemExit(main())
