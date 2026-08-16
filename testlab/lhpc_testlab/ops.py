"""Test-lab operations as free functions over a ControllerService (`svc`). testlab is
NOT part of lhpc — it composes the public service, it is not mixed into it. init is the
only env-key-only op; every other op requires the two-key latch AND that the service was
built with the lab provider active (svc._ext), so a process started before `testlab init`
never drives the real host while the UI claims simulation."""
from __future__ import annotations

import json
import os
import signal
import sys
import time

from lhpc.core import runtime_fs
from lhpc.core.service_base import ActionResult

from . import (
    active,
    data_path,
    env_enabled,
    marker_path,
    scenarios,
    state_dir,
    supervisor,
)

_CALLSIGN = "DL0LAB"
INJECT_PRESETS = ("aprs-position", "aprs-message", "meshcore-hello")


def _lab_built(svc) -> bool:
    return getattr(svc, "_ext", None) is not None


def is_active(svc) -> bool:
    return _lab_built(svc) and active(svc._paths)


def _refusal(svc) -> ActionResult:
    if not _lab_built(svc) and active(svc._paths):
        return ActionResult(False, "Test lab was initialized AFTER this process "
                                   "started — restart it (`lhpc-testlab web` / the CLI) "
                                   "so it picks up the lab root")
    return ActionResult(False, "Test lab is not active: it needs LHPC_TESTLAB=1 AND a "
                               "lab root (create one with `lhpc-testlab init`)")


# ---- init (env key only) -----------------------------------------------------------------


def init(svc) -> ActionResult:
    if not env_enabled():
        return ActionResult(False, "Refusing: LHPC_TESTLAB=1 is not set — the env key "
                                   "is the operator's explicit lab intent")
    root = svc._paths.runtime_root
    marker = marker_path(svc._paths)
    if root.exists() and any(root.iterdir()) and not marker.exists():
        return ActionResult(False, f"Refusing to convert {root}: it is neither empty "
                                   "nor a lab root (state/testlab/enabled is absent)")
    runtime_fs.ensure_dir(svc._paths, state_dir(svc._paths))
    runtime_fs.atomic_write(svc._paths, marker,
                            "LHPC test-lab root — simulated hardware\n", 0o600)
    boot = supervisor.ensure_boot_identity(svc._paths)
    scenarios.apply(svc._paths, scenarios.DEFAULT)
    return ActionResult(True, f"Lab root ready at {root} (scenario: healthy).",
                        details=[f"  simulated boot id: {boot}",
                                 f"  boot file: {supervisor.boot_file(svc._paths)}"],
                        next_commands=["lhpc-testlab reset"])


# ---- status / check ----------------------------------------------------------------------


def status(svc) -> ActionResult:
    if not is_active(svc):
        return _refusal(svc)
    flags = scenarios.effective_state(svc._paths)
    details = [f"  scenario: {flags['_name']}",
               f"  simulated boot: {supervisor.ensure_boot_identity(svc._paths)} "
               f"(uptime {supervisor.sim_uptime(svc._paths):.0f}s)",
               f"  fake gpsd: {'running' if _gpsd_pid(svc) else 'down'}",
               f"  radios: 433={flags['radio_433']} 868={flags['radio_868']}",
               f"  wifi: {flags['wifi']}  firewall receipt: {flags['fw_receipt']}"]
    tail = runtime_fs.tail(svc._paths, svc._paths.under("state", "testlab",
                                                        "events.log"), lines=6)
    if tail:
        details.append("  recent events:")
        details += [f"    {ln}" for ln in tail]
    return ActionResult(True, "Test lab is ACTIVE — all hardware is simulated.",
                        details=details)


