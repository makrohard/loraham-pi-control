"""The Voice stack on a Lite box and on a desktop: the GTK app owns the shared config and is
dropped without a display, the terminal variant is the fallback the operator is told to run
(never started directly, never restarted directly), both claim the audio device exclusively,
and the start/plan/status surfaces agree about which of the two is real here."""

from __future__ import annotations

import pytest

from lhpc.core.lifecycle import Lifecycle
from lhpc.core.outcomes import Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ConfigWrite, ControllerService
from seams import seed_built


def outcomes(res):
    """What the start actually produced — an `any(...)` assertion otherwise reports only False,
    which is unusable when the run that fails is a CI runner you cannot attach to."""
    return [(r.component, getattr(r.outcome, "name", r.outcome), (r.summary or "")[:90])
            for r in res.results]


def _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False, patch_reqs=True, gtk_built=True):
    """A voice-startable service: daemon READY/DIRECT on both bands, daemon + RadioLib + the
    terminal variant built (and the GTK app too unless `gtk_built=False`). desktop=True fakes the
    GTK header (GUI app stays) and a display; desktop=False also fakes the ABSENCE of a display,
    so the host running the tests never leaks its own DISPLAY into the "Lite" scenario.
    patch_reqs=False keeps the REAL missing_requirements so the GUI-unavailability predicate
    stays live."""
    STATUS = b"STATUS RADIO=READY TXMODE=DIRECT\n"
    paths = {"/usr/include/gtk-3.0/gtk/gtk.h"} if desktop else set()
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": STATUS,
                                   "/tmp/loraconf868.sock": STATUS},
                     paths=paths).system
    seed_built(tmp_path, "loraham-daemon/loraham_daemon/loraham_daemon", "RadioLib/build/libRadioLib.a",
                "LoRaHAM_Voice/loraham_voice_cli",
                *(("LoRaHAM_Voice/loraham_voice",) if gtk_built else ()))
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    # host evidence outside the injected System (compositor sockets), like the root conftest
    monkeypatch.setattr(type(svc), "display_available", staticmethod(lambda: desktop))
    if patch_reqs:
        # this host has no codec2/ALSA/GTK dev headers: the requirement scan is stubbed so the
        # start reaches the GUI, config and resource decisions under test
        monkeypatch.setattr(Lifecycle, "missing_requirements", lambda self, c: [])
    # ownership recording needs a live /proc identity: rebuild the lifecycle with the real-but-
    # harmless spawn
    monkeypatch.setattr(type(svc), "_lifecycle", lambda self: Lifecycle(
        self._paths, self.stacks(), self.config(), self._system, spawn=real_spawn))
    return svc


def _config_written(monkeypatch, svc, writes=None):
    """The shared config write succeeds (the seam the GTK app owns); `writes` collects the targets."""
    def written(self, t, b="", overrides=None, **kw):
        if writes is not None:
            writes.append(t)
        return [ConfigWrite("loraham-voice", "/rt/loraham_voice.conf", "written", "")]
    monkeypatch.setattr(type(svc), "write_config_files", written)


def _config_fails(monkeypatch, svc):
    monkeypatch.setattr(type(svc), "write_config_files",
                        lambda self, t, b="", overrides=None, **kw: [
                            ConfigWrite("loraham-voice", "/rt/loraham_voice.conf", "failed", "disk full")])


def test_lite_voice_start_seeds_config_despite_display_skip(tmp_path, monkeypatch, set_call, real_spawn):
    # The display-skipped GTK component OWNS loraham_voice.conf; its config must still be
    # generated so the terminal variant the operator is told to run never sees an absent/stale file.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
    writes = []
    _config_written(monkeypatch, svc, writes)
    set_call(svc)
    res = svc.start("voice", apply=True)
    assert "loraham-voice" in writes, "GTK component's config must be written before the skip"
    assert any(r.component == "loraham-voice" and r.outcome == Outcome.SKIPPED
               for r in res.results), outcomes(res)
    # The stack start is OK — the interactive sidecar's MANUAL_REQUIRED and the gui_optional
    # display-skip are both accepted, and the summary carries the command.
    assert res.ok is True, outcomes(res)
    cli = next(r for r in res.results if r.component == "loraham-voice-cli")
    assert cli.outcome == Outcome.MANUAL_REQUIRED
    assert "run it yourself in a terminal:" in (cli.summary or "")


