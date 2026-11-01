#!/usr/bin/env bash
# graywolf-amd64-sha256.sh — the sha256 that lhpc/data/scripts/graywolf-fetch.sh records for the
# amd64 .deb of one Graywolf release, so the x86 slow-build lane checks its download against it.
#
#   testlab/x86/graywolf-amd64-sha256.sh <version>
#
# Prints the 64-hex value and exits 0 only when exactly one `<version>/amd64) echo "<sha256>"` line
# exists. Exit 1, naming the reason, when the version is malformed, unrecorded, recorded twice or its
# value is not a sha256; exit 2 on usage.
set -euo pipefail

stop() { echo "graywolf-amd64-sha256: $*" >&2; exit 1; }

if [ "$#" -ne 1 ]; then
	echo "usage: testlab/x86/graywolf-amd64-sha256.sh <version>" >&2
	exit 2
fi
version=$1
[[ "$version" =~ ^[0-9]+(\.[0-9]+)+$ ]] || stop "not a release version: '$version'"

here=$(dirname -- "${BASH_SOURCE[0]}") || stop "cannot resolve this script's directory"
root=$(cd -- "$here/../.." && pwd -P) || stop "cannot resolve the checkout root from $here"
table=$root/lhpc/data/scripts/graywolf-fetch.sh
[ -r "$table" ] || stop "cannot read $table"

# Exact field comparison, not a regex: the dots of a version are literal.
values=$(awk -v key="$version/amd64)" '$1 == key && $2 == "echo" { v = $3; gsub(/"/, "", v); print v }' "$table") \
	|| stop "awk failed on $table"
case "$values" in
	"") stop "no amd64 sha256 recorded for graywolf $version in $table" ;;
	*$'\n'*) stop "more than one amd64 sha256 recorded for graywolf $version in $table" ;;
esac
[[ "$values" =~ ^[0-9a-f]{64}$ ]] || stop "the value recorded for graywolf $version/amd64 is not a sha256: $values"
printf '%s\n' "$values"
