"""Snapshot-memo invalidation for public mutating service entries.

`ControllerService.build_snapshot()` is memoized within a request/operation (services.py). The
memo is dropped by the web layer's before_request hook — and, via `@invalidates_snapshot`, by
every PUBLIC mutating service entry, on entry AND on exit (finally):

  * entry: the op never starts its reads from a snapshot another caller populated earlier in
    the same process (CLI sequences, background drivers);
  * exit (finally, so refusal/exception paths too): no read after the op — including a read
    inside an OUTER op that called this one (stop inside start's owner handling) — ever sees
    pre-mutation state. The nested public call re-invalidating on its own exit is exactly what
    makes the outer op's post-mutation `build_snapshot()` recompute.

Invalidation is idempotent and cheap; a dropped memo merely means the next read recomputes,
which is the pre-memo behavior. Plan previews (apply=False) traverse the same entries and
invalidate too — harmless by the same argument.
"""

from __future__ import annotations

import functools


def invalidates_snapshot(fn):
    """Decorate a public mutating service entry: drop the memoized snapshot on entry and exit."""
    @functools.wraps(fn)
    def _wrap(self, *args, **kwargs):
        self.invalidate_snapshot()
        try:
            return fn(self, *args, **kwargs)
        finally:
            self.invalidate_snapshot()
    _wrap.invalidates_snapshot = True        # the marker tests check (`__wrapped__` is any wraps)
    return _wrap