def test_lite_voice_start_blocks_when_shared_config_fails(tmp_path, monkeypatch, set_call, real_spawn):
    # A FAILED write of the shared config is a typed BLOCKED, never a silent skip that leaves the
    # terminal variant with stale configuration.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
    _config_fails(monkeypatch, svc)
    set_call(svc)
    res = svc.start("voice", apply=True)
    assert any(r.component == "loraham-voice" and r.outcome == Outcome.BLOCKED
               and "config generation failed" in (r.summary or "") for r in res.results), \
        outcomes(res)


def test_interactive_voice_cli_start_creates_its_config_files_symlink(tmp_path, monkeypatch, set_call, real_spawn):
    # LIVE-FOUND on the Pi: an interactive component never reaches `life.start`, the ONLY
    # caller of run_pre_steps — so the CLI's config/files symlink was never created and the
    # MANUAL_REQUIRED command handed to the operator failed with ENOENT. The app resolves
    # loraham_voice.conf from dirname(argv0), so that symlink is what makes the command work.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
    _config_written(monkeypatch, svc)
    set_call(svc)
    link = tmp_path / "config" / "files" / "loraham_voice_cli"
    assert not link.is_symlink()                                   # precondition
    res = svc.start("voice", apply=True)
    cli = next(r for r in res.results if r.component == "loraham-voice-cli")
    assert cli.outcome == Outcome.MANUAL_REQUIRED, outcomes(res)
    assert link.is_symlink(), "the manual command's argv0 must exist after the start"
    assert str(link) in (cli.summary or ""), cli.summary


def test_direct_terminal_variant_start_is_refused_and_names_the_stack(tmp_path, monkeypatch, set_call, real_spawn):
    # loraham_voice.conf — including the LICENSED CALLSIGN — is owned by loraham-voice. A direct
    # component start visits only the CLI and the daemon, so no config is written and upstream
    # falls back to its compiled-in default callsign. Refuse, and leave NO side effect behind
    # (no symlink, no marker, no config).
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
    set_call(svc)
    res = svc.start("loraham-voice-cli", apply=True)
    assert res.ok is False
    assert "lhpc stack start voice" in res.summary, res.summary
    assert "run it yourself" not in res.summary
    # PREFLIGHT: refused before the component loop — no per-component results, no
    # daemon ensure, no already-healthy shortcut.
    assert res.results == (), outcomes(res)
    assert not (tmp_path / "config" / "files" / "loraham_voice_cli").is_symlink()
    assert not (tmp_path / "config" / "files" / "loraham_voice.conf").exists()
    assert svc.interactive_band("voice") is None


def test_direct_start_refusal_is_a_preflight_even_with_marker_and_ready_daemon(tmp_path, monkeypatch, set_call, real_spawn):
    # With the Voice marker present and the daemon ready, a direct CLI start used to hit the
    # already-healthy shortcut and return SUCCESS; without it the daemon was ensured/reconfigured
    # before the refusal. The refusal must come before any of that: no feed clearing, no daemon
    # work, no shortcut.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
    svc.mark_interactive("voice", "433")
    fed = []
    monkeypatch.setattr(type(svc), "clear_daemon_feed",
                        lambda self, *a, **kw: fed.append(a) or 0, raising=False)
    set_call(svc)
    res = svc.start("loraham-voice-cli", apply=True)
    assert res.ok is False
    assert "lhpc stack start voice" in res.summary, res.summary
    assert "already healthy" not in res.summary.lower()
    assert res.results == ()
    assert fed == []                          # zero side effects


