#!/usr/bin/env bash
# build-image.sh — build the test-lab image for amd64 from this checkout's .devcontainer/Dockerfile,
# natively on an x86_64 host, and record what it was built from.
#
#   testlab/x86/build-image.sh <tag> <record-file>
#
# The base image the Dockerfile names (its one FROM line) is pulled once with the transfer backoff,
# its registry digest is read from the pull and the build is pinned to exactly that digest
# (`--build-context <base>=docker-image://<repo>@sha256:…`), so the image is built from the content
# the record names, never from a tag that moved in between. The record file gets the tag, the image
# id, its architecture, the base, the pinned digest and the Dockerfile's sha256.
#
# Exit 0: built for amd64 and recorded. Exit 1: refused, with the reason on stderr (not an x86_64
# host, no single FROM line, the pull or the build failed after the backoff, no digest for the base,
# the image is not amd64). Exit 2: usage.
set -euo pipefail

stop() { echo "build-image: $*" >&2; exit 1; }

if [ "$#" -ne 2 ]; then
	echo "usage: testlab/x86/build-image.sh <tag> <record-file>" >&2
	exit 2
fi
tag=$1
record=$2
[ -n "$tag" ] || stop "the tag is empty"
[ -n "$record" ] || stop "the record file is empty"

here=$(dirname -- "${BASH_SOURCE[0]}") || stop "cannot resolve this script's directory"
root=$(cd -- "$here/../.." && pwd -P) || stop "cannot resolve the checkout root from $here"
dockerfile=$root/.devcontainer/Dockerfile
[ -r "$dockerfile" ] || stop "cannot read $dockerfile"

machine=$(uname -m) || stop "uname -m failed"
[ "$machine" = x86_64 ] || stop "this host is $machine: the amd64 lab image is built natively on x86_64, never emulated"

froms=$(sed -n 's/^FROM[[:space:]]\{1,\}\([^[:space:]]\{1,\}\).*/\1/p' "$dockerfile") || stop "cannot read the FROM line of $dockerfile"
case "$froms" in
	"") stop "no FROM line in $dockerfile" ;;
	*$'\n'*) stop "more than one FROM line in $dockerfile: the base cannot be pinned by one digest" ;;
esac
base=$froms
# The repository part of the base: without a digest, and without a tag after the last /.
repo=${base%@*}
last=${repo##*/}
case "$last" in *:*) repo=${repo%:*} ;; esac

df_sum=$(sha256sum -- "$dockerfile") || stop "sha256sum $dockerfile failed"
df_sum=${df_sum%% *}

# shellcheck source=../../.github/scripts/retry.sh
. "$root/.github/scripts/retry.sh"

backoff docker pull --platform linux/amd64 "$base" || stop "docker pull $base failed after the transfer backoff"
digests=$(docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' "$base") \
	|| stop "docker image inspect $base failed"
pinned=
while IFS= read -r d; do
	case "$d" in
		"$repo"@sha256:*)
			[[ "${d#"$repo"@sha256:}" =~ ^[0-9a-f]{64}$ ]] || continue
			[ -z "$pinned" ] || [ "$pinned" = "$d" ] || stop "two digests for $repo: $pinned and $d"
			pinned=$d ;;
	esac
done <<< "$digests"
[ -n "$pinned" ] || stop "no registry digest for $base: an unrecorded base is not built from"

backoff docker build --platform linux/amd64 --build-context "$base=docker-image://$pinned" \
	-t "$tag" -f "$dockerfile" "$root/.devcontainer" \
	|| stop "docker build failed after the transfer backoff"

arch=$(docker image inspect -f '{{.Architecture}}' "$tag") || stop "docker image inspect $tag failed"
[ "$arch" = amd64 ] || stop "the built image $tag is $arch, not amd64"
id=$(docker image inspect -f '{{.Id}}' "$tag") || stop "docker image inspect $tag failed"

rdir=$(dirname -- "$record") || stop "cannot resolve the directory of $record"
mkdir -p -- "$rdir" || stop "cannot create $rdir"
{
	printf 'image_tag = %s\n' "$tag"
	printf 'image_id = %s\n' "$id"
	printf 'image_arch = %s\n' "$arch"
	printf 'base = %s\n' "$base"
	printf 'base_pinned = %s\n' "$pinned"
	printf 'dockerfile_sha256 = %s\n' "$df_sum"
} > "$record" || stop "cannot write $record"
cat -- "$record"
