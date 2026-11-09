#!/usr/bin/env bash
# houserules_ci_range.sh FLOOR EVENT BEFORE HEAD PR_BASE: print the <base>..<head> range CI hands
# tools/check_houserules.py, or nothing when no range can be checked. Notes go to stderr, for the log.
#
# The rules hold from FLOOR on (the commit history before it fails them and is not rewritten):
# - FLOOR an ancestor of HEAD: push = BEFORE..HEAD when BEFORE is a commit from the floor on and an
#   ancestor of HEAD (a fast-forward), else FLOOR..HEAD; pull_request and workflow_dispatch = FLOOR..HEAD.
# - FLOOR not an ancestor of HEAD (e.g. a hotfix off an old main): the branch shape never fails the
#   job; only what this event changed is checked: push = BEFORE..HEAD (an ancestor of HEAD),
#   pull_request = PR_BASE..HEAD, otherwise HEAD^..HEAD.
set -euo pipefail
floor=$1 event=$2 before=$3 head=$4 prbase=$5

commit() { [ -n "$1" ] && [ -n "${1//0/}" ] && git cat-file -e "$1^{commit}" 2>/dev/null; }
anc() { git merge-base --is-ancestor "$1" "$2" 2>/dev/null; }

if commit "$floor" && anc "$floor" "$head"; then
	if [ "$event" = push ] && commit "$before" && anc "$floor" "$before" && anc "$before" "$head"; then
		echo "$before..$head"
	else
		echo "$floor..$head"
	fi
	exit 0
fi
echo "NOTE: HR_FLOOR $floor is not an ancestor of $head: checking only what this $event changed" >&2
if [ "$event" = push ] && commit "$before" && anc "$before" "$head"; then
	echo "$before..$head"
elif [ "$event" = pull_request ] && commit "$prbase" && anc "$prbase" "$head"; then
	echo "$prbase..$head"
elif parent=$(git rev-parse --verify -q "$head^"); then
	echo "$parent..$head"
else
	echo "NOTE: $head has no parent: nothing to check" >&2
fi