def check(svc) -> ActionResult:
    """Honest health gate: ok requires the fakes alive AND every INSTALLED stack ready.
    An uninstalled stack is reported as 'not installed' (not a failure); an installed
    stack with an unmet requirement is a PROBLEM that fails the check — a reported
    missing requirement must never coexist with 'passed'."""
    if not is_active(svc):
        return _refusal(svc)
    problems: list[str] = []
    details = [f"  scenario: {scenarios.effective_state(svc._paths)['_name']}"]
    if not _gpsd_pid(svc):
        problems.append("fake gpsd is not running (run `lhpc-testlab reset`)")
    for s in svc.stacks():
        installed = svc.is_installed(s.id)
        if not installed:
            details.append(f"  {s.id}: not installed")
            continue
        gates: list[str] = []
        for comp in s.components:
            if not comp.run_argv:
                continue
            try:
                missing = svc.start_blocking_requirements(comp)
            except Exception as exc:
                missing = [f"gate error: {exc}"]
            gates += [f"{comp.id}: {m}" for m in missing]
        details.append(f"  {s.id}: " + ("ready" if not gates
                                        else "NOT READY — " + "; ".join(gates)[:180]))
        if gates:
            problems.append(f"{s.id} not ready: {gates[0]}")
    ok = not problems
    return ActionResult(ok, "Lab check passed." if ok else
                        f"Lab check found {len(problems)} problem(s).",
                        details=details + [f"  PROBLEM: {p}" for p in problems])


# ---- reset / scenario / inject -----------------------------------------------------------


def reset(svc) -> ActionResult:
    if not is_active(svc):
        return _refusal(svc)
    from lhpc.core.config import save_install_config, save_operator_config
    details: list[str] = []
    # Stop anything still running so the baseline is genuinely clean — a lab reset should not
    # leave a stack from the previous run alive (or holding a band). _operator=False: a reset is
    # NOT an operator stop, so it must not leave a stop-intent tombstone that pins the stack
    # "stay stopped" past the reset. Fail CLOSED — do not erase runtime state over a stack we
    # could not stop (a half-stopped box is not a clean baseline).
    stopped, stop_failed = 0, []
    for s in svc.stacks():
        try:
            if svc.stack_running(s.id):
                if svc.stop(s.id, apply=True, _operator=False).ok:
                    stopped += 1
                else:
                    stop_failed.append(s.id)
        except Exception as exc:
            stop_failed.append(f"{s.id} ({exc})")
    if stop_failed:
        return ActionResult(False, f"Reset aborted — could not stop: {', '.join(stop_failed)}.",
                            details=[*details, f"  stop failed: {', '.join(stop_failed)}"])
    if stopped:
        details.append(f"  stopped {stopped} running stack(s)")
    _clear_runtime_state(svc)                      # clean baseline (no surviving state/config)
    boot = svc.bootstrap(apply=True)
    details.append(f"  bootstrap: {'ok' if boot.ok else boot.summary}")
    if not boot.ok:
        return ActionResult(False, "Reset failed at bootstrap.",
                            details=details + boot.details)
    hw = svc.set_hardware_setup("loraham")
    details.append(f"  hardware: {'loraham' if hw.ok else hw.summary}")
    if not hw.ok:
        return ActionResult(False, "Reset failed configuring the radio hardware.",
                            details=details + hw.details[:6])
    try:
        save_operator_config(svc._paths, _CALLSIGN)
    except Exception as exc:
        return ActionResult(False, f"Reset failed saving the callsign ({exc}).",
                            details=details)
    svc._invalidate_config()
    details.append(f"  callsign: {_CALLSIGN}")
    # SAFETY (UI default only): seed graywolf's stored config to the local APRS-IS sink so the
    # UI reads 127.0.0.1. The HARD guarantee is the manifest-overlay swap below, which renders
    # graywolf-provision.py with a LITERAL --igate-server 127.0.0.1 regardless of this config.
    try:
        svc.save_stack_config("graywolf", {"igate_server": "127.0.0.1", "igate_port": "14580"})
        details.append("  graywolf igate default -> local sink (127.0.0.1:14580)")
    except Exception as exc:                        # non-fatal: enforcement is the overlay
        details.append(f"  graywolf igate default: not seeded ({exc})")
    supervisor.ensure_boot_identity(svc._paths)
    scenarios.apply(svc._paths, scenarios.DEFAULT)
    details.append("  scenario: healthy")
    _respawn_gpsd(svc, details)
    _prepare_pyshims(svc, details)
    _prepare_aprs_sink(svc, details)
    try:
        commits = _materialize_sources(svc)
        details.append(f"  fake sources: {', '.join(sorted(commits))}")
    except Exception as exc:
        return ActionResult(False, f"Reset failed materializing fake sources ({exc}).",
                            details=details)
    from . import manifest_overlay
    overlay = manifest_overlay.generate(svc._paths, commits)
    # SAFETY (fail closed): the graywolf iGate sink is enforced by the overlay swap. If it did
    # NOT land (manifest line drift), abort — never run a lab whose graywolf could reach the
    # live APRS-IS network.
    if manifest_overlay.GRAYWOLF_SINK_TO not in overlay.read_text():
        return ActionResult(False, "Reset aborted — graywolf iGate local-sink enforcement is "
                            "missing from the manifest overlay (line drift?).", details=details)
    details.append("  graywolf igate ENFORCED -> local sink (overlay render)")
    save_install_config(
        svc._paths,
        adopt_search_root=str(svc._paths.under("state", "testlab", "adopt")),
        source_strategy="copy")
    svc._invalidate_config()
    svc._manifest_path = overlay
    svc._stacks = None
    svc._gps_stacks_cache = svc._gps_consumers_cache = None
    inst = svc.install("daemon", apply=True, source="pinned")
    details.append(f"  install daemon (fake): {'ok' if inst.ok else inst.summary}")
    if not inst.ok:
        return ActionResult(False, "Reset failed installing the fake daemon.",
                            details=details + inst.details[:8])
    _install_binary_stacks(svc, details)
    scenarios.log_event(svc._paths, "reset")
    return ActionResult(True, "Test lab reset to the healthy baseline.", details=details,
                        next_commands=["lhpc-testlab status", "lhpc status"])


