"""The plugin manager as part of the repeater: what the host spawns, the marker protocol that
makes an unclean death safe, the one clean/unclean rule, and the bounded shutdown order.
Pure-Python fakes for the child process; the real upstream package only where marked
(skipped when openhop_repeater is not installed in this interpreter)."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess

import pytest

from meshcore_host import plugin_manager as pm

BOOT_A = "aaaaaaaa-0000-0000-0000-000000000001"
BOOT_B = "bbbbbbbb-0000-0000-0000-000000000002"


class FakeProc:
    """A Popen stand-in: `rc` is what poll()/wait() will report once `finish()` was called or
    after the given number of polls; `on_terminate` decides whether SIGTERM ends it."""

    def __init__(self, pid=4242, *, rc=None, exits_on_terminate=True, dies_on_kill=True):
        self.pid = pid
        self.returncode = None
        self._rc = rc
        self._exits_on_terminate = exits_on_terminate
        self._dies_on_kill = dies_on_kill
        self.signals: list[str] = []
        self.waits: list[float | None] = []

    def finish(self, rc: int) -> None:
        self.returncode = rc

    def poll(self):
        return self.returncode

    def terminate(self):
        self.signals.append("TERM")
        if self._exits_on_terminate:
            self.returncode = 0 if self._rc is None else self._rc

    def kill(self):
        self.signals.append("KILL")
        if self._dies_on_kill:
            self.returncode = -9

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return self.returncode


def _child(tmp_path, monkeypatch, *, proc=None, boot=BOOT_A, popen=None, state_dir=None):
    """A PluginManagerChild over a fake upstream resolver (no openhop_repeater needed)."""
    sd = state_dir or (tmp_path / "state" / "openhop")
    monkeypatch.setattr(pm, "paths_for",
                        lambda d: (pm.Path(d) / "plugins", pm.Path(d) / "plugin-manager.sock"))
    calls: list[dict] = []
    proc = proc or FakeProc()

    def factory(argv, **kw):
        calls.append({"argv": list(argv), **kw})
        if popen is not None:
            return popen(argv, **kw)
        return proc

    child = pm.PluginManagerChild(sd, popen_factory=factory, boot_id_fn=lambda: boot,
                                  python="/venv/bin/python")
    return child, calls, proc


def _marker(child):
    return json.loads(child.marker.read_text())


# --- argv and spawn shape ------------------------------------------------------------------

def test_argv_is_the_upstream_module_with_absolute_root_and_socket_no_config_no_cwd(tmp_path, monkeypatch):
    child, calls, _ = _child(tmp_path, monkeypatch)
    assert child.start()
    assert calls[0]["argv"] == ["/venv/bin/python", "-m", "repeater.plugins",
                                "--plugins-root", str(tmp_path / "state" / "openhop" / "plugins"),
                                "--socket", str(tmp_path / "state" / "openhop" / "plugin-manager.sock"),
                                "--log-level", "INFO"]
    assert "--config" not in calls[0]["argv"]
    assert "cwd" not in calls[0]                          # a first-ever start has no state dir yet
    assert "env" not in calls[0]                          # the host's environment, unchanged
    assert calls[0]["start_new_session"] is False          # stays in LHPC's process group


def test_a_missing_state_dir_does_not_stop_the_spawn(tmp_path, monkeypatch):
    sd = tmp_path / "fresh" / "state" / "openhop"
    assert not sd.exists()
    child, calls, _ = _child(tmp_path, monkeypatch, state_dir=sd)
    assert child.start() and calls                          # the marker write made the dir
    assert child.marker.exists()


@pytest.mark.parametrize("length", [60, 200])
def test_paths_agree_with_the_dashboards_resolver_including_the_long_path_fallback(tmp_path, length):
    """The dashboard resolves the socket for the same storage dir; a runtime root long enough to
    push the socket past AF_UNIX's limit lands BOTH on the same hashed temp-dir socket."""
    pytest.importorskip("repeater.plugins.storage")
    from repeater.web.plugin_endpoints import PluginAPIEndpoints
    state_dir = tmp_path / ("x" * length) / "state" / "openhop"
    root, sock = pm.paths_for(state_dir)
    dashboard = PluginAPIEndpoints(config={"storage": {"storage_dir": str(state_dir)}})
    assert sock == dashboard._socket_path()
    assert root == state_dir.resolve() / "plugins"
    if length == 200:
        assert not str(sock).startswith(str(state_dir))    # the fallback kicked in, for both


# --- the marker protocol, one test per row ---------------------------------------------------

