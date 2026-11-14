# memtools

Memory tools for AI agents that keep their notes as markdown: search before acting, check that notes are still true, track which notes get used, and find missing links.

Topics: agent-memory, llm-agents, ai-agents, markdown-notes, knowledge-management, memory-tools

Small command-line tools for an agent (or a person) that keeps its memory as a folder of markdown notes. They do four things a pile of notes does not do by itself: check that the notes are still true, find the right note before acting, notice which notes are actually used, and propose the links between them.

Twelve tools and three test suites.

Python 3.10 or newer, standard library only (3.11+ if you use a config file). vault-verify uses `ss`, so its bind checks are Linux only. vault-commit.sh needs bash, git and `flock`.

## The ideas, in the order they matter

**1. Check that your notes are still true.** Notes that describe a running system drift, in both directions, and nothing tells you. So a note can carry claims a machine can check, and a nightly run checks all of them. An unknown claim is an error, and a host that cannot be reached is not a pass.
*Why:* a gate had silently blocked 76 of 80 of one agent's memory writes for eight days, and eight recorded claims about the running system had each been wrong within two days. (`vault-verify`)

**2. Make recall a verb, not a briefing.** A summary loaded at the start of a session is read once and skipped under pressure. A search the agent runs before it acts gets used.
*Why:* an agent designed a new voice loop without searching its notes, when one built two months earlier was still installed and running. The owner's question found it, not the agent. (`vault-recall`)

**3. Log when a note is used, and read that log.** When a note actually changes a decision, write one line saying so. A note applied on two separate days has earned its place; a note never applied is a candidate for pruning.
*Why:* 196 such lines were written and nothing read them; the first reader found 10 notes already past the bar for promotion, and 9 of them were promoted that day. (`vault-applied`)

**4. Links are how notes find each other.** A note that nothing links to only turns up for someone who already knows to ask for it. The link tool proposes the missing links, not just the broken ones.
*Why:* on its first run it caught an agent re-deriving a rule that was already written down and promoted; the promoted note had nothing linking to it, so it never surfaced. (`vault-discover`)

**5. If two agents share one repository, commit from a private index under a lock.** Otherwise one agent's half-staged files go into the other's commit, under the wrong name, or break it. (`vault-commit.sh`)

## The tools

| tool | answers |
|---|---|
| `vault-recall.py` | what do I already know about X? (`--arcs`: what am I in the middle of? `--open`: read one) |
| `vault-verify.py` | is what the notes say still true? |
| `vault-applied.py` | which notes earned promotion, which are never used, which citations point at nothing? |
| `vault-discover.py` | which links are missing (A-B and B-C but no A-C), which notes nothing links to? |
| `vault-graph.py` | a self-contained HTML picture of the notes, sized by use, for a person to look at |
| `vault-index.py` | regenerate the generated half of index.md, leaving the hand-written top alone |
| `vault-open-questions.py` | which open questions are past due with no outcome? |
| `vault-close.py` | mark work finished: status, a closure note and a log line, on evidence, not age |
| `vault-park.py` | park unfinished work with a named condition for picking it up again |
| `vault-lint.py` | broken or line-split links, bad status values, stale active pages, typed source refs; your own schema rules if you set them |
| `log-rotator.py` | archive log.md past N lines; every reader here also reads the archives |
| `vault-commit.sh` | commit chosen paths under the agent's name from a private index under a lock (`MEMTOOLS_AGENT=name`) |

**If you are an agent:** read AGENTS.md. It says when to run each tool.

AGENTS.md was tried on a fresh agent that had never seen these notes: it searched, checked claims and refused to close work by its date, without being told to. Rule 3 (writing the applied line) has not yet been tried on a fresh agent that can write files; that test agent was read-only.

## Quick start

```
cd my-notes/                                  # a folder of .md files; subfolders are fine
python3 path/to/tools/vault-recall.py "how do I restart the backup job"
python3 path/to/tools/vault-applied.py
python3 path/to/tools/vault-verify.py
```

Each tool works on the current folder, or on `--vault DIR`, or on `$MEMTOOLS_VAULT`. With no config, every page weighs the same and work in progress lives in `projects/`; `memtools.example.toml` shows how to name your own folders (`[layout]`), size limits for the index (`[index]`), topic groups for the graph (`[graph.topics]`) and hosts for remote claims (`[hosts]`).