def scenario(svc, name: str) -> ActionResult:
    if not is_active(svc):
        return _refusal(svc)
    if name not in scenarios.SCENARIOS:
        return ActionResult(False, f"Unknown scenario {name!r} — one of: "
                                   f"{', '.join(sorted(scenarios.SCENARIOS))}")
    scenarios.apply(svc._paths, name)
    return ActionResult(True, f"Scenario is now '{name}' (fakes pick it up within a "
                              "second).")


def inject(svc, band: str, preset: str) -> ActionResult:
    if not is_active(svc):
        return _refusal(svc)
    if band not in ("433", "868"):
        return ActionResult(False, "Band must be 433 or 868")
    if preset not in INJECT_PRESETS:
        return ActionResult(False, f"Unknown preset {preset!r} — one of: "
                                   f"{', '.join(INJECT_PRESETS)}")
    qdir = svc._paths.under("state", "testlab", "rx-queue", band)
    runtime_fs.ensure_dir(svc._paths, qdir)
    stamp = f"{time.monotonic_ns():020d}"
    runtime_fs.atomic_write(svc._paths, qdir / f"{stamp}.json",
                            json.dumps({"preset": preset}), 0o600)
    scenarios.log_event(svc._paths, f"inject {preset} -> band {band}")
    return ActionResult(True, f"Queued '{preset}' for band {band}.")


def power(svc, kind: str) -> int:
    """Faithful simulated reboot/poweroff (hidden helper `lhpc-testlab _power`). A real
    reboot KILLS running stacks (it does NOT operator-stop them, so boot-restore may
    bring them back) and advances the boot id; then previously-running, non-operator-
    stopped stacks are restored. poweroff advances the boot id and leaves them down."""
    if not is_active(svc):
        return 1
    running = []
    for s in svc.stacks():
        try:
            if svc.stack_running(s.id):
                running.append(s.id)
        except Exception:
            pass
    # Terminate WITHOUT the operator-stop tombstone (_operator=False) — a reboot is not
    # an explicit stop, so it must not mark the stack "stay stopped".
    for sid in running:
        try:
            svc.stop(sid, apply=True, _operator=False)
        except Exception:
            pass
    supervisor.advance_boot(svc._paths, reason=f"simulated {kind}")
    _respawn_gpsd(svc, [])
    restored = 0
    if kind == "reboot":
        for sid in running:                        # boot restoration
            try:
                if svc.start(sid, apply=True).ok:
                    restored += 1
            except Exception:
                pass
    scenarios.log_event(svc._paths, f"simulated {kind}: stopped {len(running)}, "
                        f"restored {restored} (host untouched)")
    return 0


