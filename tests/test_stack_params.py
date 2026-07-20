"""Start-confirm 'Stack parameters' panel + CALL/node enforcement + ephemeral file overrides."""

from __future__ import annotations

import pytest

from lhpc.core.config import save_operator_config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

from conftest import set_call


def _svc(tmp_path):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _hold_lock_unpublished(root: str, key: str) -> None:
    """Hold ONLY the flock, never publishing an owner record — the unidentifiable-holder state.
    Module-level so `spawn` can pickle it; `spawn` (not fork) avoids the Py3.13
    fork-in-threaded-process warning the suite gates on."""
    import fcntl
    import time as _t
    from lhpc.core import reslock, runtime_fs
    from lhpc.core.paths import Paths as _P
    paths = _P(runtime_root=__import__("pathlib").Path(root))
    lockfile = paths.under("state", "locks", reslock.canonical_key(key) + ".lock")
    fh = runtime_fs.open_lock(paths, lockfile)
    fcntl.flock(fh, fcntl.LOCK_EX)
    _t.sleep(60)


def _lock_is_held(svc, key: str) -> bool:
    """True when the flock is taken, independently of whether ownership was published."""
    import fcntl
    from lhpc.core import reslock, runtime_fs
    path = svc._paths.under("state", "locks", reslock.canonical_key(key) + ".lock")
    try:
        fh = runtime_fs.open_lock(svc._paths, path)
    except OSError:
        return False
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()


# --- identity detection -------------------------------------------------------

def test_identity_field_map(tmp_path):
    svc = _svc(tmp_path)
    exp = {"igate": ("call", "run", "licensed"), "chat": ("call", "file", "licensed"),
           "voice": ("callsign", "file", "licensed"), "meshcom": ("mc_callsign", "run", "licensed"),
           "meshtastic": ("node_name", "run", "unlicensed"),
           "meshcore": ("node_name", "file", "unlicensed")}
    for tgt, (name, kind, enforce) in exp.items():
        idf = svc._identity_field(tgt)
        assert idf and (idf["name"], idf["kind"], idf["enforce"]) == (name, kind, enforce)
    assert svc._identity_field("kiss") is None            # no callsign/node
    assert svc._identity_field("daemon") is None


# --- enforcement --------------------------------------------------------------

def test_licensed_refuses_empty_and_n0call(tmp_path):
    svc = _svc(tmp_path)
    assert svc.enforce_identity("igate")[0] is False                       # empty operator callsign
    assert svc.enforce_identity("igate", params={"call": ""})[0] is False
    assert svc.enforce_identity("igate", params={"call": "N0CALL"})[0] is False
    assert svc.enforce_identity("igate", params={"call": "n0call-1"})[0] is False
    assert svc.enforce_identity("igate", params={"call": "DJ0CHE-10"})[0] is True


def test_licensed_default_uses_operator_callsign(tmp_path):
    svc = _svc(tmp_path)
    save_operator_config(svc._paths, "DJ0CHE"); svc._invalidate_config()
    assert svc.enforce_identity("igate")[0] is True       # default {callsign} -> DJ0CHE


def test_unlicensed_requires_nonempty_but_accepts_default(tmp_path):
    svc = _svc(tmp_path)
    # meshtastic node_name default "LoRaHAM Pi" (non-empty) -> accepted even with no callsign
    assert svc.enforce_identity("meshtastic")[0] is True
    assert svc.enforce_identity("meshtastic", params={"node_name": ""})[0] is False
    assert svc.enforce_identity("meshtastic", params={"node_name": "N0CALL"})[0] is True  # not licensed


def test_meshcore_file_node_uses_override(tmp_path):
    svc = _svc(tmp_path)
    assert svc.enforce_identity("meshcore")[0] is False                    # default {callsign} empty
    assert svc.enforce_identity("meshcore", file_over={"node_name": "MyNode"})[0] is True


# --- the panel view -----------------------------------------------------------

def test_stack_start_params_shapes(tmp_path):
    svc = _svc(tmp_path)
    rows = svc.stack_start_params("igate")
    idr = [r for r in rows if r["is_identity"]]
    assert len(idr) == 1 and idr[0]["name"] == "call" and idr[0]["field"] == "p_call"
    assert svc.stack_start_params("daemon") == []                          # daemon excluded
    # voice exposes its file-config params with pf_ fields
    vrows = svc.stack_start_params("voice")
    assert any(r["field"].startswith("pf_") for r in vrows)
    assert any(r["field"] == "pf_callsign" and r["is_identity"] for r in vrows)


def test_stack_start_params_prefill_and_override(tmp_path):
    svc = _svc(tmp_path)
    # an ephemeral run override is reflected as the row value; config_value stays the saved value
    rows = svc.stack_start_params("igate", params={"tx_freq": "434.500"})
    r = next(x for x in rows if x["name"] == "tx_freq")
    assert r["value"] == "434.500" and r["config_value"] == "433.900"
    # a file override is reflected too
    vrows = svc.stack_start_params("voice", file_over={"callsign": "XX1XX"})
    assert next(x for x in vrows if x["name"] == "callsign")["value"] == "XX1XX"


# --- ephemeral file-override normalization + precedence -----------------------

def test_normalize_file_overrides_validates(tmp_path):
    svc = _svc(tmp_path)
    clean, err = svc._normalize_file_overrides("voice", {"callsign": "DJ0CHE", "sf": "9"})
    assert not err and clean["callsign"] == "DJ0CHE" and clean["sf"] == "9"
    _c, err2 = svc._normalize_file_overrides("voice", {"callsign": "bad call!"})
    assert err2                                                            # invalid -> typed error
    _u, uerr = svc._normalize_file_overrides("voice", {"unknown": "x"})
    assert uerr                                                            # unknown -> typed error


def test_blank_nonflag_file_override_is_skipped(tmp_path):
    # Start-page regression: leaving the meshcore Frequency field empty must NOT fail int validation
    # — a blank non-flag override is treated as absent so the selected RF preset owns the frequency.
    svc = _svc(tmp_path)
    assert svc._normalize_file_overrides("meshcore", {"frequency": ""}) == ({}, "")   # skipped, no error
    ok, err = svc._normalize_file_overrides("meshcore", {"frequency": "869618000"})
    assert not err and ok["frequency"] == "869618000"                     # real value still validated/kept
    assert svc._normalize_file_overrides("meshcore", {"frequency": "abc"})[1]   # bad value -> typed error
    # a flag is NOT skipped when blank (blank flag = off, still an explicit override)
    assert svc._normalize_file_overrides("meshcore", {"enable_tx": ""}) == ({"enable_tx": ""}, "")