## Writing claims a machine can check

Put a `verify:` list in a note's frontmatter:

```
---
title: Backups
verify:
  - file_exists: /etc/cron.d/backup
  - grep_in: /etc/cron.d/backup::backup.sh
  - scheduled_present: backup.sh
  - bind_not_wildcard: 5432
  - web1:file_exists: /srv/www/index.html
---
```

A prefix such as `web1:` checks the claim over ssh on that host; name your hosts in `memtools.toml` (see `memtools.example.toml`). Relative paths are relative to the notes folder.

Kinds: `file_exists`, `file_absent`, `flag_present`, `flag_absent`, `sha256` (`<path> == <hex>`), `grep_in` and `grep_absent` (`<path>::<pattern>`), `cron_present`, `scheduled_present` (cron or a systemd user unit), `proc_loaded` (`<process pattern>::<file>`: the running process started after the file was last written), `git_tracked` (`<repo>::<path>`), `git_unpushed` (`<dir>[::<remote substring>]`), `git_push_blocked` (`<repo>::<remote>`), `gh_private` and `gh_owner_private` (GitHub CLI), `cmd_absent`, `bind_not_wildcard`.

Results: PASS, FAIL, or UNKNOWN (could not check: unreachable host, unreadable file, unknown kind, malformed claim). Exit 0 if all hold, 1 if anything failed, 2 if anything could not be checked. A run with no claims at all exits 2, because a folder that asserts nothing is not the same as a folder that is right.

**Security.** Claims are checked with shell commands, locally or over ssh. Every argument is quoted, and the tests check that shell text inside a claim is not run. Even so, only point vault-verify at notes you trust.

## The log line

```
## [2026-11-01] applied | [[backup-restore]] -> restored from the right snapshot first time
## [2026-11-03] violated | [[read-the-card-first]] -> answered before reading the card
```

`vault-recall` ranks notes higher the more distinct days they were applied. `vault-applied` lists promotion candidates (applied on 2+ distinct days), pruning candidates (never applied, older than `--stale-days`), and citations to notes that do not exist. A `violated` line is logged and shown, never counted as support for the rule.

## What the tools write

Nothing is deleted by any tool. What is written, and where:
- Inside the notes folder, only when you ask: `vault-close.py` and `vault-park.py` edit the one page you name and append a line to `log.md` (dry run unless `--apply`; a page name with a folder or `..` in it is refused); `vault-index.py` rewrites the generated half of `index.md` (`--apply`); `log-rotator.py` archives `log.md` beside it (`--apply`); `vault-discover.py --write` writes `discoveries.md`.
- Where you point it: `vault-graph.py` writes its page to `--out` (default `./vault-graph.html` in the current folder); `vault-lint.py --report FILE` and `vault-discover.py --out FILE` write the file you name.
- Commands: `vault-verify.py` runs read-only checks built from your notes' claims (quoted), locally or over ssh to hosts you configure. `vault-commit.sh` commits and, with `--push`, pushes the repository it is run in.
- Everything else only reads.

## Tests

```
python3 tests/test_vault_verify.py
python3 tests/test_tools_smoke.py
python3 tests/test_commit_and_lint.py
python3 tests/test_review_fixes.py
```

`test_tools_smoke.py` runs the note tools on a small made-up notes folder in a temp directory (22 checks, nothing written outside it, including that rotating the log loses no applied history and that a page name cannot reach outside the notes folder). `test_commit_and_lint.py` (17 checks) commits in a temp git repo with a local remote: a new file is committed under the agent's name, another agent's staged file is neither swept in nor unstaged, nothing-changed and deletions are handled, and a push made in between is rebased over; then lint's checks, with and without a schema. `test_vault_verify.py` has 38 controls: every kind answers UNKNOWN for an unreachable host, malformed claims are UNKNOWN, both answers are reachable for the simple kinds, a port bound to all interfaces fails and a loopback-only one passes (with sockets the test opens itself), and shell text in a claim is not executed. `test_review_fixes.py` (v1.4.1, 6 arms) holds one control per fix from an outside review (see CHANGELOG.md); run it with `<v1.4 tools dir> --control` and every arm must fail on the old code.

## What is not here

The notes themselves. These tools come from a lab whose agents keep their memory this way; the notes stay private. METHOD.md tells, once, the failure behind each rule the tools enforce.

Licence: MIT (see LICENSE).
