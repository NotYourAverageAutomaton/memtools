# For an agent working in a notes folder that has these tools

These notes are your memory. The tools are how you use it. Run them at these moments, without being asked.
Paths below assume the tools are in `tools/`; run them from the notes folder (or pass `--vault`).

1. **Before you act on anything that touches past work, search first:** `python3 tools/vault-recall.py "<what you are about to do>"`, then `--open <stem>` on the best hit.
   Why: an index read at the start of a session gets skipped; agents re-build what their notes already hold.
2. **When you resume work, ask where you were:** `python3 tools/vault-recall.py --arcs`. Read the page before you touch the work; the page outranks your memory of it.
3. **When a note actually changes what you do, log it in `log.md`:** `## [YYYY-MM-DD] applied | [[note-stem]] -> what it changed`. If you knew a note and did not follow it, log `violated` instead.
   Why: these lines are how notes earn promotion and how unused notes are found.
4. **Before you state that something is running, configured or present, check it:** `python3 tools/vault-verify.py`, and add `verify:` claims to the note if it has none.
   Why: notes drift from the system they describe, silently. An unknown or unreachable check is not a pass.
   A claim must point at the system (the file, the schedule, the port), never at the note's own text: a note that greps itself always passes.
5. **Before you close a piece of work, check it against the running system, not its date:** `python3 tools/vault-close.py <stem> --check --why "<the evidence>"` (dry run), then `--apply`.
   Why: work that looks abandoned by its date is often still running.
6. **When you set work aside unfinished, say what brings it back:** `python3 tools/vault-park.py <stem> --until "<condition>" --apply`.
7. **When you write a new note, link it** to at least one related note with `[[stem]]` and a reason. Then `python3 tools/vault-discover.py` shows links that are missing and notes nothing links to.
   Why: a note nothing links to only turns up for someone who already knows to ask for it.
8. **At the end of a session:** `python3 tools/vault-verify.py`, `python3 tools/vault-applied.py`, `python3 tools/vault-lint.py`. Act on what they list: fix broken links, promote what earned it, close or re-date what is stale.
   Why: a report nobody reads is a queue filling in silence.
9. **If you share this repository with another agent, commit with** `MEMTOOLS_AGENT=<your name> tools/vault-commit.sh -m "<what and why>" <paths>`, never `git add -A`.
   Why: a sweep signs your name to someone else's work, and a shared index breaks commits.

Never edit `raw/` if the folder has one, and never delete a note to make a check pass: fix the note or record why it is wrong.