def test_meshcore_preset_owns_frequency_for_all_presets(tmp_path):
    # Blank frequency default -> the generated meshcore config sets the chosen preset and writes NO
    # frequency override, so lorahaminterface uses that preset's frequency (eu_uk_long/medium 869.525,
    # eu_uk_narrow 869.618 — the T-Deck's). An explicit override still writes an active line.
    svc = _svc(tmp_path)
    base = tmp_path / "src" / "meshcore-pi" / "examples" / "config-loraham868.toml"
    base.parent.mkdir(parents=True)
    base.write_text('[interface.loraham868]\npreset = "eu_uk_medium"\n'
                    "# frequency = 869525000\n# sf = 11\n"
                    '[device.companion]\nname = "N0CALL"\n')
    gen = tmp_path / "config" / "files" / "meshcore-pi.toml"
    for preset in ("eu_uk_long", "eu_uk_medium", "eu_uk_narrow"):
        res = svc.write_config_files("meshcore", overrides={"preset": preset})
        assert any(w.status == "written" for w in res), [(w.component, w.status, w.detail) for w in res]
        out = gen.read_text()
        assert f'preset = "{preset}"' in out                               # preset selected
        assert not any(ln.strip().startswith("frequency ") or ln.strip().startswith("frequency=")
                       for ln in out.splitlines())                          # NO active frequency override
        assert "# frequency = 869525000" in out                            # commented example remains
    svc.write_config_files("meshcore", overrides={"preset": "eu_uk_narrow", "frequency": "869618000"})
    out = gen.read_text()
    assert any(ln.strip() == "frequency = 869618000" for ln in out.splitlines())   # explicit override writes it


def test_start_blocks_licensed_without_call_backstop(tmp_path):
    # Direct/CLI start (authoritative) refuses a licensed stack with no callsign, carrying the
    # field to highlight; nothing is launched.
    svc = _svc(tmp_path)
    res = svc.start("igate", apply=True)
    assert not res.ok and "callsign" in res.summary.lower()
    assert res.data.get("enforce_field") == "p_call"


def test_start_licensed_with_call_passes_enforcement(tmp_path):
    svc = _svc(tmp_path)
    set_call(svc)
    res = svc.start("igate", apply=True)                                   # not blocked by enforcement
    assert "callsign is required" not in res.summary


def test_param_groups_required_on_top_then_by_component(tmp_path):
    svc = _svc(tmp_path)
    groups = svc.stack_start_param_groups("meshcom")
    assert groups[0]["header"] == "Required"
    assert [r["name"] for r in groups[0]["rows"]] == ["mc_callsign"]   # identity pulled to top
    headers = [g["header"] for g in groups]
    assert "MeshCom GPS relay" in headers                              # a per-component group
    # the identity field is NOT duplicated inside its component group
    qemu = next(g for g in groups if g["header"] == "MeshCom QEMU node")
    assert "mc_callsign" not in [r["name"] for r in qemu["rows"]]
    # a stack with no identity still groups by component (no "Required" group)
    assert all(g["header"] != "Required" for g in svc.stack_start_param_groups("kiss"))


def test_same_process_claim_waits_then_succeeds(tmp_path):
    # Two overlapping controller ops in DIFFERENT threads of the SAME process that share a claim
    # must SERIALIZE (wait), not fail with "your own stack is busy". A different-process holder
    # still fails fast (covered by reslock's external-contention tests).
    import threading, time, contextlib
    from lhpc.core import reslock
    svc = _svc(tmp_path)
    svc._SELF_LOCK_WAIT_S = 3.0
    key = "claim.loraham.daemon-socket.433"
    held = threading.Event()
    released = threading.Event()
    def hold():
        with reslock.operation_lock(svc._paths, key, "stop", "meshcom"):
            # Signal only once the lock is BOTH taken and its ownership PUBLISHED — a bare sleep
            # here made the test assume a window it never verified, so under load the contender
            # could arrive before publication and the run flaked.
            for _ in range(500):
                if reslock.read_owner(svc._paths, key):
                    break
                time.sleep(0.002)
            held.set()
            time.sleep(0.4)
            released.set()
    t = threading.Thread(target=hold); t.start()
    assert held.wait(5.0), "holder never published its ownership record"
    with contextlib.ExitStack() as st:
        svc._acquire_key(st, key, "start", "kiss")        # waits for the same-process holder
        assert released.is_set()                          # proved it waited past the release
    t.join()


def test_same_process_claim_retries_while_ownership_is_unpublished(tmp_path, monkeypatch):
    """REGRESSION: `operation_lock` takes the flock and only THEN writes its `.owner` record. A
    second same-process thread arriving inside that window got a ResourceBusy whose holder was
    unidentifiable, was treated as an EXTERNAL conflict, and failed immediately instead of
    serializing — intermittently, and most often under load (i.e. exactly when two controller
    threads overlap). Here publication is deliberately delayed to make that window deterministic."""
    import threading, time, contextlib
    from lhpc.core import reslock, runtime_fs
    svc = _svc(tmp_path)
    svc._SELF_LOCK_WAIT_S = 3.0
    key = "claim.loraham.daemon-socket.433"
    flocked = threading.Event()
    publish_now = threading.Event()
    released = threading.Event()

    real_write_marker = runtime_fs.write_marker

    def slow_publish(paths, path, text, *a, **k):
        # Only the OWNER record of this key is delayed; every other marker write is untouched.
        if path.name.endswith(".owner"):
            flocked.set()
            publish_now.wait(5.0)
        return real_write_marker(paths, path, text, *a, **k)

    monkeypatch.setattr(runtime_fs, "write_marker", slow_publish)

    def hold():
        with reslock.operation_lock(svc._paths, key, "stop", "meshcom"):
            time.sleep(0.2)
            released.set()

    t = threading.Thread(target=hold); t.start()
    try:
        assert flocked.wait(5.0), "holder never reached the publication window"
        # The flock IS held and the owner record does NOT exist yet — the ambiguous state.
        assert reslock.read_owner(svc._paths, key) is None
        contender = {}
        def acquire():
            try:
                with contextlib.ExitStack() as st:
                    svc._acquire_key(st, key, "start", "kiss")
                    contender["ok"] = released.is_set()      # serialized behind the holder
            except reslock.ResourceBusy as exc:
                contender["busy"] = str(exc)
        c = threading.Thread(target=acquire); c.start()
        time.sleep(0.05)                                     # contender is now inside the grace
        publish_now.set()                                    # ownership becomes visible
        c.join(10.0)
    finally:
        publish_now.set()
        t.join(10.0)
    assert "busy" not in contender, f"retried window still reported busy: {contender}"
    assert contender.get("ok") is True, contender


