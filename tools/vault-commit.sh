#!/usr/bin/env bash
# vault-commit.sh — commit chosen paths of a shared notes repo under the AGENT's name, without touching
# anyone else's staged or unstaged work.
#
#   MEMTOOLS_AGENT=<name> vault-commit.sh -m "<message>" [--push] <paths...>
#   MEMTOOLS_AGENT=<name> vault-commit.sh -F <msgfile>   [--push] <paths...>
#
# Why each piece exists (METHOD.md has the failures):
# - The author is the agent, not the account owner, so a wrong note can be traced to who wrote it.
#   No agent name -> refuse. An anonymous commit is the thing this prevents.
# - Explicit paths only, and new files are staged too (plain `git commit <path>` skips untracked files).
# - The commit is built in a PRIVATE temporary index (read-tree HEAD + only these paths), and the branch
#   moves by compare-and-swap under a lock. Another agent's half-staged files can neither break this
#   commit nor be swept into it.
# - A commit that did not happen never reports success: HEAD is checked.
# - --push retries after rebasing if another writer pushed in between; a real conflict stops, loudly.
set -euo pipefail
paths=(); msg=""; msgfile=""; push=0; vault="${MEMTOOLS_VAULT:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    -m|--message) msg=$2; shift 2 ;;
    -F|--file) msgfile=$2; shift 2 ;;
    --push) push=1; shift ;;
    --vault) vault=$2; shift 2 ;;
    -*) echo "vault-commit: unsupported flag $1" >&2; exit 2 ;;
    *) paths+=("$1"); shift ;;
  esac
done
[ -n "$vault" ] && cd "$vault"
cd "$(git rev-parse --show-toplevel)"

who="${MEMTOOLS_AGENT:-}"
[ -n "$who" ] || { echo "vault-commit: set MEMTOOLS_AGENT to the name of the agent committing. Refusing to commit anonymously." >&2; exit 2; }
email=$(git config user.email 2>/dev/null || true)
[ -n "$email" ] || { echo "vault-commit: git config user.email is not set. Refusing." >&2; exit 2; }

[ ${#paths[@]} -gt 0 ] || { echo "vault-commit: no paths given" >&2; exit 2; }
[ -n "$msg" ] || { [ -n "$msgfile" ] && msg=$(cat "$msgfile"); }
[ -n "$msg" ] || { echo "vault-commit: no message (-m or -F)" >&2; exit 2; }

gitdir=$(git rev-parse --git-dir); branch=$(git symbolic-ref --short HEAD)
exec 9>"$gitdir/vault-commit.lock"
flock -w 120 9 || { echo "vault-commit: lock busy >120s" >&2; exit 1; }
idx=$(mktemp "$gitdir/vault-commit-index.XXXXXX"); pushlog=$(mktemp); trap 'rm -f "$idx" "$pushlog"' EXIT
before=$(git rev-parse HEAD)
new=""
for try in 1 2 3 4 5; do
  old=$(git rev-parse HEAD)
  GIT_INDEX_FILE=$idx git read-tree "$old"
  GIT_INDEX_FILE=$idx git add -A -- "${paths[@]}"
  tree=$(GIT_INDEX_FILE=$idx git write-tree)
  [ "$tree" != "$(git rev-parse "$old^{tree}")" ] || { echo "vault-commit: nothing changed in those paths. NOTHING WAS COMMITTED." >&2; exit 3; }
  new=$(printf '%s\n' "$msg" | GIT_AUTHOR_NAME="$who" GIT_AUTHOR_EMAIL="$email" GIT_COMMITTER_NAME="$who" GIT_COMMITTER_EMAIL="$email" git commit-tree "$tree" -p "$old")
  if git update-ref "refs/heads/$branch" "$new" "$old" 2>/dev/null; then break; fi
  new=""; sleep 1
done
[ -n "$new" ] || { echo "vault-commit: the branch kept moving; gave up after 5 tries" >&2; exit 1; }
git reset -q -- "${paths[@]}" 2>/dev/null || true
after=$(git rev-parse HEAD)
[ "$before" != "$after" ] || { echo "vault-commit: HEAD did not move. NOTHING WAS COMMITTED." >&2; exit 3; }
echo "committed as: $who  ($(git rev-parse --short HEAD), $(git show --stat --format= HEAD | grep -c ' | ') file(s))"
if [ "$push" = 1 ]; then
  for _try in 1 2 3; do
    git push -q origin "$branch" 2>"$pushlog" && { echo "pushed"; break; }
    if ! grep -qE "non-fast-forward|fetch first|rejected" "$pushlog"; then cat "$pushlog" >&2; exit 1; fi
    echo "push rejected (the remote moved): rebasing onto origin/$branch, try $_try" >&2
    if ! git pull -q --rebase --autostash origin "$branch" 2>&1; then
      git rebase --abort 2>/dev/null || true
      echo "vault-commit: PUSH FAILED: the rebase hit a real conflict; resolve it by hand" >&2; exit 1
    fi
    [ "$_try" = 3 ] && { echo "vault-commit: PUSH FAILED after 3 rebase-retries" >&2; exit 1; }
  done
fi
exit 0
