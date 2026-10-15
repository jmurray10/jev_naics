#!/usr/bin/env bash
# Build a fresh, history-free copy of this repo for public release (JEV-59). The public repository starts
# from the reviewed tree at HEAD, not from the private history (which holds work addresses, internal
# references, and results on private data that the current tree no longer carries). Never pushes.
#
#   tools/export_public.sh <new-empty-dir> [author-name] [author-email]
#
# author-email: what the public commit should show, e.g. your GitHub noreply address
#   (<id>+<user>@users.noreply.github.com, GitHub Settings > Emails).
# Then, after reading the result: create an empty public repository and push the one commit yourself.
set -euo pipefail

target="${1:?usage: tools/export_public.sh <new-empty-dir> [author-name] [author-email]}"
name="${2:-$(git log -1 --format=%an)}"
email="${3:-$(git log -1 --format=%ae)}"
root="$(git rev-parse --show-toplevel)"

[[ -z "$(git -C "$root" status --porcelain)" ]] || { echo "commit or stash your changes first: the export is HEAD" >&2; exit 1; }
[[ ! -e "$target" || -z "$(ls -A "$target")" ]] || { echo "$target is not empty" >&2; exit 1; }
mkdir -p "$target"

git -C "$root" archive --format=tar HEAD | tar -x -C "$target"
git -C "$target" init -q -b main
git -C "$target" add -A
GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email" GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email" \
  git -C "$target" commit -q -m "jev_naics: first public release

Exported from the private repository at $(git -C "$root" rev-parse --short HEAD) by tools/export_public.sh."

echo "Exported $(git -C "$target" ls-files | wc -l | tr -d ' ') files to $target as one commit by $name <$email>."
fail=0
if git -C "$target" ls-files | grep -E '(^|/)(\.env$|test_data/|\.scratch/)' ; then echo "local-only files were exported" >&2; fail=1; fi
if command -v gitleaks >/dev/null; then gitleaks git --redact "$target" || fail=1; else echo "gitleaks not installed: skipped"; fi
(cd "$target" && python3 tools/leak_scan.py) || fail=1
[[ "$fail" == 0 ]] || { echo "do not publish: a check failed" >&2; exit 1; }
echo "Checks passed. Read it, then create an empty public repository and push this one commit yourself."