def test_direct_start_of_other_interactive_sidecars_stays_unrefused(tmp_path, monkeypatch, set_call, real_spawn):
    # The fallback policy is derived from the manifest shape (non-main interactive + gui_optional
    # MAIN) and must capture ONLY voice's terminal variant — nomadnet (reticulum) and meshcore-cli
    # (meshcore) keep their long-standing behaviour.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
    assert svc._gui_fallback_refusal("loraham-voice-cli") is not None
    for other in ("nomadnet", "meshcore-cli", "loraham-chat"):
        assert svc._gui_fallback_refusal(other) is None, other
    set_call(svc)
    res = svc.start("nomadnet", apply=True)
    assert "shares" not in res.summary       # never the voice refusal
    assert any(r.component == "nomadnet" for r in res.results), \
        "the component loop must be reached"


def test_plan_omits_the_terminal_command_where_the_gtk_app_runs(tmp_path, monkeypatch, set_call, real_spawn):
    # The apply=False PLAN (CLI dry-run and the web confirm page) must obey the same fallback
    # policy — a desktop `start voice` plan may not render the CLI command it would refuse to honour.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=True)
    set_call(svc)
    res = svc.start("voice", apply=False)
    text = "\n".join(res.details)
    assert "loraham_voice_cli" not in text, text
    assert "run it yourself" not in text, text
    assert "[skip] loraham-voice-cli" in text, text
    # A direct CLI plan is refused outright by the same preflight.
    res2 = svc.start("loraham-voice-cli", apply=False)
    assert res2.ok is False and "lhpc stack start voice" in res2.summary
    # On a Lite box the plan still presents the command — that is the fallback working.
    lite = _voice_svc(real_spawn, tmp_path / "lite", monkeypatch, desktop=False)
    set_call(lite)
    res3 = lite.start("voice", apply=False)
    assert "run it yourself" in "\n".join(res3.details)


@pytest.mark.parametrize("apply", [False, True], ids=["plan", "apply"])
def test_direct_restart_of_the_fallback_refuses_before_any_stop(tmp_path, monkeypatch, set_call, real_spawn, apply):
    # restart stops BEFORE starting, so the start-side refusal used to arrive only after the
    # component and its daemon were already taken down (marker cleared, daemon released). Both
    # restart entries must refuse first — marker, daemon, feed and owner state untouched — and the
    # dry-run must show the real reason, not a plan suggesting the impossible apply.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
    svc.mark_interactive("voice", "433")
    touched = []
    for m in ("stop", "clear_daemon_feed"):
        monkeypatch.setattr(type(svc), m,
                            (lambda name: lambda self, *a, **kw: touched.append(name))(m),
                            raising=False)
    set_call(svc)
    res = svc.restart("loraham-voice-cli", apply=apply)
    assert res.ok is False, res.summary
    assert "lhpc stack start voice" in res.summary, res.summary
    assert res.results == ()
    assert touched == []                      # no stop, no feed clearing
    assert svc.interactive_band("voice") == "433"   # marker survives


def _cli_holds_audio_conflict(monkeypatch, svc):
    # the resource verdict is stubbed for the CLI alone: reproducing an exclusive audio.default
    # claim through the FakeSystem process table would also make a Voice component RUNNING in the
    # snapshot, which is a different case from "a sibling holds the device"
    monkeypatch.setattr(type(svc), "_running_conflicts",
                        lambda self, c, b: c.id == "loraham-voice-cli")


def test_desktop_daemon_recovery_is_ok_despite_a_running_gtk_app(tmp_path, monkeypatch, set_call, real_spawn):
    # On a desktop with the GTK app holding the audio device, `start voice` after a daemon
    # failure must recover the daemon and return OK — the INACTIVE fallback is SKIPPED before
    # the resource gate, never BLOCKED into a failed result.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=True)
    _cli_holds_audio_conflict(monkeypatch, svc)
    _config_written(monkeypatch, svc)
    set_call(svc)
    res = svc.start("voice", apply=True)
    cli = next(r for r in res.results if r.component == "loraham-voice-cli")
    assert cli.outcome == Outcome.SKIPPED, outcomes(res)
    assert "resource conflict" not in (cli.summary or "")
    assert res.ok is True, outcomes(res)