def _clear_runtime_state(svc) -> None:
    """Wipe accumulated lab + network runtime state so reset is a CLEAN baseline —
    simulated NM profiles, unit states, TX/RX logs, and any lhpc network markers do not
    survive a reset."""
    import shutil
    p = svc._paths
    for rel in (("state", "testlab", "nm.json"), ("state", "testlab", "units.json"),
                ("state", "testlab", "tx.jsonl"), ("state", "testlab", "events.log"),
                ("state", "testlab", "commands.log"),
                # populate marker + progress + per-run gate: reset is a clean baseline, so a
                # subsequent start re-populates instead of being suppressed by a stale marker.
                ("state", "testlab", "populated"), ("state", "testlab", "populate.json"),
                ("state", "testlab", "populate-run"),
                ("state", "network-pending.json"), ("state", "network-outcome.json"),
                ("state", "network-retry.json"), ("state", "network-preferred.json")):
        try:
            p.under(*rel).unlink()
        except OSError:
            pass
    try:
        shutil.rmtree(p.under("state", "testlab", "rx-queue"))
    except OSError:
        pass
    # stop-intent tombstones: a reset must not leave a stack pinned "stay stopped" from a prior
    # run (they survive otherwise and suppress boot-restore / the next start).
    try:
        shutil.rmtree(p.under("state", "stop-intent"))
    except OSError:
        pass
    # Per-stack configs (config/stacks/*.toml, incl. band-suffixed <id>@<band>.toml): a reset
    # must not leave the previous run's kiss.toml/graywolf.toml etc. behind, or the "clean
    # baseline" quietly carries stale params. reset re-seeds the ones it needs afterwards.
    try:
        for f in p.under("config", "stacks").glob("*.toml"):
            try:
                f.unlink()
            except OSError:
                pass
    except OSError:
        pass


# ---- fake-process + staging plumbing -----------------------------------------------------


def _testlab_entry() -> list:
    return [sys.executable, "-m", "lhpc_testlab"]