def test_absent_marker_is_written_before_the_spawn_with_boot_id_only(tmp_path, monkeypatch):
    seen = {}

    def popen(argv, **kw):
        seen["marker_at_spawn"] = json.loads(
            (tmp_path / "state" / "openhop" / pm.MARKER_NAME).read_text())
        return FakeProc()
    child, _, _ = _child(tmp_path, monkeypatch, popen=popen)
    assert child.start()
    assert seen["marker_at_spawn"] == {"version": 1, "boot_id": BOOT_A}   # no pid, ever


def test_same_boot_marker_refuses_to_spawn(tmp_path, monkeypatch, caplog):
    child, calls, _ = _child(tmp_path, monkeypatch)
    pm.write_marker(child.marker, BOOT_A)
    assert not child.start() and calls == []
    assert "unclean" in caplog.text and "rebooted" in caplog.text
    assert _marker(child)["boot_id"] == BOOT_A            # left as found


def test_other_boot_marker_is_replaced_and_the_manager_starts(tmp_path, monkeypatch):
    child, calls, _ = _child(tmp_path, monkeypatch)
    pm.write_marker(child.marker, BOOT_B)
    assert child.start() and len(calls) == 1
    assert _marker(child) == {"version": 1, "boot_id": BOOT_A}


@pytest.mark.parametrize("content", [b"not json", b"[]", b'{"version": 2, "boot_id": "x"}',
                                     b'{"version": 1}', b'{"version": 1, "boot_id": ""}',
                                     b"\xff\xfe"])
def test_malformed_marker_refuses_and_is_left_untouched(tmp_path, monkeypatch, content, caplog):
    child, calls, _ = _child(tmp_path, monkeypatch)
    child.marker.parent.mkdir(parents=True)
    child.marker.write_bytes(content)
    assert not child.start() and calls == []
    assert child.marker.read_bytes() == content
    assert "cannot prove" in caplog.text