def test_unknown_owner_still_fails_after_the_bounded_grace(tmp_path, monkeypatch):
    """An UNIDENTIFIABLE holder must not become a five-second stall: it is retried only for the
    short publication grace and then surfaces the typed ResourceBusy. Proven with a lock held by a
    real external process whose owner record never appears."""
    import contextlib, time
    import multiprocessing as mp
    import pytest as _pytest
    from lhpc.core import reslock
    svc = _svc(tmp_path)
    svc._SELF_LOCK_WAIT_S = 5.0
    key = "claim.loraham.daemon-socket.433"
    proc = mp.get_context("spawn").Process(target=_hold_lock_unpublished,
                                           args=(str(tmp_path), key))
    proc.start()
    try:
        for _ in range(500):                                 # wait for the flock, NOT the owner
            if _lock_is_held(svc, key):
                break
            time.sleep(0.02)
        assert _lock_is_held(svc, key), "external holder never took the lock"
        assert reslock.read_owner(svc._paths, key) is None   # deliberately never published
        started = time.monotonic()
        with _pytest.raises(reslock.ResourceBusy):
            with contextlib.ExitStack() as st:
                svc._acquire_key(st, key, "start", "kiss")
        waited = time.monotonic() - started
        # bounded by the grace, nowhere near the same-process budget
        assert waited < svc._SELF_LOCK_WAIT_S / 2, f"waited {waited:.2f}s — grace not bounded"
    finally:
        proc.terminate(); proc.join(10)
        if proc.is_alive():
            proc.kill(); proc.join()


@pytest.mark.needs_session  # spawns a real process; identity_complete needs sid>0 (skips under sid==0)
def test_component_booting_tracks_live_post_runner(tmp_path):
    # A running component reads 'booting' while its post-start (--setcall) runner is still alive,
    # then flips to normal once the runner finishes.
    import subprocess, time
    svc = _svc(tmp_path)
    life = svc._lifecycle()
    assert svc._component_booting("meshcom-qemu") is False           # no runner
    p = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        for _ in range(50):
            idn = life._capture_identity(p.pid)
            if idn and idn.get("exec") == "sleep":
                break
            time.sleep(0.05)
        comp, stack = svc.stack("meshcom").component("meshcom-qemu"), svc.stack("meshcom")
        life.record_launch(stack, comp, p.pid, "", ident=life._capture_identity(p.pid), role="post")
        assert svc._component_booting("meshcom-qemu") is True          # runner alive -> booting
    finally:
        p.terminate(); p.wait()
    time.sleep(0.3)
    assert svc._component_booting("meshcom-qemu") is False             # runner gone -> ready


# --- direct-component identity/config scope + ephemeral run-param normalization --------------

class _Seam(Exception):
    """Raised at the first lifecycle side effect (daemon ensure / config write) — proves whether a
    start reached the seam or was blocked BEFORE any side effect."""


