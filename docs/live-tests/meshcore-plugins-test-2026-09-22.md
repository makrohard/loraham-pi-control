# MeshCore plugin manager — live proof, 2026-09-22/23

The openHop plugin manager as part of the repeater (`docs/stacks/meshcore.md`, "Plugins"), run on the
reference box against the maintainer's own repeater. Measured values only; where the run went wrong
by my hand it says so.

**Bench.** Box `lhpc-e293`, Pi Zero 2 W, Lite image, Wi-Fi only. Controller `feature/meshcore-plugins`
(one commit on v0.8.3); MeshCore sources at the v0.8.3 pins (openhop-core `cedb26b`, openhop-repeater
`277f11c`, meshcore-cli `d4eac61`), rebuilt on the box. Stack mode `chat+repeater`, repeater
`CHErptr`, `plugins = on` (the default). Two reboots through the console's own Reboot action inside
the run. Evidence: the node's log (`logs/start-meshcore-node.log`), `pgrep` with the process group
of `meshcore_host`, the marker file, the dashboard's API with a real admin login, `lhpc status`,
`free`, `dmesg`.

## Rows

| # | row | result | evidence |
|---|---|---|---|
| 1 | start with `plugins = on` | pass | start verified in 29 s; node log `plugins=on`, "Plugin manager started (pid …): root state/openhop/plugins, sock …/plugin-manager.sock", upstream "IPC listening"; the manager's PGID equals `meshcore_host`'s; marker `{"version": 1, "boot_id": …}` on disk; the daemon's own "Plugin-manager bootstrap skipped: venv not present" warning still logged, as documented; `GET /api/plugins/` → 200 `{"plugins": []}` (503 before the feature) |
| 2 | install a catalogue plugin, enable, status, logs | pass | `POST /api/plugins/catalogue_install {"id":"openhop.nomad"}` → 200 in 40 s (network, pip); state RUNNING, its venv under `state/openhop/plugins/openhop.nomad/releases/0.1.2/venv/`, its own session, RSS 29 MB; the logs endpoint returns its lines; 121 MB available before, 145 MB with it running; 0 OOM lines |
| 3 | **graceful stop — the acceptance criterion** | pass | `lhpc stack stop meshcore` verified in 18 s (budget 40 s): "Plugin manager stopped" → "openHop repeater host stopped", no watchdog line; afterwards no manager, no plugin process, **marker absent**, plugins root kept |
| 3b | start again | pass | the plugin comes back enabled and RUNNING under the new manager |
| 4 | manager `kill -9` | pass | node log "Plugin manager exited (code -9) — plugins will not be started again until the box is rebooted"; **no restart**; marker stays; the plugin process survived the kill (upstream keeps it in its own session — the documented limitation); dashboard 200, `/api/plugins/` 503 "Connection refused"; `meshcore-node` still running |
| 4 cont. | `lhpc stack restart meshcore` in the same boot | pass | the new host logs "Plugin manager not started: the previous plugin-manager shutdown in this boot was unclean … rebooted"; **no manager spawned**; the repeater runs; `/api/plugins/` 503 |
| 4d | gates on that state (MeshCore stopped, marker present) | pass | `lhpc uninstall meshcore --yes`, `lhpc clean meshcore --purge --yes`, `lhpc update meshcore --yes` and `lhpc _controller-uninstall-prep` each refuse: "unclean MeshCore plugin-manager ownership in this boot … reboot before modifying the MeshCore installation"; `src/openhop-core` untouched |
| 4 after the reboot | old-boot marker → replaced, exactly one manager and one plugin | pass | node log "Plugin-manager marker from an earlier boot found; replacing it", "Plugin manager started"; marker carries the new boot id; the plugin RUNNING; the controller uninstall-prep is quiescent again |
| 4b | host `kill -9` (the `meshcore_host` pid only, manager + plugin running) | pass, as designed | the manager (a sibling in the group, not the host's child) and its plugin **survived**, the marker stayed, LHPC read `meshcore-node stopped`; `lhpc stack start meshcore` in the same boot: the new host (new process group) logged "previous plugin-manager shutdown in this boot was unclean … rebooted" and spawned **no manager** — the only manager on the box was the survivor in the dead host's group, the only plugin the survivor's; the dashboard's `/api/plugins/` reached that surviving manager (200, the plugin RUNNING) — exactly the documented behaviour: no duplicate tree, the old one may stay reachable; `lhpc uninstall meshcore --yes` refused (running). After the reboot the survivors were gone and **boot-restore brought MeshCore back with exactly one manager and one plugin** and a marker carrying the new boot id; a clean stop then left none and no marker; MeshCore started again to leave the box as found |
| 4c | runtime plugin + UI plugin, graceful stop (partial: not two runtime processes) | pass, partial | `waev.outpost` 0.9.395 installs in 9 s but reports `has_runtime: false`, state STOPPED: it is a dashboard-served UI plugin without a process, so the stop ran with one plugin process and one UI plugin; `lhpc stack stop` verified in 17 s, "Plugin manager stopped" before "repeater host stopped", no watchdog line; 172 MB available with both installed |
| 5 | `plugins = off` → on | pass | off: node log without `plugins=on`, no manager, `/api/plugins/` 503 "Plugin manager unavailable" (upstream's banner); on again: manager and plugin back |
| browser | the Plugins page in a real browser through LHPC's mTLS proxy (headless Chromium with a client certificate, `https://<box>:8448`, login as admin, System → Plugins) | pass | no "Plugin manager is unavailable" banner; Installed 2 / Enabled 2 / Running 1 / Failed 0; NOMAD Bridge 0.1.2 RUNNING "Enabled · pid 2761" (Service + UI app); waev:outpost 0.9.395 UI READY "No background service"; Catalogue tab present; no page errors |
| end | box as found | | mode chat+repeater, `plugins = on`, daemon + MeshCore running with one manager and one plugin, 158 MB available, 0 OOM lines |

## Two mistakes of my own during the run, recorded rather than hidden

1. My afternoon API probes had started the manager with `setsid … &` and killed `$!`; setsid forks,
   so three orphan managers survived on the box and the first row 4 killed one of them instead of the
   live manager. They were killed in the repair and the row helpers now count only managers in
   `meshcore_host`'s process group.
2. The first row 4d stopped MeshCore cleanly before `lhpc uninstall meshcore --yes`; the clean stop
   clears the marker, so the gate correctly did not fire and the uninstall removed the box's MeshCore
   sources (config, secrets and profiles preserved by design; sources re-adopted at the pins and
   rebuilt). The correct orphan state is "kill -9 the manager, then stack stop", which the re-run
   used. A later script called the real uninstall-prep while MeshCore ran — it is not a dry run and
   stops every stack — which cost the first attempt at row 4b; the re-run makes no prep call.

## Not proven here

- A graceful stop with two RUNTIME plugins (the catalogue's second plugin is UI-only): the stop
  path is the same per plugin (upstream's sequential 5 s each), and the failure mode past the 8 s
  grace is the safe one (kill, marker stays, no same-boot replacement).
- Memory under a plugin's pip install on a box with less headroom than 121 MB: recorded, not gated.