def _gpsd_pid(svc) -> int:
    from . import gpsd
    try:
        pid = int(runtime_fs.read_text_regular(
            svc._paths, gpsd.pid_path(svc._paths), max_bytes=32).strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return 0


def _respawn_gpsd(svc, details: list) -> None:
    from . import gpsd
    old = _gpsd_pid(svc)
    if old:
        try:
            os.kill(old, signal.SIGTERM)
        except OSError:
            pass
    pid = svc._lifecycle()._spawn([*_testlab_entry(), "_gpsd"],
                                  svc._paths.under("logs", "testlab-gpsd.log"))
    try:
        runtime_fs.atomic_write(svc._paths, gpsd.pid_path(svc._paths),
                                f"{pid or 0}\n", 0o600)
    except Exception:
        pass
    details.append(f"  fake gpsd: {'respawned' if pid else 'SPAWN FAILED'}")



def _prepare_pyshims(svc, details: list) -> None:
    import shutil
    dest = svc._paths.under("state", "testlab", "pyshims")
    try:
        shutil.copytree(data_path("pyshims"), dest, dirs_exist_ok=True)
        details.append("  rns radio shims: staged (spidev/gpiod fakes)")
    except OSError as exc:
        details.append(f"  rns radio shims: staging failed ({exc})")
    # meshtasticd sim-radio yaml -> runtime root (the overlay's config_file base points
    # here; the manifest validator accepts only {runtime}/... paths).
    try:
        runtime_fs.ensure_dir(svc._paths, svc._paths.under("state", "testlab"))
        shutil.copy2(data_path("meshtasticd-sim.yaml"),
                     svc._paths.under("state", "testlab", "meshtasticd-sim.yaml"))
    except OSError as exc:
        details.append(f"  meshtastic sim yaml: staging failed ({exc})")


def _prepare_aprs_sink(svc, details: list) -> None:
    """igate SAFETY: compile the APRS-IS DNS interposer (LD_PRELOADed into igate by the
    spawn guard) and start the local sink on 127.0.0.1:14580, so the deprecated igate
    connects to the lab, never euro.aprs2.net."""
    import shutil
    import subprocess
    gcc = shutil.which("gcc") or shutil.which("cc")
    so = svc._paths.under("state", "testlab", "aprs-redirect.so")
    if gcc and not so.exists():
        try:
            runtime_fs.ensure_dir(svc._paths, so.parent)
            subprocess.run([gcc, "-shared", "-fPIC", "-o", str(so),
                            str(data_path("aprs-redirect.c"))], check=True,
                           capture_output=True, timeout=60)
        except Exception as exc:
            details.append(f"  aprs redirect: build failed ({exc})")
    pid = svc._lifecycle()._spawn(
        [sys.executable, str(data_path("aprs_sink.py"))],
        svc._paths.under("logs", "testlab-aprs-sink.log"))
    details.append(f"  aprs-is sink: {'up' if pid else 'SPAWN FAILED'} "
                   "(igate stays off the live network)")


# Headless stacks that `populate` installs+builds so every non-desktop-GUI stack is
# startable out of the box. meshcom & meshtastic are included: on aarch64 reset already
# binary-installed them (populate then skips), but Codespaces are x86-only — there is no
# x86 binary, so populate SOURCE-builds them (meshcom's qemu-xtensa compiles in ~minutes on
# x86). voice & sideband run headless under Xvfb. Interactive/desktop pieces (nomadnet,
# lxmd TUIs) are intentionally excluded.
_POPULATE_STACKS = ("kiss", "graywolf", "igate", "meshcore", "reticulum",
                    "voice", "sideband", "meshcom", "meshtastic")


def populate_marker_path(paths):
    """The completion marker whose presence stops start.sh relaunching populate."""
    return paths.under("state", "testlab", "populated")


def populate_progress_path(paths):
    """Populate progress — the set of stacks already brought up ({ready:[...]})."""
    return paths.under("state", "testlab", "populate.json")



def _populate_state(svc) -> dict:
    try:
        return json.loads(populate_progress_path(svc._paths).read_text())
    except (OSError, ValueError):
        return {"ready": []}


def populate(svc) -> ActionResult:
    """Install + build the remaining headless stacks so they are STARTABLE out of the box,
    preferring OUR binary channel (lhpc-binaries) wherever a stack has one — no big builds
    — and source otherwise. onCreate runs it once (a prebuild bakes the result); the
    devcontainer's start.sh then RE-RUNS it detached, after the web console is already up,
    until the `populated` marker lands — so first paint is instant, stacks light up as each
    finishes, and a build that failed on an earlier pass self-heals. Does NOT auto-start
    them (bands would collide).

    The ready set is tracked in state/testlab/populate.json ({ready}) and a stack counts
    READY only when it is installed AND (binary, or source-built) — so a build failure is
    retried, not silently marked done. The `populated` completion marker (which stops the
    relaunch) is written ONLY when every present stack is ready: a durable failure leaves NO
    marker, so start.sh re-runs populate on the next boot and the stack self-heals once its
    cause is fixed (per-invocation retries are bounded by provision.sh's loop, and start.sh
    re-runs it at most once per container start). `reset` clears both, so a fresh baseline
    re-populates."""
    if not is_active(svc):
        return _refusal(svc)
    st = _populate_state(svc)
    ready = set(st.get("ready", []))
    details: list[str] = []
    present = [s for s in _POPULATE_STACKS if svc.stack(s) is not None]
    for sid in present:
        if sid in ready:
            continue
        try:
            try:
                use_bin = bool(svc.binary_available(sid)[0])
            except Exception:
                use_bin = False
            if not svc.is_installed(sid):
                r = (svc.install(sid, apply=True, source=svc.BINARY_CHANNEL) if use_bin
                     else svc.install(sid, apply=True))
                if not r.ok:
                    details.append(f"  {sid}: install failed ({r.summary})")
                    continue
            # Build ONLY source installs — lhpc refuses to build a binary-installed stack
            # (no source tree), so a build call there is a guaranteed misleading failure.
            if not use_bin:
                b = svc.build(sid, apply=True)
                if not b.ok:
                    details.append(f"  {sid}: build failed ({b.summary})")
                    continue
            if svc.is_installed(sid):
                ready.add(sid)
                details.append(f"  {sid}: ready ({'binary' if use_bin else 'source'})")
        except Exception as exc:
            details.append(f"  {sid}: error ({exc})")
    st["ready"] = sorted(ready)
    runtime_fs.atomic_write(svc._paths, populate_progress_path(svc._paths),
                            json.dumps(st), 0o600)
    # Write the completion marker ONLY when every present stack is ready. A durable failure
    # deliberately leaves NO marker so the next boot's start.sh re-runs populate (cross-boot
    # self-heal). Per-pass looping is bounded by provision.sh, not by giving up here — that
    # is what let a prebuilt-but-incomplete image bake a "done" marker and never heal.
    all_ready = ready.issuperset(present)
    if all_ready:
        runtime_fs.atomic_write(svc._paths, populate_marker_path(svc._paths), "ok", 0o600)
        details.append("  DONE")
        summary = "Populate complete — every present stack installed & startable."
    else:
        missing = [s for s in present if s not in ready]
        details.append(f"  incomplete (not yet ready: {', '.join(missing)}) — retries next pass")
        summary = f"Populate INCOMPLETE — not yet startable: {', '.join(missing)}."
    scenarios.log_event(svc._paths, "populate headless stacks")
    return ActionResult(True, summary, details=details)


def _install_binary_stacks(svc, details: list) -> None:
    """On aarch64 (matching the Pi), pull meshcom and meshtastic from OUR binaries
    (lhpc-binaries) via the production binary channel — the real qemu-system-xtensa and
    meshtasticd, no compile. Non-fatal: on a non-aarch64 host the channel is unavailable
    and the stack is simply left uninstalled (its acceptance leg then skips)."""
    for sid in ("meshcom", "meshtastic"):
        try:
            avail, _why = svc.binary_available(sid)
        except Exception:
            avail = False
        if not avail:
            details.append(f"  {sid}: binary channel unavailable (not aarch64) — skipped")
            continue
        try:
            r = svc.install(sid, apply=True, source=svc.BINARY_CHANNEL)
            details.append(f"  install {sid} (binary): {'ok' if r.ok else r.summary}")
        except Exception as exc:
            details.append(f"  install {sid} (binary): error ({exc})")


def _materialize_sources(svc) -> dict:
    import shutil
    adopt = svc._paths.under("state", "testlab", "adopt")
    runtime_fs.ensure_dir(svc._paths, adopt)
    env = dict(os.environ, GIT_AUTHOR_NAME="testlab", GIT_CONFIG_GLOBAL="/dev/null",
               GIT_AUTHOR_EMAIL="lab@lhpc", GIT_COMMITTER_NAME="testlab",
               GIT_COMMITTER_EMAIL="lab@lhpc")
    commits: dict = {}
    for name in ("loraham-daemon-fake", "radiolib-fake"):
        dest = adopt / name

        def git(*args, _d=dest):
            r = svc._system.runner.run(["git", "-C", str(_d), *args], 30.0, env=env)
            if getattr(r, "returncode", 1) != 0:
                raise RuntimeError(f"git {' '.join(args)}: "
                                   f"{getattr(r, 'stderr', '')[:200]}")
            return (getattr(r, "stdout", "") or "").strip()
        if not (dest / ".git").exists():
            shutil.copytree(data_path(name), dest, dirs_exist_ok=True)
            for sub in dest.rglob("*"):
                if sub.is_file() and (sub.name == sub.parent.name
                                      or sub.suffix == ".sh"):
                    sub.chmod(0o755)
            git("init", "-q")
            git("add", "-A")
            git("commit", "-q", "-m", "testlab fake source")
        commits[name] = git("rev-parse", "HEAD")
    return commits