def test_unreadable_marker_refuses(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    child, calls, _ = _child(tmp_path, monkeypatch)
    pm.write_marker(child.marker, BOOT_B)
    child.marker.chmod(0)
    try:
        assert not child.start() and calls == []
    finally:
        child.marker.chmod(0o600)


def test_empty_boot_id_refuses(tmp_path, monkeypatch):
    child, calls, _ = _child(tmp_path, monkeypatch, boot="")
    assert not child.start() and calls == []


def test_marker_write_failure_refuses_to_spawn(tmp_path, monkeypatch, caplog):
    if os.geteuid() == 0:
        pytest.skip("root writes everywhere")
    sd = tmp_path / "state" / "openhop"
    sd.mkdir(parents=True)
    sd.chmod(0o500)
    try:
        child, calls, _ = _child(tmp_path, monkeypatch, state_dir=sd)
        assert not child.start() and calls == []
        assert "could not write its marker" in caplog.text
    finally:
        sd.chmod(0o700)


def test_spawn_failure_is_a_subsystem_failure_and_clears_the_marker(tmp_path, monkeypatch, caplog):
    def popen(argv, **kw):
        raise OSError("no such interpreter")
    child, _, _ = _child(tmp_path, monkeypatch, popen=popen)
    assert not child.start()                                # no exception: the repeater continues
    assert not child.marker.exists()                        # nothing runs: no ownership recorded
    assert "plugins stay offline" in caplog.text


# --- the one clean / unclean rule -------------------------------------------------------------

def test_clean_exit_under_our_sigterm_clears_the_marker(tmp_path, monkeypatch):
    proc = FakeProc(exits_on_terminate=True)
    child, _, _ = _child(tmp_path, monkeypatch, proc=proc)
    assert child.start()
    child.stop()
    assert proc.signals == ["TERM"] and proc.waits == [pm.TERM_GRACE_S]
    assert not child.marker.exists()


def test_timeout_kill_keeps_the_marker(tmp_path, monkeypatch, caplog):
    proc = FakeProc(exits_on_terminate=False, dies_on_kill=True)
    child, _, _ = _child(tmp_path, monkeypatch, proc=proc)
    assert child.start()
    child.stop()
    assert proc.signals == ["TERM", "KILL"] and proc.waits == [pm.TERM_GRACE_S, pm.KILL_GRACE_S]
    assert child.marker.exists() and "rebooted" in caplog.text


def test_nonzero_exit_keeps_the_marker(tmp_path, monkeypatch, caplog):
    proc = FakeProc(rc=3)
    child, _, _ = _child(tmp_path, monkeypatch, proc=proc)
    assert child.start()
    child.stop()
    assert child.marker.exists() and "code 3" in caplog.text


def test_an_exit_the_watcher_sees_applies_the_same_rule(tmp_path, monkeypatch, caplog):
    proc = FakeProc()
    child, _, _ = _child(tmp_path, monkeypatch, proc=proc)
    assert child.start()
    monkeypatch.setattr(pm, "WATCH_POLL_S", 0.001)

    async def go():
        task = asyncio.get_running_loop().create_task(child.watch())
        await asyncio.sleep(0.01)
        proc.finish(-15)                                     # died to a signal, not ours
        await asyncio.wait_for(task, 2)
    asyncio.run(go())
    assert child.marker.exists() and "code -15" in caplog.text and "rebooted" in caplog.text
    # and a clean exit seen by the watcher clears it — once, even if stop() also looks
    proc2 = FakeProc()
    child2, _, _ = _child(tmp_path, monkeypatch, proc=proc2,
                          state_dir=tmp_path / "second" / "state" / "openhop")   # a clean dir
    assert child2.start()

    async def go2():
        task = asyncio.get_running_loop().create_task(child2.watch())
        await asyncio.sleep(0.01)
        proc2.finish(0)
        await asyncio.wait_for(task, 2)
    asyncio.run(go2())
    assert not child2.marker.exists()
    child2.stop()                                            # idempotent: no warning, no error
    assert "could not be cleared" not in caplog.text


def test_clearing_is_idempotent_and_an_unlink_failure_keeps_the_marker(tmp_path, monkeypatch, caplog):
    assert pm.clear_marker(tmp_path / "missing") is True   # already clear
    if os.geteuid() == 0:
        pytest.skip("root unlinks everything")
    sd = tmp_path / "state" / "openhop"
    marker = sd / pm.MARKER_NAME
    pm.write_marker(marker, BOOT_A)
    sd.chmod(0o500)
    try:
        assert pm.clear_marker(marker) is False
        assert "could not be cleared" in caplog.text
    finally:
        sd.chmod(0o700)
    assert marker.exists()


# --- the host's shutdown order, bounded, then the deferred upstream watchdog ------------------

def _host_with_fakes(monkeypatch, tmp_path, *, radio_hangs=False, gps_raises=False, plugins=True):
    from meshcore_host import repeater as rep
    from meshcore_host.config import HostConfig
    order: list[str] = []

    class FakeRadio:
        on_link_state = None

        def begin(self):
            order.append("radio begin")

        async def aclose(self):
            order.append("radio close")
            if radio_hangs:
                await asyncio.sleep(3600)

    class FakeGps:
        async def stop(self):
            order.append("gps stop")
            if gps_raises:
                raise RuntimeError("feed broke")

    class FakeDaemon:
        lhpc_watchdog_deferred = True

        async def run(self):
            order.append("daemon run")

    class FakeChild:
        def start(self):
            order.append("plugins start")
            return True

        async def watch(self):
            await asyncio.sleep(3600)

        def stop(self):
            order.append("plugins stop")

    armed = []
    monkeypatch.setattr(rep, "LoRaHAMRadio", lambda **kw: FakeRadio())
    monkeypatch.setattr(rep, "PluginManagerChild", lambda sd: FakeChild())
    monkeypatch.setattr(rep, "CLEANUP_STEP_S", 0.05)
    cfg = HostConfig(name="Chat 1", allow="127.0.0.1", bind="127.0.0.1", port=5000, key="11" * 32,
                     frequency=869618000, bandwidth=62500, spreading_factor=8, coding_rate=8,
                     txpower=14, preamble=16, airtime=10.0, mode="chat+repeater",
                     repeater_name="Relay 1", repeater_key="22" * 64, repeater_behaviour="forward",
                     dashboard_password="x" * 24, repeater_state_dir=str(tmp_path / "openhop"),
                     plugins_on=plugins)
    host = rep._Host.__new__(rep._Host)
    host.cfg = cfg
    host.radio = FakeRadio()
    host.gps = FakeGps()
    host.plugins = FakeChild() if plugins else None
    host.daemon = FakeDaemon()

    class FakeUpstream:
        @staticmethod
        def _arm_exit_watchdog(daemon):
            armed.append(daemon)
    import sys
    import types
    fake_main = types.ModuleType("repeater.main")
    fake_main.RepeaterDaemon = FakeUpstream
    monkeypatch.setitem(sys.modules, "repeater.main", fake_main)
    monkeypatch.setitem(sys.modules, "repeater", types.ModuleType("repeater"))
    return host, order, armed


def test_shutdown_order_manager_then_gps_then_radio_then_the_deferred_watchdog(tmp_path, monkeypatch):
    host, order, armed = _host_with_fakes(monkeypatch, tmp_path)
    asyncio.run(host.run())
    assert order == ["radio begin", "plugins start", "daemon run", "plugins stop", "gps stop",
                     "radio close"]
    assert armed == [host.daemon]                            # armed AFTER LHPC's own cleanup


def test_a_hanging_radio_close_is_bounded_and_the_watchdog_is_still_armed(tmp_path, monkeypatch, caplog):
    host, order, armed = _host_with_fakes(monkeypatch, tmp_path, radio_hangs=True, gps_raises=True)
    asyncio.run(host.run())
    assert order[-3:] == ["plugins stop", "gps stop", "radio close"]
    assert "GPS feed stop failed" in caplog.text
    assert "radio close did not finish" in caplog.text
    assert armed == [host.daemon]


def test_the_watchdog_is_armed_even_when_the_daemon_run_raises(tmp_path, monkeypatch):
    host, order, armed = _host_with_fakes(monkeypatch, tmp_path)

    async def boom():
        order.append("daemon run")
        raise RuntimeError("upstream died")
    host.daemon.run = boom
    with pytest.raises(RuntimeError):
        asyncio.run(host.run())
    assert order[-3:] == ["plugins stop", "gps stop", "radio close"] and armed == [host.daemon]


def test_plugins_off_spawns_nothing(tmp_path, monkeypatch):
    host, order, armed = _host_with_fakes(monkeypatch, tmp_path, plugins=False)
    asyncio.run(host.run())
    assert "plugins start" not in order and "plugins stop" not in order


def test_the_subclass_defers_upstreams_watchdog(tmp_path):
    """Real upstream: `_arm_exit_watchdog` on the LHPC subclass arms nothing by itself and
    records the deferral; the base method it defers to still exists at this pin."""
    pytest.importorskip("repeater.main")
    from meshcore_host import repeater as rep
    from repeater.main import RepeaterDaemon
    from test_repeater import _cfg
    host = rep._Host(_cfg(repeater_state_dir=str(tmp_path / "openhop"), plugins_on=False))
    assert host.daemon.lhpc_watchdog_deferred is False
    host.daemon._arm_exit_watchdog()
    assert host.daemon.lhpc_watchdog_deferred is True
    assert callable(getattr(RepeaterDaemon, "_arm_exit_watchdog"))
    assert RepeaterDaemon.SHUTDOWN_EXIT_GRACE_S == 5.0


# --- the config switch --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expect", [('"on"', True), ('"off"', False), (None, True)])
def test_plugins_switch_on_off_absent(tmp_path, value, expect):
    from meshcore_host.config import load_config
    from test_repeater import KEY64
    line = f"plugins = {value}\n" if value is not None else ""
    p = tmp_path / "c.toml"
    p.write_text(f'''
[companion]
name = "Chat 1"
[identity]
[radio]
preset = "eu_uk_narrow"
[repeater]
role = "repeater"
name = "Relay 1"
key = "{KEY64}"
behaviour = "forward"
admin_password = "{'x' * 24}"
state_dir = "/rt/state/openhop"
{line}''')
    assert load_config(p).plugins_on is expect


@pytest.mark.parametrize("value", ['"ON"', '"yes"', "1", "true", '"off "'])
def test_plugins_switch_refuses_anything_but_the_two_literals(tmp_path, value):
    from meshcore_host.config import ConfigError, load_config
    from test_repeater import KEY64
    p = tmp_path / "c.toml"
    p.write_text(f'''
[companion]
name = "Chat 1"
[identity]
[radio]
preset = "eu_uk_narrow"
[repeater]
role = "repeater"
name = "Relay 1"
key = "{KEY64}"
behaviour = "forward"
admin_password = "{'x' * 24}"
state_dir = "/rt/state/openhop"
plugins = {value}
''')
    with pytest.raises(ConfigError, match="plugins"):
        load_config(p)


def test_the_switch_is_ignored_in_chat(tmp_path):
    from meshcore_host.config import load_config
    from test_repeater import SEED
    p = tmp_path / "c.toml"
    p.write_text(f'''
[companion]
name = "Chat 1"
[identity]
key = "{SEED}"
[radio]
preset = "eu_uk_narrow"
[repeater]
role = "chat"
plugins = "banana"
''')
    assert load_config(p).mode == "chat"                    # the repeater table is not read
