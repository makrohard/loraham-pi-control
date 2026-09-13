"""The flagship chain: fake daemon -> REAL kiss TNC -> (REAL graywolf where dpkg-deb
exists) with real frame round-trips both directions, driven end-to-end through the real
executable and the real HTTP console. Slow (network clone + gcc build) — one ordered
module holding the built chain, torn down at the end."""
from __future__ import annotations

import json
import shutil
import socket
import time

import pytest
from lhpc_testlab.release import (
    GRAYWOLF_UI,
    KISS_TCP,
    MESHCORE_COMPANION,
    wait_http,
    wait_tcp,
)
from lhpc_testlab.testing import run_lab, run_lhpc


def _kiss_serving() -> bool:
    """True when kiss's KISS/TCP listener accepts a connection RIGHT NOW — a one-shot functional
    liveness probe that does not depend on lhpc's /proc-based status."""
    try:
        socket.create_connection(("127.0.0.1", KISS_TCP), timeout=5).close()
        return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def chain(lab):
    """Install+build+start kiss over the fake daemon; stop everything afterwards."""
    run_lhpc(lab.env, "install", "kiss", "--yes", check=True, timeout=600)
    run_lhpc(lab.env, "build", "kiss", "--yes", check=True, timeout=600)
    run_lhpc(lab.env, "stack", "start", "kiss", "--yes", check=True, timeout=300)
    yield lab
    run_lhpc(lab.env, "stack", "stop", "kiss", "--yes", timeout=300)
    run_lhpc(lab.env, "stack", "stop", "daemon", "--yes", timeout=300)


@pytest.mark.slow
def test_kiss_rx_and_tx_round_trip(chain):
    """Injected APRS frame -> fake daemon framed RX -> real kiss TNC -> valid AX.25/KISS
    on TCP 8001; the same frame written back is a TX the fake daemon captures."""
    s = socket.create_connection(("127.0.0.1", KISS_TCP), timeout=10)
    s.settimeout(15)
    run_lab(chain.env, "inject", "433", "aprs-position", check=True)
    data = s.recv(1024)
    assert data[:2] == b"\xc0\x00" and data.endswith(b"\xc0")     # KISS data frame
    assert bytes(b >> 1 for b in data[2:8]).startswith(b"APDR16")  # AX.25 dest
    assert bytes(b >> 1 for b in data[9:15]).startswith(b"DL0LAB")
    assert b"Test Lab mobile" in data
    tx_log = chain.root / "state" / "testlab" / "tx.jsonl"
    before = tx_log.read_text().count("\n") if tx_log.exists() else 0
    s.sendall(data)                                # loop it back as a transmit
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        lines = tx_log.read_text().splitlines() if tx_log.exists() else []
        if len(lines) > before:
            break
        time.sleep(0.3)
    assert len(lines) > before
    last = json.loads(lines[-1])
    assert last["result"] == "OK" and "DL0LAB-9>APDR16" in last["text"]
    s.close()


@pytest.mark.slow
def test_kiss_stop_restart_and_status(chain):
    run_lhpc(chain.env, "stack", "restart", "kiss", "--yes", check=True, timeout=300)
    assert wait_tcp(KISS_TCP, 120), "kiss not serving on 8001 after restart"


@pytest.mark.slow
def test_graywolf_shows_injected_station(chain):
    """Real graywolf consumes the chain and its REAL web UI shows the injected
    station. Needs dpkg-deb (Debian containers — the devcontainer/CI always has it)."""
    if not shutil.which("dpkg-deb"):
        pytest.skip("dpkg-deb not installed (graywolf fetch unpacks a .deb)")
    import urllib.error
    import urllib.request
    run_lhpc(chain.env, "install", "graywolf", "--yes", check=True, timeout=900)
    run_lhpc(chain.env, "build", "graywolf", "--yes", check=True, timeout=900)
    run_lhpc(chain.env, "stack", "start", "graywolf", "--yes", check=True, timeout=300)
    try:
        assert wait_http(f"http://127.0.0.1:{GRAYWOLF_UI}/", 120, accept=(200, 401, 403)), \
            "graywolf's UI never answered on 8080"
        # authenticate exactly like the production provision step: the generated admin
        # password lives under state/graywolf (0600), sessions ride a cookie jar
        import http.cookiejar
        pw = (chain.root / "state" / "graywolf" / "graywolf-admin.txt") \
            .read_text().strip()
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        login = urllib.request.Request(
            f"http://127.0.0.1:{GRAYWOLF_UI}/api/auth/login",
            data=json.dumps({"username": "admin", "password": pw}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        opener.open(login, timeout=10).read()
        run_lab(chain.env, "inject", "433", "aprs-position", check=True)
        # The pinned graywolf's packet list. One endpoint, polled until the frame has travelled
        # the chain; a 404 is retried (the API is not up before the UI is), any other HTTP error
        # is graywolf refusing or breaking and must surface, not be retried into a timeout.
        found = False
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not found:
            try:
                raw = opener.open(f"http://127.0.0.1:{GRAYWOLF_UI}/api/packets?limit=50",
                                  timeout=5).read()
                found = b"DL0LAB" in raw
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
            except OSError:
                pass
            time.sleep(2)
        assert found, "injected station never appeared in graywolf's packet list"
    finally:
        run_lhpc(chain.env, "stack", "stop", "graywolf", "--yes", timeout=300)


@pytest.mark.slow
def test_meshcore_real_process_over_fake_868(chain):
    """The real MeshCore host (python venv) starts against the fake 868 daemon and serves its
    companion port, and stops verified — the second real stack family on the chain. The
    release lane proves the same start at release time; this runs on every push."""
    run_lhpc(chain.env, "install", "meshcore", "--yes", check=True, timeout=900)
    run_lhpc(chain.env, "build", "meshcore", "--yes", check=True, timeout=900)
    r = run_lhpc(chain.env, "stack", "start", "meshcore", "--yes", timeout=300)
    try:
        assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-500:])
        assert wait_tcp(MESHCORE_COMPANION, 180), "MeshCore companion never opened TCP 5000"
    finally:
        run_lhpc(chain.env, "stack", "stop", "meshcore", "--yes", timeout=300)


