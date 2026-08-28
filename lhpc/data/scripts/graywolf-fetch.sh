#!/usr/bin/env bash
# Fetch the pinned upstream graywolf .deb and unpack it into the runtime root — ROOTLESS.
#
# graywolf is not in the Debian archive (no repo candidate on Trixie), so it cannot be a
# plain apt dependency; and LHPC has no root, so `apt install ./graywolf_*.deb` is not
# available to it either. A .deb is just an ar archive, and `dpkg-deb -x` unpacks one as an
# ordinary user, so the binaries can live in the runtime root like every other managed
# artifact — no root, no system package, no vendored rebuild.
#
#   usage: graywolf-fetch.sh <dest-dir> <version> [--from-upstream]
#
# The dest dir ends up containing the package's own layout, so the binaries are at
# <dest-dir>/usr/bin/{graywolf,graywolf-modem}.
#
# Two sha256 sources, both verified before anything is unpacked:
#   default        the version is in the checksum table below — the reproducible pin the image
#                  build and every auto-install use, so shipped installs are a known hash.
#   --from-upstream  the hash comes from that release's OWN `checksums.txt` (same GitHub
#                  release, same HTTPS origin) — how a one-click "update to a newer graywolf"
#                  verifies a version not yet in the table. Same integrity against the network
#                  as apt/pip trusting a registry's published hashes.
set -euo pipefail

DEST="${1:?usage: graywolf-fetch.sh <dest-dir> <version> [--from-upstream]}"
VERSION="${2:?usage: graywolf-fetch.sh <dest-dir> <version> [--from-upstream]}"
FROM_UPSTREAM=""
[ "${3:-}" = "--from-upstream" ] && FROM_UPSTREAM=1

BASE_URL="https://github.com/chrissnell/graywolf/releases/download"

# sha256 of the upstream release assets, from the release's own checksums.txt.
#   key: <version>/<debian-arch>
sums() {
    case "$1" in
        0.14.13/arm64) echo "1c85cb35e8ffbf364aa7fccc4b62f2c344e847739f4323ba5fac663de7167ed7" ;;
        0.14.13/armhf) echo "4885291f0138c9417ffef415d0f473c451d68742ccdd4f9cb3951dcc5fd933fa" ;;
        0.14.13/amd64) echo "660e79f0d0779575fb049506dc05a1d96799c697a0c94598cbf7eaf64dd98360" ;;
        0.14.12/arm64) echo "8fb64987dcd51fbb0d4311a2dc13eec6577b1701385b2b7051270bb813f370ac" ;;
        0.14.12/armhf) echo "b3c4772bf89b4075587ec18dc81b0805f084cce4ce4a91ddadbd68ce7a4c06d0" ;;
        0.14.12/amd64) echo "bfc9307b8016e077a506f937929e9a17bbc0a75f72b606e19ce5a7407800a404" ;;
        *) return 1 ;;
    esac
}

die() { echo "[graywolf-fetch] $*" >&2; exit 1; }

command -v dpkg-deb >/dev/null || die "dpkg-deb not found (ships with dpkg)"
command -v curl     >/dev/null || die "curl not found"
command -v sha256sum >/dev/null || die "sha256sum not found"

ARCH="$(dpkg --print-architecture)"
DEB="graywolf_${VERSION}_${ARCH}.deb"
URL="${BASE_URL}/v${VERSION}/${DEB}"

EXPECTED="$(sums "${VERSION}/${ARCH}" || true)"
if [ -z "$EXPECTED" ] && [ -n "$FROM_UPSTREAM" ]; then
    # Operator-opted upstream install: derive the sha256 from the release's own checksums.txt
    # (same release, same HTTPS origin as the .deb). A line is "<sha256>  <filename>"; match
    # the exact .deb name so a rename or a partial file cannot smuggle a different hash in.
    CK_URL="${BASE_URL}/v${VERSION}/checksums.txt"
    echo "[graywolf-fetch] upstream mode: reading ${CK_URL}"
    CK="$(curl -fsSL --retry 3 --retry-delay 2 "$CK_URL" || true)"
    [ -n "$CK" ] || die "could not fetch upstream checksums.txt for graywolf ${VERSION}"
    # Strip a trailing CR (CRLF-published checksums.txt) before matching the exact .deb name,
    # so a legitimate update is not aborted by line endings; a rename still cannot smuggle a
    # different hash in (the name must match exactly), and sha256sum -c re-verifies below.
    EXPECTED="$(printf '%s\n' "$CK" | awk -v f="$DEB" '
        { sub(/\r$/, "", $2) }
        $2==f || $2=="*"f || $2=="./"f {print $1; exit}')"
    [ -n "$EXPECTED" ] || die "upstream checksums.txt has no entry for ${DEB}"
    echo "[graywolf-fetch] upstream sha256 for ${DEB}: ${EXPECTED}"