# Every public `ControllerService` entry that is NOT `@invalidates_snapshot`, each with why.
# The rule: an entry that writes anything (files, processes, the host's network, firewall,
# nginx or units) is decorated, whether or not `build_snapshot` reads it today; only reads, the
# few read-path writers below and the NEUTRAL WRITERS (`SNAPSHOT_NEUTRAL_WRITERS`: writes no
# snapshot input observes, each with its paths) stay neutral. tests/core/test_snapshot_memo.py
# fails on a public entry in neither set, in both, or listed here but gone.
SNAPSHOT_NEUTRAL: dict[str, str] = {
    # writers on a read path or inside a decorated op: what they write is no snapshot input
    "active_jobs": "render path: drops dead job records only; no snapshot input",
    "build_snapshot": "the memoized read; writes only the memo",
    "clear_daemon_feed": "feed cursor inside start; not a snapshot input",
    "clear_stale_interactive": "inside start: drops UI markers after its own build_snapshot",
    "daemon_channel_scan": "radio CAD measurement; no durable state",
    "firewall_gate_activation": "gate inside decorated ops; renders only via firewall_render (decorated)",
    "firewall_gate_stack_start": "gate inside decorated ops; renders only via firewall_render (decorated)",
    "invalidate_snapshot": "the invalidation itself; decorating it would recurse",
    "mark_interactive": "UI marker inside start (decorated); not a snapshot input",
    "network_view": "GET: WLAN panel cache; drops an expired pending marker only",
    "prune_logs": "log housekeeping inside build/spawn; logs are no snapshot input",
    "refresh_gps_auto": "per-request hook; drops only the cached config itself",
    "rflog_decode": "GET/XHR: in-memory decode cache only",
    "web_session_secret": "console start: get-or-create of the session key, not a snapshot input",
    # neutral writers: lock files and RF-log rolls that no snapshot input observes (paths below)
    "gps_monitor": "neutral writer: the receiver claim lock while sampling; see SNAPSHOT_NEUTRAL_WRITERS",
    "log_tail": "neutral writer: rolls the native RF trace under its lock; see SNAPSHOT_NEUTRAL_WRITERS",
    "rflog_records": "neutral writer: through log_tail; see SNAPSHOT_NEUTRAL_WRITERS",
    "rflog_tail": "neutral writer: through log_tail; see SNAPSHOT_NEUTRAL_WRITERS",
    # reads (views, predicates, plans, log tails, status)
    "active_bands": "read-only",
    "active_config_consumer": "read-only",
    "allowed_channels": "read-only",
    "auto_install_check_selection": "read-only",
    "auto_install_component_log_chunk": "read-only",
    "auto_install_component_log_seed": "read-only",
    "auto_install_dep_preflight": "read-only",
    "auto_install_log_chunk": "read-only",
    "auto_install_mode": "read-only",
    "auto_install_recovery_reason": "read-only",
    "auto_install_rows": "read-only",
    "auto_install_running": "read-only",
    "auto_install_status": "read-only",
    "auto_install_welcome": "read-only",
    "band_active": "read-only",
    "band_refusal": "read-only",
    "binary_active_override": "read-only",
    "binary_artifact_repair": "read-only",
    "binary_available": "read-only",
    "binary_behind": "read-only",
    "binary_block_reason": "read-only",
    "binary_covers": "read-only",
    "binary_freshness": "read-only",
    "binary_install_refusal": "read-only",
    "binary_receipt_state": "read-only",
    "binary_requirement_class": "read-only",
    "binary_spec": "read-only",
    "binary_target": "read-only",
    "boot_restore_enabled": "read-only",
    "boot_restore_status": "read-only",
    "build_inputs_path": "read-only",
    "build_inputs_text": "read-only",
    "build_remedy": "read-only",
    "channel_error": "read-only",
    "chip_family_for_band": "read-only",
    "classify_request": "read-only",
    "config": "read-only",
    "config_param_fields": "read-only",
    "config_param_groups": "read-only",
    "config_view": "read-only",
    "controller": "read-only",
    "controller_identity_live": "read-only",
    "controller_log_tail": "read-only",
    "controller_status": "read-only",
    "controller_system_deps": "read-only",
    "daemon_channel": "read-only",
    "daemon_feed": "read-only",
    "daemon_params_view": "read-only",
    "daemon_socket_line": "read-only",
    "daemon_view": "read-only",
    "dash_signature": "read-only",
    "dashboard_webservers": "read-only",
    "default_channel": "read-only",
    "dep_note_dismissed": "read-only",
    "dep_note_signature": "read-only",
    "dependency_overview": "read-only",
    "deps_declared": "read-only",
    "deps_report": "read-only",
    "deps_script": "read-only",
    "disk_health": "read-only",
    "display_available": "read-only",
    "doctor": "read-only",
    "effective_identity": "read-only",
    "enforce_identity": "read-only",
    "explain": "read-only",
    "fetched_artifacts_present": "read-only",
    "fetched_version_state": "read-only",
    "file_config_values": "read-only",
    "firewall_boot_gate": "read-only",
    "firewall_candidate": "read-only",
    "firewall_containment": "read-only",
    "firewall_has_log": "read-only",
    "firewall_log_tail": "read-only",
    "firewall_reapply_notice": "read-only",
    "firewall_settings_view": "read-only",
    "firewall_status": "read-only",
    "firewall_update_nginx_preflight": "read-only",
    "gps_block": "read-only",
    "gps_consumers_running": "read-only",
    "gps_enabled_for": "read-only",
    "gps_feed_state": "read-only",
    "gps_liveness_blockers": "read-only",
    "gps_nmea": "read-only",
    "gps_owner_stack": "read-only",
    "gps_plan": "read-only",
    "gps_settings": "read-only",
    "gps_view": "read-only",
    "graywolf_upstream_state": "read-only",
    "gui_fallback_active": "read-only",
    "gui_skipped_stack": "read-only",
    "gui_unavailable_components": "read-only",
    "hardware_block": "read-only",
    "hardware_configured": "read-only",
    "hardware_setup": "read-only",
    "high_power_for_band": "read-only",
    "high_power_state": "read-only",
    "hmac_applies": "read-only",
    "hmac_apply_log_chunk": "read-only",
    "hmac_apply_running": "read-only",
    "hmac_apply_status": "read-only",
    "hmac_binary_block": "read-only",
    "hmac_component_log_chunk": "read-only",
    "hmac_component_log_seed": "read-only",
    "hmac_default_stack": "read-only",
    "hmac_managed_param_hint": "read-only",
    "hmac_status": "read-only",
    "hw_preset_for_band": "read-only",
    "hw_setups": "read-only",
    "identity_refusal_for_values": "read-only",
    "identity_resolution": "read-only",
    "inheritable_global": "read-only",
    "install_blocker": "read-only",
    "install_dep_gate": "read-only",
    "interactive_band": "read-only",
    "is_built": "read-only",
    "is_installed": "read-only",
    "known_working_offer": "read-only",
    "legacy_gps_values": "read-only",
    "list_stacks": "read-only",
    "listener_scopes": "read-only",
    "log_running": "read-only",
    "logs": "read-only",
    "manual_start_command": "read-only",
    "meshcore_identity_candidates": "read-only",
    "meshcore_mode": "read-only",
    "meshcore_mode_display": "read-only",
    "meshcore_position": "read-only",
    "meshcore_running_mode": "read-only",
    "missing_system_deps": "read-only",
    "needs_display": "read-only",
    "network_supported": "read-only",
    "observed_conflicts": "read-only",
    "on_binary_channel": "read-only",
    "operation_band": "read-only",
    "operator_callsign_correction": "read-only",
    "operator_callsign_legacy": "read-only",
    "optional_role": "read-only",
    "page_mode_note": "read-only",
    "pki_normalise_pending": "read-only",
    "power_supported": "read-only",
    "radio_mode": "read-only",
    "radio_mode_block": "read-only",
    "radio_overview": "read-only",
    "restart_marker_payload": "read-only",
    "restart_required": "read-only",
    "restart_required_stacks": "read-only",
    "rflog_job": "read-only",
    "rflog_logging_state": "read-only",
    "rflog_running": "read-only",
    "rflog_switch": "read-only",
    "rflog_switcher": "read-only",
    "run_blockers": "read-only",
    "run_params_for": "read-only",
    "running_band": "read-only",
    "running_lora_stacks": "read-only",
    "running_tasks": "read-only",
    "runs_on_band": "read-only",
    "runtime_root": "read-only",
    "scope_is_loopback": "read-only",
    "security_pill": "read-only",
    "self_update_branch": "read-only",
    "self_update_divergence": "read-only",
    "self_update_ff_blocked": "read-only",
    "self_update_incomplete": "read-only",
    "self_update_local_changes": "read-only",
    "self_update_local_dirty": "read-only",
    "self_update_status": "read-only",
    "source_check_view": "read-only",
    "stack": "read-only",
    "stack_bands": "read-only",
    "stack_config": "read-only",
    "stack_of": "read-only",
    "stack_running": "read-only",
    "stack_web_eligible": "read-only",
    "stack_web_pages": "read-only",
    "stack_web_view": "read-only",
    "stack_web_views": "read-only",
    "stack_webs_overview": "read-only",
    "stacks": "read-only",
    "start_blocking_requirements": "read-only",
    "start_notes": "read-only",
    "status": "read-only",
    "status_versions": "read-only",
    "stop_dependents": "read-only",
    "switch_source_plan": "read-only",
    "system_deps": "read-only",
    "system_stats": "read-only",
    "ui_credentials": "read-only",
    "ui_credentials_list": "read-only",
    "unbuilt_build_deps": "read-only",
    "unbuilt_components": "read-only",
    "uninstall_guard_blocks": "read-only",
    "units_stale": "read-only",
    "update_default_channel": "read-only",
    "update_status": "read-only",
    "updater_integration": "read-only",
    "web_page": "read-only",
    "web_pages": "read-only",
    "webserver_apply_pending": "read-only",
    "webserver_cert_export_bytes": "read-only",
    "webserver_cert_list": "read-only",
    "webserver_log_tail": "read-only",
    "webserver_monitor": "read-only",
    "webserver_server_ca_bytes": "read-only",
    "welcome_note_dismissed": "read-only",
}