@pytest.mark.slow
def test_meshcom_starts_on_the_prebuilt_image(chain):
    """meshcom's emulated radio (qemu-system-xtensa) is baked into the lab image; where
    the binary exists the REAL stack builds its firmware and starts. Elsewhere the
    production requirement gate refuses with its own hint — skip with that truth."""
    # Both are baked into the lab image; requiring BOTH keeps a skewed/older image a
    # SKIP (image publishes race the lane on the same push) instead of a red lane.
    import os
    import platform
    if platform.machine() not in ("aarch64", "arm64"):
        pytest.skip("meshcom uses our aarch64 binary (lhpc-binaries) — needs an ARM lab")
    if os.environ.get("LHPC_CHAIN_HEAVY") != "1":
        pytest.skip("meshcom ESP32 boot is opt-in (LHPC_CHAIN_HEAVY=1) — validate on real "
                    "aarch64 hardware; reset already installed the binary")
    # reset already installed meshcom via --source binary; just release 433 and start.
    run_lhpc(chain.env, "stack", "stop", "kiss", "--yes", timeout=300)
    r = run_lhpc(chain.env, "stack", "start", "meshcom", "--yes", timeout=600)
    try:
        assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-500:])
    finally:
        run_lhpc(chain.env, "stack", "stop", "meshcom", "--yes", timeout=300)


@pytest.mark.slow
def test_simulated_reboot_restores_running_stacks(chain):
    """With kiss running, a simulated reboot advances the boot id AND brings
    the previously-running stacks back (no operator-stop tombstone)."""
    # Verify kiss by its REAL endpoint (the KISS/TCP listener on 8001), not by lhpc's
    # status string: proof-based /proc ownership is unreliable under nested-qemu (a running
    # stack can read as "stopped"), so a functional probe is the faithful check on every
    # arch. Precondition — bring the chain up operator-style: the fake daemon (provider)
    # FIRST, then kiss (lhpc does not auto-cascade providers; earlier tests here consume the
    # same daemon on 868 and leave it stopped). Only (re)start when kiss is not already
    # serving, so we never spawn a duplicate that collides on port 8001.
    if not _kiss_serving():
        run_lhpc(chain.env, "stack", "start", "daemon", "--yes", timeout=200)
        run_lhpc(chain.env, "stack", "start", "kiss", "--yes", timeout=300)
    assert wait_tcp(KISS_TCP, 120), "kiss not serving on 127.0.0.1:8001 before reboot"
    before = (chain.root / "state" / "testlab" / "host" / "boot_id").read_text().strip()
    run_lab(chain.env, "_power", "--kind", "reboot", check=True, timeout=300)
    after = (chain.root / "state" / "testlab" / "host" / "boot_id").read_text().strip()
    assert after != before                                   # boot advanced
    # reboot must not OPERATOR-stop kiss — a reboot is not an explicit stop, so it writes
    # no per-stack tombstone for kiss (boot-restore may then bring it back). Check kiss
    # specifically: the stop-intent/ dir may hold other stacks' tombstones (e.g. meshcore,
    # operator-stopped by an earlier test in this shared module).
    assert not (chain.root / "state" / "stop-intent" / "kiss.json").exists(), \
        "reboot wrongly left a kiss operator-stop tombstone"
    assert wait_tcp(KISS_TCP, 90), "kiss not serving on 127.0.0.1:8001 after reboot (restore)"
