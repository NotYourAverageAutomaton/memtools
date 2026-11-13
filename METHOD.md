# Method: the failure behind each rule

Each rule in these tools was added after something went wrong. The failures are told here once, in plain words, so the code can stay short.

## vault-verify
- **Notes drift in both directions.** A gate had silently blocked 76 of 80 of one agent's memory writes for eight days, armed without the review its own task required. The checker was then seeded with eight recorded claims about the running system, each of which had been wrong at some point in the two days before. Nothing had been checking whether the notes still matched the system they described. So: claims in the note, checked by a machine, every night.
- **An unknown claim is an error, not a pass.** A kind the checker does not know, or a claim it cannot parse, would otherwise vanish from the count and the run would look clean.
- **Unreachable is not clean.** An early version treated "could not reach the host" as a failure of the claim, and later as nothing at all. Both are wrong. It is UNKNOWN, reported separately, with the reason.
- **Relative paths mean the notes folder.** Resolved against the current directory, a claim passed when run by hand from the notes folder and failed when run from a scheduler started in the home folder. The false alarm went out twice. A checker that cries wolf costs the same trust as one that misses.
- **A malformed claim is UNKNOWN, not FAIL.** A typo in a note is an authoring error. Reporting it as a failure in the world sends someone to look for a problem that does not exist.
- **The listen address decides, not whether the port answers.** A check that connected to a service on one address and called the bind correct could not fail: a service bound to all interfaces answers there too. Only the listening table (`ss`) shows the bind, so that is what bind_not_wildcard reads, for TCP and UDP.
- **Absence of a pattern in a missing file proves nothing.** grep_absent on a file that does not exist is UNKNOWN.
- **A process can run old code.** proc_loaded compares when the process started with when the file was last written. A file edited after the process started is not running yet.
- **Arguments are quoted.** Claims come from notes. Unquoted, a note could run a command. The tests plant shell text in claims and check that nothing runs.
- **The checker gets checked.** For its first eleven days the checker had no tests, while it was quoted as evidence and changed seven times in one day. The test suite has a minimum control count, so removing controls fails the suite.

## vault-recall
- **A briefing is not recall.** An index loaded at the start of every session was read once and skipped. An agent designed a new voice loop without searching its notes, when one built two months earlier was still installed and running; the owner's question found it. A search cheap enough to run before every decision gets run.
- **Use is a signal, with a caution.** Ranking by times applied helps the notes that work rise. It can also feed itself: a note ranked high gets applied more because it is ranked high. Distinct days, not raw counts, are what count, and the weight is mild.
- **Follow one link.** A good answer often sits one link away from the best text match, so recall adds the linked notes of its top hits at a lower weight.

## vault-applied
- **A log nobody reads is a queue filling in silence.** 196 applied lines were written over weeks and nothing read them. The first reader found 10 notes already past the bar for promotion (9 were promoted that day), one of them applied 13 times while misfiled as a draft.
- **Distinct days, not citations.** Two citations in one session are one instance, not two.
- **A violation is not support.** A line saying a rule was known and not followed was being counted as an application. It is shown separately and never added to the promotion count.
- **A decision to hold a note back belongs in the note.** Without a recorded "promotion declined" and its reason, a held-back note came back as a fresh candidate on every run. Both spellings of that field are accepted, because a second spelling once hid a recorded decline.

## vault-discover
- **A link tool should propose, not only complain.** A checker that lists broken links tells you what is wrong, not what is missing. Triadic closure (A links B, B links C, A does not link C) names the connection worth drawing. On its first run it caught an agent re-deriving a rule that was already promoted: the promoted note had nothing linking to it, so it never surfaced.

## vault-close and vault-park
- **Close on evidence, never on age.** Eighteen pieces of work were flagged as stale by their review date. Checked against the running system, only 2 of the 18 were finished, and three would have been closed while in use. So closing takes a reason, and `--check` runs the page's own claims first.
- **Finished is not decayed.** `closed` means the work ended and the record is true; `stale` means it may have drifted. Mixing them makes every old record look suspect, or every suspect record look finished.
- **A park needs a condition.** Unfinished work set aside without saying what brings it back is forgotten with a date on it.

## vault-index
- **The start of a file may be all that loads.** An agent harness that auto-loads a memory file can cut it off past a limit without saying so, keeping the top and dropping the bottom while the file still looks complete. The limit has also changed between versions. So the index refuses to write past the limit you set, and the hand-written top, the part that matters most, is never touched.
- **Search replaced the catalog.** A hand-kept list of every page grew to 89% of the file and was the first thing cut off. Once recall existed it was retired, and the file went from 88 KB to 20 KB.

## vault-graph
- **A picture is for a person, not for the agent.** Handed to an agent, a rendered graph is one more briefing. Its one signal nothing else shows is node size by times applied: what the notes actually run on.

## vault-open-questions
- **A question asked in advance has to be answered.** Questions written down before an experiment and never given an outcome are the same silent queue as an unread log. The first run found 11 open questions: 2 had an outcome, 7 were more than 14 days overdue, the oldest 76 days. Any question past its age with no outcome is listed until it gets one, or a reason it cannot.

## vault-lint
- **A link split by line wrapping resolves nowhere.** Hard-wrapped text broke 6 valid `[[links]]` across two lines; neither the editor nor recall could follow them, in either direction, and nothing said so.
- **Your schema, not ours.** The lint rules for required fields are opt-in. A stranger's notes are not failed for fields this lab happens to use.

## log-rotator
- **Rotating a log can erase what it counts.** The log is also the record of which notes were applied. Before every reader was made to read the archives too, turning rotation on would have destroyed 226 of 227 applied entries, silently. Any new reader must read `log_*.md` as well.

## vault-commit.sh
- **Git could not tell the agents apart.** The last 40 commits all carried the account owner's name, so a wrong note could not be traced to whoever wrote it. The author is now the agent, and an anonymous commit is refused.
- **`git commit <path>` skips new files.** The commit tool could not commit new files at all: three notes sat unversioned on disk, one of them reported on the board as pushed. New files are staged explicitly.
- **A shared index breaks commits.** Two sessions shared one clone's index; one staged files and deleted them before they were stored, and the other's commit failed on its half-finished staging. Earlier, a sweep with `git add -A` signed one session's name over 598 lines another session wrote, and two of its pages. The commit is built in a private index, and the branch moves only by compare-and-swap.