def test_second_start_noop_does_not_fabricate_already_healthy_for_the_fallback(tmp_path, monkeypatch, set_call, real_spawn):
    # The already-healthy shortcut used to synthesize 'loraham-voice-cli already_healthy already
    # running' — a lie twice over: on Lite the marker only proves the command was PRESENTED, and
    # the gui-skipped GTK app is not runnable at all. Components the health predicate skipped are
    # omitted.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
    _config_written(monkeypatch, svc)
    set_call(svc)
    first = svc.start("voice", apply=True)
    assert first.ok is True, outcomes(first)
    assert svc.interactive_band("voice") is not None      # command presented
    second = svc.start("voice", apply=True)
    assert second.ok is True, outcomes(second)
    assert "already healthy" in second.summary
    comps = {r.component: r.outcome for r in second.results}
    assert "loraham-voice-cli" not in comps, comps        # marker-satisfied, not "running"
    assert "loraham-voice" not in comps, comps            # gui-skipped, not "running"
    assert comps.get("loraham-daemon") == Outcome.ALREADY_HEALTHY, comps


def test_terminal_variant_not_presented_when_shared_config_fails(tmp_path, monkeypatch, set_call, real_spawn):
    # The CLI must not reach pre-steps, marker creation or command presentation unless the
    # shared-config OWNER completed. A failed GTK config write previously still produced a marker
    # and a printed command over an absent shared config.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
    _config_fails(monkeypatch, svc)
    set_call(svc)
    res = svc.start("voice", apply=True)
    gtk = next(r for r in res.results if r.component == "loraham-voice")
    cli = next(r for r in res.results if r.component == "loraham-voice-cli")
    assert gtk.outcome == Outcome.BLOCKED, outcomes(res)
    assert cli.outcome == Outcome.BLOCKED, outcomes(res)
    assert "did not complete" in (cli.summary or ""), cli.summary
    assert "run it yourself" not in (cli.summary or "")
    assert not (tmp_path / "config" / "files" / "loraham_voice_cli").is_symlink()
    assert svc.interactive_band("voice") is None


def test_terminal_variant_blocked_while_a_sibling_holds_the_audio_device(tmp_path, monkeypatch, set_call, real_spawn):
    # Both voice components claim audio.default EXCLUSIVELY. The interactive branch used to
    # return before the resource gate, so the documented "can never run at once" guarantee was
    # not enforced for the terminal variant.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
    _cli_holds_audio_conflict(monkeypatch, svc)
    _config_written(monkeypatch, svc)
    set_call(svc)
    res = svc.start("voice", apply=True)
    cli = next(r for r in res.results if r.component == "loraham-voice-cli")
    assert cli.outcome == Outcome.BLOCKED, outcomes(res)
    assert "resource conflict" in (cli.summary or ""), cli.summary
    assert not (tmp_path / "config" / "files" / "loraham_voice_cli").is_symlink()
    assert svc.interactive_band("voice") is None


def test_interactive_start_blocks_when_its_pre_steps_fail(tmp_path, monkeypatch, set_call, real_spawn):
    # A pre-step failure is a typed BLOCKED — never a manual command that cannot run.
    from lhpc.core import commands
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
    _config_written(monkeypatch, svc)

    def _boom(steps, runtime, source, band=""):
        raise commands.CommandError("pre-step failed: denied")

    monkeypatch.setattr(commands, "run_pre_steps", _boom)
    set_call(svc)
    res = svc.start("voice", apply=True)
    cli = next(r for r in res.results if r.component == "loraham-voice-cli")
    assert cli.outcome == Outcome.BLOCKED, outcomes(res)
    assert "pre-start setup failed" in (cli.summary or "")
    assert "run it yourself" not in (cli.summary or "")


def test_desktop_voice_start_stays_ok_with_interactive_sidecar(tmp_path, monkeypatch, set_call, real_spawn):
    # On a desktop the GTK app starts as before and the terminal variant is NOT presented at all —
    # the GUI main is usable, owns the shared config and holds the exclusive audio device, so
    # offering a fallback would contradict the "can never run at once" claim.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=True)
    _config_written(monkeypatch, svc)
    set_call(svc)
    res = svc.start("voice", apply=True)
    outcomes = {r.component: r.outcome for r in res.results}
    assert outcomes.get("loraham-voice-cli") == Outcome.SKIPPED
    cli = next(r for r in res.results if r.component == "loraham-voice-cli")
    assert "fallback" in (cli.summary or ""), cli.summary
    assert "run it yourself" not in (cli.summary or "")
    assert res.ok is True, outcomes(res)


