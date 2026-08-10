#!/usr/bin/env bash
# Fetch the pinned upstream graywolf .deb and unpack it into the runtime root — ROOTLESS.
#
# graywolf is not in the Debian archive (no repo candidate on Trixie), so it cannot be a
# plain apt dependency; and LHPC has no root, so `apt install ./graywolf_*.deb` is not
# available to it either. A .deb is just an ar archive, and `dpkg-deb -x` unpacks one as an
# ordinary user, so the binaries can live in the runtime root like every other managed
# artifact — no root, no system package, no vendored rebuild.
#
#   usage: graywolf-fetch.sh <dest-dir> <version>
#
# The dest dir ends up containing the package's own layout, so the binaries are at
# <dest-dir>/usr/bin/{graywolf,graywolf-modem}.
#
# Fail-closed by construction: the version must be known to the checksum table below, the
# architecture must be one upstream publishes, and the download must match the recorded
# sha256 before anything is unpacked. A new version means a new table entry — that is the
# point, because it makes the pin reviewable in the diff.
set -euo pipefail

DEST="${1:?usage: graywolf-fetch.sh <dest-dir> <version>}"
VERSION="${2:?usage: graywolf-fetch.sh <dest-dir> <version>}"

BASE_URL="https://github.com/chrissnell/graywolf/releases/download"

# sha256 of the upstream release assets, from the release's own checksums.txt.
#   key: <version>/<debian-arch>
sums() {
    case "$1" in
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
EXPECTED="$(sums "${VERSION}/${ARCH}" || true)"
[ -n "$EXPECTED" ] || die "no recorded sha256 for graywolf ${VERSION} on ${ARCH} — add it to this script's table (upstream publishes arm64/armhf/amd64)"

DEB="graywolf_${VERSION}_${ARCH}.deb"
URL="${BASE_URL}/v${VERSION}/${DEB}"
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