fi
[ -n "$EXPECTED" ] || die "no recorded sha256 for graywolf ${VERSION} on ${ARCH} — add it to this script's table (upstream publishes arm64/armhf/amd64), or pass --from-upstream to trust the release's own checksums.txt"
DEST="${DEST%/}"
STAMP="${DEST}/.lhpc-graywolf-version"

# ALREADY AT THIS VERSION -> do nothing. A rebuild must not need the network when the pinned
# binary is already unpacked: an offline box (or a GitHub outage) would otherwise lose a
# working install to a failed download.
if [ -x "${DEST}/usr/bin/graywolf" ] && [ -x "${DEST}/usr/bin/graywolf-modem" ] \
   && [ "$(cat "$STAMP" 2>/dev/null || true)" = "${VERSION} ${ARCH}" ]; then
    echo "[graywolf-fetch] already at ${VERSION} (${ARCH}) — nothing to do"
    exit 0
fi

# The parent must exist BEFORE mktemp places the work dir beside DEST: on a box that has never
# built another build/tools component, {runtime}/build/tools does not exist yet and mktemp
# would fail with nothing but a template error.
mkdir -p "$(dirname "$DEST")"

# Work in a sibling temp dir so a failed or half-finished fetch can never be mistaken for a
# complete install, and so the swap into place is a rename rather than a partial overwrite.
WORK="$(mktemp -d "${DEST}.fetch.XXXXXX")"
OLD=""
# Restore a moved-aside install on ANY exit path — an interrupted or failed swap must never
# leave the box with no graywolf at all.
cleanup() {
    if [ -n "$OLD" ] && [ -d "$OLD" ] && [ ! -e "$DEST" ]; then
        mv -T "$OLD" "$DEST" 2>/dev/null || true
    fi
    rm -rf "$WORK" "${OLD:-/nonexistent-nothing}"
}
trap cleanup EXIT

echo "[graywolf-fetch] ${DEB} (${ARCH}) <- ${URL}"
curl -fsSL --retry 3 --retry-delay 2 -o "${WORK}/${DEB}" "$URL" \
    || die "download failed: $URL"

echo "${EXPECTED}  ${WORK}/${DEB}" | sha256sum -c - >/dev/null 2>&1 \
    || die "sha256 MISMATCH for ${DEB} — refusing to unpack (expected ${EXPECTED}, got $(sha256sum "${WORK}/${DEB}" | cut -d' ' -f1))"
echo "[graywolf-fetch] sha256 verified"

dpkg-deb -x "${WORK}/${DEB}" "${WORK}/root" || die "dpkg-deb -x failed"

for b in graywolf graywolf-modem; do
    [ -x "${WORK}/root/usr/bin/${b}" ] || die "unpacked package has no executable usr/bin/${b}"
done

# Record the version INSIDE the tree that is about to be swung in, so the stamp can never
# describe a different tree than the one on disk.
printf '%s %s\n' "$VERSION" "$ARCH" > "${WORK}/root/.lhpc-graywolf-version"

# Replace atomically. `mv -T` throughout: a plain `mv` onto an existing directory would nest
# the new tree INSIDE it and leave bin/run_argv pointing at nothing.
if [ -e "$DEST" ] || [ -L "$DEST" ]; then
    OLD="${DEST}.old.$$"
    mv -T "$DEST" "$OLD" || die "could not move the existing ${DEST} aside"
fi
if ! mv -T "${WORK}/root" "$DEST"; then
    die "could not move the unpacked tree into ${DEST}"   # the EXIT trap restores $OLD
fi

echo "[graywolf-fetch] ready: ${DEST}/usr/bin/graywolf (${VERSION} ${ARCH})"