# The neutral writers: what each one writes, as globs relative to the runtime root (a trailing
# `/` is a directory it creates), or `termios:gps-receiver` for the tty mode of the configured
# GPS receiver. None of these is a `build_snapshot` input, so the memo stays valid across the
# write. tests/core/test_snapshot_memo.py drives every neutral entry under a write trace (each
# writer must write exactly these; a "read-only" entry nothing) and traces every path, command
# and tty-mode read of a snapshot: it fails the day one of these becomes an input (then the
# entry is decorated instead).
_RF_NATIVE_TRACE = ("state/locks/", "state/locks/rflog-rf-meshtastic.log.lock",
                    "logs/rf-meshtastic.log", "logs/rf-meshtastic.log.1")
SNAPSHOT_NEUTRAL_WRITERS: dict[str, tuple[str, ...]] = {
    # `_gps_direct_sample`: reslock.operation_lock on `claim.gps.<device>` (service_params.py:2274;
    # the lock file stays, the owner record is removed on release), and `_gps_read_device`
    # (:2315) -> gps_bridge._configure_port: termios.tcsetattr on the idle receiver (raw mode,
    # the configured baud; gps_bridge.py:878). The tty mode is no snapshot input: termios is
    # used only in gps_bridge.py (the feed and this sample), never in status.py, probes/ or
    # services.py; the snapshot checks no GPS device at all.
    "gps_monitor": ("state/locks/", "state/locks/claim.gps.*.lock", "state/locks/claim.gps.*.owner",
                    "termios:gps-receiver"),
    # `_rflog_roll_native` (a job log over rflog.MAX_BYTES): the last ~5 MB to `<job>.1`, the
    # live trace truncated in place, under `state/locks/rflog-<job>.lock`. Only meshtasticd's
    # trace is native (rflog.REGISTRY).
    "log_tail": _RF_NATIVE_TRACE,
    "rflog_records": _RF_NATIVE_TRACE,
    "rflog_tail": _RF_NATIVE_TRACE,
}