def test_lite_voice_unbuilt_gate_ignores_the_dropped_gtk_component(tmp_path, monkeypatch, real_spawn):
    # The web start gate's unbuilt_components must not count the gui-dropped GTK component
    # (shared checkout exists, binary can never be built here) — otherwise the console loops
    # needs-build forever.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False, patch_reqs=False,
                     gtk_built=False)                                  # CLI built, GTK not
    assert "loraham-voice" in svc.gui_unavailable_components(svc.stack("voice"))  # precondition
    assert "loraham-voice" not in svc.unbuilt_components("voice")


def test_lite_status_overlay_marks_unbuildable_gtk_not_applicable(tmp_path, monkeypatch, real_spawn):
    # With the SHARED checkout installed by the terminal variant, the stopped-but-unbuildable GTK
    # app must read NOT_APPLICABLE on a Lite console, not present as a startable stopped app.
    from lhpc.core.model import RunState
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False, patch_reqs=False)
    assert "loraham-voice" in svc.gui_unavailable_components(svc.stack("voice"))  # precondition
    snap = svc.build_snapshot()
    ss = next(x for x in snap.stacks if x.stack.id == "voice")
    assert ss.components["loraham-voice"].run_state is RunState.NOT_APPLICABLE


def test_desktop_without_gtk_deps_start_voice_is_ok(tmp_path, monkeypatch, set_call, real_spawn):
    # A box WITH a display but WITHOUT the GTK dev deps (default bootstrap, no --with-gui) must
    # start voice OK — the toolkit-missing GTK component is typed-SKIPPED by the same predicate
    # that admitted the stack, never BLOCKED on its missing requirements or build.
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False, patch_reqs=False)
    monkeypatch.setattr(type(svc), "display_available", staticmethod(lambda: True))
    # REAL missing_requirements stays live (it drives the GUI predicate under test); only the
    # terminal variant's OWN codec2/ALSA/ncurses headers are treated as present, since this
    # host is not the Pi. Without this the CLI blocks on ITS deps, which is a different case.
    monkeypatch.setattr(type(svc), "start_blocking_requirements",
                        lambda self, c: [] if c.id == "loraham-voice-cli"
                        else self._lifecycle().missing_requirements(c))
    _config_written(monkeypatch, svc)
    set_call(svc)
    res = svc.start("voice", apply=True)
    gtk = next(r for r in res.results if r.component == "loraham-voice")
    assert gtk.outcome == Outcome.SKIPPED and "GUI toolkit not installed" in (gtk.summary or "")
    assert res.ok is True, outcomes(res)


def test_other_stack_start_keeps_voice_sidecar_marker(tmp_path, monkeypatch, real_spawn):
    # The interactive-marker reaper must judge voice by its RUNNING terminal sidecar, not by the
    # GTK main (never RUNNING on Lite) — a live TUI's command block must survive another stack's
    # start.
    from lhpc.core.model import RunState
    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
    assert svc.mark_interactive("voice", "868")

    class _St:                       # snapshot stub: the sidecar TUI is process-detected
        def __init__(self, rs): self.run_state = rs
    snap = type("Snap", (), {"stacks": [
        type("X", (), {"components": {"loraham-voice": _St(RunState.NOT_APPLICABLE),
                                      "loraham-voice-cli": _St(RunState.RUNNING)}})()]})()
    monkeypatch.setattr(type(svc), "build_snapshot", lambda self: snap)
    cleared = svc.clear_stale_interactive(keep="kiss")
    assert "voice" not in cleared, "a RUNNING terminal sidecar must keep its marker"
    # ...and with the TUI gone, the marker is reaped exactly like chat's.
    snap.stacks[0].components["loraham-voice-cli"] = _St(RunState.STOPPED)
    assert "voice" in svc.clear_stale_interactive(keep="kiss")