def _seam_svc(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    def seam(*a, **k):
        raise _Seam()
    monkeypatch.setattr(svc, "_ensure_daemon", seam)
    monkeypatch.setattr(svc, "write_config_files", seam)
    return svc


@pytest.mark.parametrize("call", ["", "N0CALL", "N0CALL-1"])
def test_direct_licensed_component_rejects_bad_call_before_side_effects(tmp_path, monkeypatch, call):
    svc = _seam_svc(tmp_path, monkeypatch)
    res = svc._start_impl("meshcom-qemu", apply=True, params={"mc_callsign": call})  # no _Seam
    assert not res.ok and res.data.get("enforce_field") == "p_mc_callsign"
    assert "callsign" in res.summary.lower()


def test_direct_unlicensed_component_rejects_empty_node_before_side_effects(tmp_path, monkeypatch):
    svc = _seam_svc(tmp_path, monkeypatch)
    # No operator callsign -> node_name default {callsign} resolves empty -> enforcement blocks it
    # (before any side effect / _Seam).
    res = svc._start_impl("meshcore-pi", apply=True)
    assert not res.ok and res.data.get("enforce_field") == "pf_node_name"


def test_direct_valid_identity_reaches_start_seam(tmp_path, monkeypatch):
    svc = _seam_svc(tmp_path, monkeypatch)
    with pytest.raises(_Seam):                                             # enforcement passed
        svc._start_impl("meshcom-qemu", apply=True, params={"mc_callsign": "DJ0CHE-3"})


def test_direct_file_identity_uses_owner_stack_persisted_and_ephemeral(tmp_path):
    svc = _svc(tmp_path)
    svc.save_config_bundle("meshcore", values={"file_node_name": "SavedNode"}, band="868")
    assert svc._identity_value("meshcore-pi", "868", None, None) == "SavedNode"   # owner-stack value
    assert svc.enforce_identity("meshcore-pi", "868")[0] is True
    assert svc._identity_value("meshcore-pi", "868", None, {"node_name": "EphNode"}) == "EphNode"


def test_direct_unknown_file_override_fails_typed(tmp_path):
    svc = _svc(tmp_path)
    assert svc._normalize_file_overrides("meshcore-pi", {"nope": "x"})[1]          # unknown -> typed
    # a param from a SIBLING component is unknown to a direct component target
    assert svc._normalize_file_overrides("meshcom-qemu", {"node_name": "x"})[1]


def test_invalid_ordinary_run_param_rejected_before_lifecycle(tmp_path, monkeypatch):
    svc = _seam_svc(tmp_path, monkeypatch)
    res = svc._start_impl("igate", apply=True,
                          params={"call": "DJ0CHE-10", "tx_freq": "not-a-frequency"})
    assert not res.ok and "invalid parameter" in res.summary                       # no _Seam


def test_unknown_ordinary_run_param_rejected(tmp_path, monkeypatch):
    svc = _seam_svc(tmp_path, monkeypatch)
    res = svc._start_impl("igate", apply=True, params={"call": "DJ0CHE-10", "nope": "x"})
    assert not res.ok and "unknown parameter" in res.summary


def test_non_mapping_run_params_rejected(tmp_path, monkeypatch):
    svc = _seam_svc(tmp_path, monkeypatch)
    res = svc._start_impl("igate", apply=True, params="not-a-dict")
    assert not res.ok and "must be a mapping" in res.summary


def test_stack_target_and_daemon_behavior_unchanged(tmp_path):
    # Stack targets keep whole-stack scope; the daemon stays identity-exempt.
    svc = _svc(tmp_path)
    assert svc._identity_field("daemon") is None
    assert svc._identity_field("meshcom") == svc._identity_field("meshcom-qemu")   # same field
    assert {r["name"] for r in svc.stack_start_params("meshcom")} >= \
           {r["name"] for r in svc.stack_start_params("meshcom-qemu")}             # stack ⊇ component


# --- Area 1: run-param normalization BEFORE public start lock planning -----------------------

def _lock_seam_svc(tmp_path, monkeypatch):
    """A service whose lock-planning seams (`_daemon_needs`, `_lifecycle_guard`) raise — so a start
    that reaches lock/radio planning trips the seam, and one blocked earlier does not."""
    svc = _svc(tmp_path)
    def seam(*a, **k):
        raise _Seam()
    monkeypatch.setattr(svc, "_daemon_needs", seam)
    monkeypatch.setattr(svc, "_lifecycle_guard", seam)
    return svc


def test_public_start_non_mapping_params_returns_typed_before_lock(tmp_path, monkeypatch):
    svc = _lock_seam_svc(tmp_path, monkeypatch)
    res = svc.start("daemon", apply=True, params="not-a-dict")             # must NOT raise
    assert res.ok is False and "must be a mapping" in res.summary          # no _Seam reached


def test_public_start_unknown_params_fail_before_lock(tmp_path, monkeypatch):
    svc = _lock_seam_svc(tmp_path, monkeypatch)
    res = svc.start("igate", apply=True, params={"nope": "x"})
    assert res.ok is False and "unknown parameter" in res.summary


def test_public_start_invalid_radio_fails_before_lock(tmp_path, monkeypatch):
    svc = _lock_seam_svc(tmp_path, monkeypatch)
    res = svc.start("daemon", apply=True, params={"radio": "999"})         # invalid daemon radio
    assert res.ok is False and "invalid parameter" in res.summary


def test_public_start_valid_params_reach_lock_seam(tmp_path, monkeypatch):
    svc = _lock_seam_svc(tmp_path, monkeypatch)
    with pytest.raises(_Seam):                                             # canonical values -> planning
        svc.start("daemon", apply=True, params={"radio": "433"})


# --- Area 1: restart preflight BEFORE lock planning / stop ----------------------------------

def _restart_lock_seam(tmp_path, monkeypatch):
    """Seams for the public restart lock-planning path (`_daemon_needs`, `_lifecycle_guard`)."""
    svc = _svc(tmp_path)
    def seam(*a, **k):
        raise _Seam()
    monkeypatch.setattr(svc, "_daemon_needs", seam)
    monkeypatch.setattr(svc, "_lifecycle_guard", seam)
    return svc


def test_public_restart_non_mapping_params_typed_no_lock(tmp_path, monkeypatch):
    svc = _restart_lock_seam(tmp_path, monkeypatch)
    res = svc.restart("igate", apply=True, params="not-a-dict")            # must NOT raise
    assert res.ok is False and "must be a mapping" in res.summary          # no _Seam / no stop


def test_public_restart_unknown_and_invalid_radio_no_lock(tmp_path, monkeypatch):
    svc = _restart_lock_seam(tmp_path, monkeypatch)
    assert svc.restart("igate", apply=True, params={"nope": "x"}).ok is False
    assert svc.restart("daemon", apply=True, params={"radio": "999"}).ok is False  # invalid daemon radio


def test_public_restart_invalid_file_override_no_lock(tmp_path, monkeypatch):
    svc = _restart_lock_seam(tmp_path, monkeypatch)
    assert svc.restart("voice", apply=True, file_overrides={"unknown": "x"}).ok is False
    assert svc.restart("voice", apply=True, file_overrides={"freq": "not-a-freq"}).ok is False


def test_public_restart_invalid_identity_no_lock(tmp_path, monkeypatch):
    svc = _restart_lock_seam(tmp_path, monkeypatch)
    res = svc.restart("igate", apply=True, params={"call": "N0CALL"})
    assert res.ok is False and res.data.get("enforce_field") == "p_call"


def test_public_restart_valid_reaches_lock_seam(tmp_path, monkeypatch):
    svc = _restart_lock_seam(tmp_path, monkeypatch)
    with pytest.raises(_Seam):                                             # preflight passed
        svc.restart("igate", apply=True, params={"call": "DJ0CHE-10"})


def test_restart_impl_validates_before_its_stop(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    def seam(*a, **k):
        raise _Seam()
    monkeypatch.setattr(svc, "stop", seam)                                 # stop() is the seam
    # invalid inputs -> typed failure BEFORE stop()
    assert svc._restart_impl("igate", apply=True, params={"nope": "x"}).ok is False
    assert svc._restart_impl("igate", apply=True, params={"call": "N0CALL"}).ok is False
    with pytest.raises(_Seam):                                             # valid -> reaches stop()
        svc._restart_impl("igate", apply=True, params={"call": "DJ0CHE-10"})


# --- Area 2: direct component targets use the OWNER stack for persistence --------------------

def test_direct_component_daemon_params_use_owner_stack(tmp_path):
    from lhpc.core import config as cfgmod, daemon_params as dp
    svc = _svc(tmp_path)
    assert svc._has_daemon_params("meshcom-qemu") and svc._has_daemon_params("meshcore-pi")
    nd = "9" if dp.default_value("meshcom", "433", "SF") != "9" else "8"
    assert svc.save_daemon_params("meshcom-qemu", "433", {"SF": nd}).ok
    assert cfgmod.load_stack_config(svc._paths, "meshcom").get("dp_433_SF") == nd   # OWNER stack
    assert cfgmod.load_stack_config(svc._paths, "meshcom-qemu") == {}               # NOT the component
    assert svc._daemon_param_overrides("meshcom-qemu", "433") == {"SF": nd}         # read back via comp
    sf = next(r for pnl in svc.daemon_start_panels("meshcom-qemu")
              for r in pnl["rows"] if r["name"] == "SF")
    assert sf["value"] == nd                                                        # panel shows owner value
    assert svc.save_daemon_params("meshcore-pi", "868", {"CADWAIT": "1234"}).ok
    assert cfgmod.load_stack_config(svc._paths, "meshcore").get("dp_868_CADWAIT") == "1234"


def test_direct_component_config_save_owner_scope(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("meshcom-qemu", values={"mc_callsign": "DJ0CHE-3"}).ok
    owner_cfg = cfgmod.load_stack_config(svc._paths, "meshcom")                              # OWNER file
    assert owner_cfg.get("__r__meshcom-qemu__mc_callsign") == "DJ0CHE-3"                     # COMPONENT-scoped key
    assert "mc_callsign" not in owner_cfg                                                    # not a flat key
    assert cfgmod.load_stack_config(svc._paths, "meshcom-qemu") == {}                        # NOT component file
    assert svc.stack_config("meshcom-qemu").get("mc_callsign") == "DJ0CHE-3"                 # later read resolves
    # a direct component may edit ONLY its own fields — sibling/unknown/autostart/remotes rejected
    assert svc.save_config_bundle("meshcom-qemu", values={"port": "7000"}).ok is False       # sibling field
    assert svc.save_config_bundle("meshcom-qemu",
                                  values={"autostart_meshcom-gps-relay": "on"}).ok is False
    assert svc.save_config_bundle("meshcom-qemu", values={},
                                  remotes={"meshcom-qemu": "https://x/y.git"}).ok is False
    assert svc.save_config_bundle("meshcom",                                                 # stack: unchanged
                                  values={"autostart_meshcom-gps-relay": "on"}).ok


# --- Area 1: saved config stays STABLE across an applied start/restart -----------------------

def _exclusive_available(paths) -> bool:
    """True iff the EXCLUSIVE config lock is free (a config SAVE could proceed right now). False
    means a start/restart holds the SHARED stability guard and a save would BLOCK."""
    import fcntl
    from lhpc.core import runtime_fs
    fh = runtime_fs.open_lock(paths, paths.under("config", ".lock"))
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fh, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        fh.close()


def test_config_guard_held_across_applied_start(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    set_call(svc)                                                  # valid persisted call
    assert _exclusive_available(svc._paths) is True               # free before the start
    seen = {}
    def spy(*a, **k):
        seen["exclusive"] = _exclusive_available(svc._paths)       # inside the start (after identity)
        seen["call"] = svc.stack_config("igate").get("call")
        raise _Seam()
    monkeypatch.setattr(svc, "_ensure_daemon", spy)
    with pytest.raises(_Seam):
        svc.start("igate", apply=True)
    assert seen["exclusive"] is False                             # a save would BLOCK mid-start
    assert seen["call"] == "DJ0CHE"                               # config read is the stable snapshot
    assert _exclusive_available(svc._paths) is True               # released afterwards


def test_direct_start_impl_and_restart_impl_hold_config_guard(tmp_path, monkeypatch):
    from lhpc.core.services import ActionResult
    svc = _svc(tmp_path)
    set_call(svc)
    held = {}
    def spy(*a, **k):
        held["v"] = _exclusive_available(svc._paths)
        raise _Seam()
    monkeypatch.setattr(svc, "_ensure_daemon", spy)
    with pytest.raises(_Seam):
        svc._start_impl("igate", apply=True)                     # DIRECT internal call
    assert held["v"] is False                                    # guard held — cannot be bypassed
    monkeypatch.setattr(svc, "stop", lambda *a, **k: ActionResult(True, "stopped"))
    held.clear()
    with pytest.raises(_Seam):
        svc._restart_impl("igate", apply=True)                   # DIRECT internal restart
    assert held["v"] is False


def test_competing_save_blocks_until_start_completes_then_succeeds(tmp_path, monkeypatch):
    import threading, time
    svc = _svc(tmp_path)
    set_call(svc)
    done = threading.Event()
    def spy(*a, **k):
        threading.Thread(target=lambda: (svc.save_config_bundle("igate", values={"call": "DJ0XYZ"}),
                                         done.set())).start()
        time.sleep(0.3)
        assert not done.is_set()                                 # competing save BLOCKED during start
        assert svc.stack_config("igate").get("call") == "DJ0CHE" # generation would read the stable value
        raise _Seam()
    monkeypatch.setattr(svc, "_ensure_daemon", spy)
    with pytest.raises(_Seam):
        svc.start("igate", apply=True)
    done.wait(3)                                                  # after the guard released, save runs
    assert done.is_set() and svc.stack_config("igate").get("call") == "DJ0XYZ"


def test_restart_not_stopped_then_failed_by_concurrent_invalid_save(tmp_path, monkeypatch):
    import threading, time
    from lhpc.core.services import ActionResult
    svc = _svc(tmp_path)
    svc.save_config_bundle("igate", values={"call": "DJ0CHE-5"})  # valid persisted call
    stops = []
    monkeypatch.setattr(svc, "stop",
                        lambda *a, **k: (stops.append(1), ActionResult(True, "stopped"))[1])
    saved = threading.Event()
    def spy(*a, **k):
        # a competing save flipping the call to N0CALL must be BLOCKED for the whole restart, so the
        # restart's start still sees the VALID call — it never stops then rejects the target.
        threading.Thread(target=lambda: (svc.save_config_bundle("igate", values={"call": "N0CALL"}),
                                         saved.set())).start()
        time.sleep(0.3)
        assert not saved.is_set()
        assert svc.stack_config("igate").get("call") == "DJ0CHE-5"
        raise _Seam()
    monkeypatch.setattr(svc, "_ensure_daemon", spy)
    with pytest.raises(_Seam):
        svc.restart("igate", apply=True)
    assert stops == [1]                                           # stopped ONCE (reached the start)
    saved.wait(3)
    assert saved.is_set()                                        # invalid save applied only AFTER restart


# --- Area 2: direct component file-config generation stays COMPONENT-scoped ------------------

_SCOPE_MANIFEST = '''
[[stack]]
id = "ostack"
name = "Owner Stack"
main = "tgt"
[[stack.component]]
id = "tgt"
name = "Target"
kind = "service"
run = "true"
readiness = "process"
depends_on = ["dep"]
  [[stack.component.param]]
  name = "shared"
  kind = "str"
  default = "tgt-run"
  [stack.component.config_file]
  path = "{runtime}/config/files/tgt.conf"
  fmt = "env"
    [[stack.component.config_file.param]]
    name = "tval"
    key = "TVAL"
    default = "tdefault"
[[stack.component]]
id = "dep"
name = "Dependency"
kind = "service"
run = "true"
readiness = "process"
  [[stack.component.param]]
  name = "shared"
  kind = "str"
  default = "dep-run"
  [stack.component.config_file]
  path = "{runtime}/config/files/dep.conf"
  fmt = "env"
    [[stack.component.config_file.param]]
    name = "dval"
    key = "DVAL"
    default = "ddefault"
[[stack.component]]
id = "sib"
name = "Sibling"
kind = "service"
run = "true"
readiness = "process"
optional = true
  [stack.component.config_file]
  path = "{runtime}/config/files/sib.conf"
  fmt = "env"
    [[stack.component.config_file.param]]
    name = "sval"
    key = "SVAL"
    default = "sdefault"
'''


def _scope_svc(tmp_path):
    m = tmp_path / "scope.toml"; m.write_text(_SCOPE_MANIFEST)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "files").mkdir(parents=True, exist_ok=True)
    return ControllerService(manifest_path=m, system=FakeSystem().system,
                             paths=Paths(runtime_root=tmp_path))


def _run_scoped_start(svc, target, monkeypatch, **kw):
    from lhpc.core.lifecycle import Lifecycle, StartLaunch
    # Stub the actual launch (no real process) so the start exercises config generation only.
    monkeypatch.setattr(Lifecycle, "start",
                        lambda self, stack, comp, cfg, band="": StartLaunch(True, "log", ""))
    return svc.start(target, apply=True, **kw)


def test_direct_start_generates_only_started_components_scoped(tmp_path, monkeypatch):
    svc = _scope_svc(tmp_path)
    _run_scoped_start(svc, "tgt", monkeypatch, file_overrides={"tval": "TXX"})
    files = tmp_path / "config" / "files"
    assert (files / "tgt.conf").exists()                          # target config generated
    assert (files / "dep.conf").exists()                          # dependency config generated
    assert not (files / "sib.conf").exists()                      # sibling NEVER written
    assert "TVAL=TXX" in (files / "tgt.conf").read_text()         # target ephemeral reaches target
    dep_txt = (files / "dep.conf").read_text()
    assert "DVAL=ddefault" in dep_txt                             # dependency uses its OWN default
    assert "TVAL" not in dep_txt and "TXX" not in dep_txt         # target override never leaks to dep


def test_direct_start_run_params_component_scoped_no_collision(tmp_path, monkeypatch):
    # `shared` exists on BOTH tgt and dep; the target's ephemeral value must not leak into the
    # dependency's launch config (component-scoped comp_cfg).
    svc = _scope_svc(tmp_path)
    seen = {}
    from lhpc.core.lifecycle import Lifecycle, StartLaunch
    def stub(self, stack, comp, cfg, band=""):
        seen[comp.id] = dict(cfg)
        return StartLaunch(True, "log", "")
    monkeypatch.setattr(Lifecycle, "start", stub)
    svc.start("tgt", apply=True, params={"shared": "EPHEMERAL"})
    assert seen["tgt"].get("shared") == "EPHEMERAL"               # target gets the ephemeral value
    assert seen["dep"].get("shared") == "dep-run"                 # dependency keeps its OWN default


def test_stack_start_keeps_whole_stack_generation(tmp_path, monkeypatch):
    svc = _scope_svc(tmp_path)
    _run_scoped_start(svc, "ostack", monkeypatch, file_overrides={"tval": "TZZ", "sval": "SZZ"})
    files = tmp_path / "config" / "files"
    # a stack start includes the non-optional components (tgt + dep); the ephemeral applies to each
    assert "TVAL=TZZ" in (files / "tgt.conf").read_text()
    assert (files / "dep.conf").exists()


def test_direct_persistence_writes_only_allowed_owner_fields(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _scope_svc(tmp_path)
    assert svc.save_config_bundle("tgt", values={"shared": "keepme"}).ok
    owner_cfg = cfgmod.load_stack_config(svc._paths, "ostack")                       # owner store
    assert owner_cfg.get("__r__tgt__shared") == "keepme"                             # component-scoped key
    assert "shared" not in owner_cfg                                                 # never a flat key
    assert cfgmod.load_stack_config(svc._paths, "tgt") == {}                         # not component-named
    assert svc.save_config_bundle("tgt", values={"dval": "x"}).ok is False           # sibling field rejected


# --- Component-scoped persisted run/file keys (collision-free) -------------------------------

_SCOPE2_MANIFEST = '''
[[stack]]
id = "ostack2"
name = "Owner Two"
main = "tgt"
[[stack.component]]
id = "tgt"
name = "Target"
kind = "service"
run = "true"
readiness = "process"
depends_on = ["dep"]
  [[stack.component.param]]
  name = "rp"
  kind = "str"
  default = "rp-tgt"
  [[stack.component.param]]
  name = "uniq"
  kind = "str"
  default = "uniq-tgt"
  [stack.component.config_file]
  path = "{runtime}/config/files/tgt.conf"
  fmt = "env"
    [[stack.component.config_file.param]]
    name = "fp"
    key = "FP"
    default = "fp-tgt"
[[stack.component]]
id = "dep"
name = "Dependency"
kind = "service"
run = "true"
readiness = "process"
  [[stack.component.param]]
  name = "rp"
  kind = "str"
  default = "rp-dep"
  [stack.component.config_file]
  path = "{runtime}/config/files/dep.conf"
  fmt = "env"
    [[stack.component.config_file.param]]
    name = "fp"
    key = "FP"
    default = "fp-dep"
[[stack.component]]
id = "sib"
name = "Sibling"
kind = "service"
run = "true"
readiness = "process"
optional = true
  [stack.component.config_file]
  path = "{runtime}/config/files/sib.conf"
  fmt = "env"
    [[stack.component.config_file.param]]
    name = "sp"
    key = "SP"
    default = "sp-sib"
'''


def _scope2_svc(tmp_path):
    m = tmp_path / "scope2.toml"; m.write_text(_SCOPE2_MANIFEST)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "files").mkdir(parents=True, exist_ok=True)
    return ControllerService(manifest_path=m, system=FakeSystem().system,
                             paths=Paths(runtime_root=tmp_path))


def _capture_start(svc, monkeypatch):
    """Stub the launch to capture each component's resolved launch config; config files still get
    generated for real."""
    from lhpc.core.lifecycle import Lifecycle, StartLaunch
    seen = {}
    def stub(self, stack, comp, cfg, band=""):
        seen[comp.id] = dict(cfg)
        return StartLaunch(True, "log", "")
    monkeypatch.setattr(Lifecycle, "start", stub)
    return seen


def _seed_flat(svc, stack_id, values):
    from lhpc.core import config as cfgmod
    cfgmod.update_stack_config(svc._paths, stack_id, values)


def test_scoped_values_isolate_target_and_dependency(tmp_path, monkeypatch):
    svc = _scope2_svc(tmp_path)
    # distinct component-scoped values for target and dependency (same param names)
    assert svc.save_config_bundle("tgt", values={"rp": "RP-T", "file_fp": "FP-T"}).ok
    assert svc.save_config_bundle("dep", values={"rp": "RP-D", "file_fp": "FP-D"}).ok
    seen = _capture_start(svc, monkeypatch)
    svc.start("tgt", apply=True)                                  # direct start of tgt (+dep)
    assert seen["tgt"]["rp"] == "RP-T" and seen["dep"]["rp"] == "RP-D"   # argv per component
    files = tmp_path / "config" / "files"
    assert "FP=FP-T" in (files / "tgt.conf").read_text()
    assert "FP=FP-D" in (files / "dep.conf").read_text()
    assert not (files / "sib.conf").exists()                     # sibling never generated


def test_only_target_scoped_dependency_uses_defaults(tmp_path, monkeypatch):
    svc = _scope2_svc(tmp_path)
    assert svc.save_config_bundle("tgt", values={"rp": "RP-T", "file_fp": "FP-T"}).ok
    seen = _capture_start(svc, monkeypatch)
    svc.start("tgt", apply=True)
    assert seen["tgt"]["rp"] == "RP-T"
    assert seen["dep"]["rp"] == "rp-dep"                         # dependency DEFAULT, never target's
    assert "FP=fp-dep" in (tmp_path / "config" / "files" / "dep.conf").read_text()


def test_ephemeral_applies_only_to_target(tmp_path, monkeypatch):
    svc = _scope2_svc(tmp_path)
    seen = _capture_start(svc, monkeypatch)
    svc.start("tgt", apply=True, params={"rp": "EPH"})
    assert seen["tgt"]["rp"] == "EPH"
    assert seen["dep"]["rp"] == "rp-dep"                         # ephemeral never leaks to dependency


def test_stack_start_honors_scoped_and_unique_flat(tmp_path, monkeypatch):
    svc = _scope2_svc(tmp_path)
    assert svc.save_config_bundle("tgt", values={"rp": "RP-T"}).ok       # scoped (direct)
    assert svc.save_config_bundle("dep", values={"rp": "RP-D"}).ok       # scoped (direct)
    assert svc.save_config_bundle("ostack2", values={"uniq": "U-FLAT"}).ok   # stack -> flat legacy
    assert _cfg_has_flat(svc, "uniq")                                    # stack save stays flat
    seen = _capture_start(svc, monkeypatch)
    svc.start("ostack2", apply=True)                                     # whole-stack start
    assert seen["tgt"]["rp"] == "RP-T" and seen["dep"]["rp"] == "RP-D"   # scoped honored per component
    assert seen["tgt"]["uniq"] == "U-FLAT"                               # unique flat legacy honored


def test_ambiguous_flat_legacy_fails_typed_before_any_seam(tmp_path, monkeypatch):
    from lhpc.core.lifecycle import Lifecycle
    svc = _scope2_svc(tmp_path)
    _seed_flat(svc, "ostack2", {"rp": "LEGACY"})                 # rp declared by tgt AND dep -> ambiguous
    def boom(*a, **k):
        raise _Seam()
    monkeypatch.setattr(svc, "write_config_files", boom)         # config-write seam
    monkeypatch.setattr(Lifecycle, "start", boom)               # spawn seam
    res = svc.start("tgt", apply=True)                           # must NOT raise
    assert res.ok is False and "ambiguous" in res.summary        # typed failure before any seam


def test_unique_flat_legacy_is_backward_compatible(tmp_path, monkeypatch):
    svc = _scope2_svc(tmp_path)
    _seed_flat(svc, "ostack2", {"uniq": "LEGACY-U"})            # uniq declared only by tgt -> unique
    seen = _capture_start(svc, monkeypatch)
    svc.start("tgt", apply=True)
    assert seen["tgt"]["uniq"] == "LEGACY-U"                     # unique flat legacy still applied


def _cfg_has_flat(svc, key):
    from lhpc.core import config as cfgmod
    return key in cfgmod.load_stack_config(svc._paths, "ostack2")


# --- component identity through the whole stack-target parameter pipeline --------------------

_ID_COLLIDE_MANIFEST = '''
[[stack]]
id = "ids"
name = "Id Stack"
main = "tgt"
[[stack.component]]
id = "tgt"
name = "Target"
kind = "service"
run = "true"
readiness = "process"
depends_on = ["dep"]
  [[stack.component.param]]
  name = "call"
  kind = "str"
  validator = "callsign"
  default = ""
[[stack.component]]
id = "dep"
name = "Dependency"
kind = "service"
run = "true"
readiness = "process"
  [[stack.component.param]]
  name = "call"
  kind = "str"
  validator = "callsign"
  default = ""
'''


def _id_collide_svc(tmp_path):
    m = tmp_path / "ids.toml"; m.write_text(_ID_COLLIDE_MANIFEST)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    return ControllerService(manifest_path=m, system=FakeSystem().system,
                             paths=Paths(runtime_root=tmp_path))


def test_qualified_ephemeral_applies_per_component(tmp_path, monkeypatch):        # (3)
    svc = _scope2_svc(tmp_path)
    seen = _capture_start(svc, monkeypatch)
    svc.start("ostack2", apply=True, params={"tgt.rp": "E-T", "dep.rp": "E-D"})
    assert seen["tgt"]["rp"] == "E-T" and seen["dep"]["rp"] == "E-D"              # each to its component


def test_unqualified_dup_ephemeral_fails_before_locks(tmp_path, monkeypatch):    # (4)
    svc = _scope2_svc(tmp_path)
    def boom(*a, **k):
        raise _Seam()
    monkeypatch.setattr(svc, "_config_stable", boom)                             # config-stability seam
    res = svc.start("ostack2", apply=True, params={"rp": "X"})                   # unqualified duplicate
    assert res.ok is False and "multiple components" in res.summary              # typed, no _Seam


def test_identity_selected_component_not_masked_by_sibling(tmp_path, monkeypatch):  # (5)
    from lhpc.core.lifecycle import Lifecycle
    svc = _id_collide_svc(tmp_path)
    def boom(*a, **k):
        raise _Seam()
    monkeypatch.setattr(svc, "write_config_files", boom)
    monkeypatch.setattr(Lifecycle, "start", boom)
    # the SELECTED licensed field (tgt.call) is N0CALL; a later same-named component (dep.call) is
    # valid — the start must still BLOCK on tgt, before any lifecycle side effect (no _Seam).
    res = svc.start("ids", apply=True, params={"tgt.call": "N0CALL", "dep.call": "DJ0CHE-1"})
    assert res.ok is False and "callsign" in res.summary.lower()
    assert res.data.get("enforce_field") == "p_tgt__call"                        # selected component's field


def test_qualified_identity_valid_reaches_start_seam(tmp_path, monkeypatch):     # (6)
    from lhpc.core.lifecycle import Lifecycle
    svc = _id_collide_svc(tmp_path)
    def boom(*a, **k):
        raise _Seam()
    monkeypatch.setattr(Lifecycle, "start", boom)                               # controlled non-hardware seam
    with pytest.raises(_Seam):
        svc.start("ids", apply=True, params={"tgt.call": "DJ0CHE-5", "dep.call": "DJ0CHE-6"})


def test_unique_name_stack_stays_bare_no_regression(tmp_path):                   # (7)
    svc = _svc(tmp_path)
    rows = svc.stack_start_params("voice")                                       # voice: unique names
    for r in rows:
        assert "__" not in r["field"] and "." not in r["key"]                    # bare fields/keys preserved
    # a bare ephemeral for a unique name still works (igate's tx_freq)
    clean, err = svc._normalize_run_params("igate", {"tx_freq": "434.500"})
    assert not err and clean == {"tx_freq": "434.500"}


# --- permanent Config page: component-aware (collision fixture) ------------------------------

def test_config_view_identity_and_values_per_component(tmp_path):
    svc = _id_collide_svc(tmp_path)
    svc.save_config_bundle("ids", values={"tgt.call": "N0CALL", "dep.call": "DJ0CHE-1"})
    cv = svc.config_view("ids")
    tgt = next(c for c in cv["components"] if c["id"] == "tgt")
    dep = next(c for c in cv["components"] if c["id"] == "dep")
    # the SELECTED licensed component's own (invalid) value is shown independently of a later valid one
    assert tgt["values"]["call"] == "N0CALL" and dep["values"]["call"] == "DJ0CHE-1"
    assert tgt["fields"]["call"] == "c_tgt__call" and dep["fields"]["call"] == "c_dep__call"


def test_config_unique_fields_stay_bare_and_flat(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    for f in svc.config_param_fields("igate"):
        assert "__" not in f["field"] and "." not in f["key"]        # bare fields/keys preserved
    assert svc.save_stack_config("igate", {"call": "DJ0CHE-9"}).ok    # canonical delegate
    assert cfgmod.load_stack_config(svc._paths, "igate").get("call") == "DJ0CHE-9"   # flat key


def test_save_stack_config_rejects_unqualified_dup_and_unknown(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _scope2_svc(tmp_path)
    r1 = svc.save_stack_config("ostack2", {"rp": "X"})               # unqualified duplicate
    assert r1.ok is False and "multiple components" in "; ".join(r1.details)
    r2 = svc.save_stack_config("ostack2", {"nope": "Y"})            # unknown field
    assert r2.ok is False and "unknown config field" in "; ".join(r2.details)
    assert cfgmod.load_stack_config(svc._paths, "ostack2") == {}     # NO mutation on rejection
    assert svc.save_stack_config("ostack2", {"tgt.rp": "RP-T"}).ok  # valid qualified persists scoped
    assert cfgmod.load_stack_config(svc._paths, "ostack2").get("__r__tgt__rp") == "RP-T"


# --- overrides-only config storage (enables automatic self-update config preservation) ------

def test_config_stores_overrides_only(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    p = next(pp for pp in svc.run_params_for("igate") if pp.name == "tx_freq")
    default = svc._param_default_canon(p, "", "")
    # saving the current default persists NOTHING, yet the effective value is still the default
    assert svc.save_config_bundle("igate", values={"tx_freq": default}).ok
    assert "tx_freq" not in cfgmod.load_stack_config(svc._paths, "igate")
    assert svc.stack_config("igate")["tx_freq"] == default
    # saving a real override persists it and survives reload
    assert svc.save_config_bundle("igate", values={"tx_freq": "434.500"}).ok
    assert cfgmod.load_stack_config(svc._paths, "igate").get("tx_freq") == "434.500"
    assert svc.stack_config("igate")["tx_freq"] == "434.500"
    # saving it back to the default clears the stored override again
    assert svc.save_config_bundle("igate", values={"tx_freq": default}).ok
    assert "tx_freq" not in cfgmod.load_stack_config(svc._paths, "igate")


def test_value_at_old_default_follows_new_default(tmp_path):
    # Simulate a self-update that changes a manifest default: a value stored while it equalled the
    # OLD default must resolve to the NEW default; a genuine override must be preserved.
    from lhpc.core import config as cfgmod
    man = tmp_path / "m.toml"
    man.write_text(
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        '  [[stack.component.param]]\n  name="opt"\n  kind="str"\n  default="OLD"\n'
    )
    svc = ControllerService(manifest_path=man, system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path))
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    # user leaves it at the (old) default -> nothing stored
    assert svc.save_config_bundle("s", values={"opt": "OLD"}).ok
    assert cfgmod.load_stack_config(svc._paths, "s") == {}
    # "update" to a manifest whose default changed OLD -> NEW
    man.write_text(man.read_text().replace('default="OLD"', 'default="NEW"'))
    svc2 = ControllerService(manifest_path=man, system=FakeSystem().system,
                             paths=Paths(runtime_root=tmp_path))
    assert svc2.stack_config("s")["opt"] == "NEW"                 # at-old-default -> follows new default
    # a genuine override is preserved across the same update
    assert svc2.save_config_bundle("s", values={"opt": "MINE"}).ok
    man.write_text(man.read_text().replace('default="NEW"', 'default="NEWER"'))
    svc3 = ControllerService(manifest_path=man, system=FakeSystem().system,
                             paths=Paths(runtime_root=tmp_path))
    assert svc3.stack_config("s")["opt"] == "MINE"               # override preserved


def test_empty_non_default_override_is_kept(tmp_path):
    # A value that DIFFERS from a non-empty default but is empty ("unset") is a genuine override and
    # must still persist (two-phase write must not drop it).
    from lhpc.core import config as cfgmod
    man = tmp_path / "m.toml"
    man.write_text(
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        '  [[stack.component.param]]\n  name="opt"\n  kind="str"\n  default="D"\n'
    )
    svc = ControllerService(manifest_path=man, system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path))
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    assert svc.save_config_bundle("s", values={"opt": ""}).ok
    assert cfgmod.load_stack_config(svc._paths, "s").get("opt") == ""   # empty override persisted
    assert svc.stack_config("s")["opt"] == ""
