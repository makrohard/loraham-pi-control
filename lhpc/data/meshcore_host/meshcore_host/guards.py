"""What keeps LHPC's values LHPC's in the repeater roles, at upstream's own single entry points.

Rule: a write that would CHANGE a value LHPC owns is refused before upstream's code runs; the same
value sent unchanged passes. Structured writes (Glass updates, the dashboard's radio, identity and
import routes) are compared on the data; the CLI refuses its listed commands by name. Each guard
is a subclass bound to the name upstream constructs from, installed before the daemon is built;
`self_check` refuses the start when one of those names is missing in the installed openHop.

Also here, because they hook the same way: every upstream restart becomes LHPC's own stack
restart (detached, `restart_argv`), and the companion chat socket shares the node's single client
slot through the meshcore-cli lock (docs/stacks/meshcore.md "Command-line client").
"""

from __future__ import annotations

import copy
import fcntl
import importlib
import logging
import os
import subprocess
from urllib.parse import parse_qs

from . import openhop_file as ofile
from .config import ConfigError

logger = logging.getLogger("meshcore-host.guards")

NOTICE = "Managed by LoRaHAM Pi Control: change it in LHPC Settings (MeshCore)."
SLOT_BUSY = "Companion in use by meshcore-cli"
RESTARTED = "Restart scheduled by LoRaHAM Pi Control"

# The CLI commands that change an LHPC value (radio, http); each maps to an owned key.
CLI_REFUSED = {
    "set radio ": "radio", "set freq ": "radio", "set tx ": "radio", "tempradio ": "radio",
    "http start": "http", "http stop": "http",
}
# update_radio_config's radio fields, coerced as upstream coerces them.
# Every request field the pinned `update_radio_config` reads (web/api_endpoints.py), classified.
# LHPC's: the radio fields, compared after upstream's own coercion (a value that does not coerce
# is refused), and the `kiss` fields (LHPC removes that section, so any value changes it). The
# user's: the rest. `radio_id` is refused unless absent, None or blank (radios[] are not LHPC's
# model). The classification holds for ONE version of the route: RADIO_ROUTE_SOURCE is the sha256
# (first 16 hex) of its dedented source at the pin, and a host test fails on any other source, so
# a repin that changes the route at all is re-classified (reads through variables included) before
# the digest is updated. A second test checks the direct reads against the classification.
RADIO_ROUTE_SOURCE = "6fe07c866eae40ec"        # openhop_repeater 85fc14a6, update_radio_config
RADIO_FIELDS = {"frequency": float, "tx_power": int, "bandwidth": lambda v: int(float(v)),
                "spreading_factor": int, "coding_rate": int}
RADIO_ROUTE_REMOVED = ("kiss_port", "kiss_baud_rate")
RADIO_ROUTE_USER = ("tx_delay_factor", "direct_tx_delay_factor", "rx_delay_base", "node_name",
                    "owner_info", "latitude", "longitude", "max_flood_hops",
                    "flood_advert_interval_hours", "advert_interval_minutes",
                    "direct_advert_interval_hours", "path_hash_mode", "loop_detect")

# Every coupling point the host binds to or calls: (module, dotted attribute).
COUPLING = (
    ("repeater.config", "load_config"),
    ("repeater.handler_helpers.text", "MeshCLI"),
    ("repeater.handler_helpers.mesh_cli", "MeshCLI._route_command"),
    ("repeater.main", "GlassHandler"),
    ("repeater.data_acquisition.glass_handler", "GlassHandler._apply_config_update"),
    ("repeater.data_acquisition.glass_handler", "GlassHandler._execute_command_action"),
    ("repeater.data_acquisition.glass_handler", "restart_service"),
    ("repeater.web.http_server", "APIEndpoints"),
    ("repeater.web.api_endpoints", "APIEndpoints.update_radio_config"),
    ("repeater.web.api_endpoints", "APIEndpoints.create_identity"),
    ("repeater.web.api_endpoints", "APIEndpoints.update_identity"),
    ("repeater.web.api_endpoints", "APIEndpoints.delete_identity"),
    ("repeater.web.api_endpoints", "APIEndpoints.config_import"),
    ("repeater.web.http_server", "CompanionFrameWebSocket"),
    ("repeater.web.companion_ws_proxy", "CompanionFrameWebSocket.opened"),
    ("repeater.web.companion_ws_proxy", "CompanionFrameWebSocket.closed"),
    ("repeater.service_utils", "restart_service"),
)


def self_check() -> None:
    """Fail closed: one clear message naming the first missing coupling point."""
    for module, dotted in COUPLING:
        try:
            obj = importlib.import_module(module)
            for part in dotted.split("."):
                obj = getattr(obj, part)
        except (ImportError, AttributeError):
            raise ConfigError(f"this openHop version is not supported by this LHPC (missing "
                              f"{module}.{dotted}); use the pinned source") from None


def owned_view(conf: dict, use_gps: bool, chat_key: str) -> dict:
    """Everything LHPC owns in `conf`, for an equality check before and after a write."""
    view = {s: copy.deepcopy(conf.get(s)) for s in ofile.OWNED_SECTIONS + ofile.REMOVED_SECTIONS}
    view.update({k: ofile.get(conf, k) for k in ofile.OWNED_KEYS})
    view["gps"] = copy.deepcopy(conf.get("gps")) if use_gps else ofile.get(conf, "gps.time_sync_enabled")
    view["chat_node"] = copy.deepcopy(chat_entry(conf, chat_key))
    view[ofile.MARKER_KEY] = copy.deepcopy(conf.get(ofile.MARKER_KEY))   # LHPC's own block
    return view


def chat_entry(conf: dict, chat_key: str):
    """LHPC's own chat node among the companions: found by its identity key, never by name."""
    if not chat_key:
        return None
    for entry in ofile.get(conf, "identities.companions") or []:
        if isinstance(entry, dict) and str(entry.get("identity_key", "")).strip().lower() == chat_key:
            return entry
    return None


def deep_merge(target: dict, source: dict) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def changes_owned(conf: dict, updates: dict, merge_mode: str, use_gps: bool, chat_key: str) -> bool:
    """Would the write change anything LHPC owns? Compared in upstream's canonical form: its import
    and its Glass save both run the pinned normalize_modem_config before writing (legacy aliases
    such as pymc_tcp/pymc_usb become modem_tcp/modem_usb), so the guard judges what lands."""
    from repeater.modem_config import normalize_modem_config
    candidate = copy.deepcopy(conf)
    if merge_mode == "replace":
        candidate.update(copy.deepcopy(updates))
    else:
        deep_merge(candidate, updates)
    before, after = (normalize_modem_config(c, warn=False) for c in (conf, candidate))
    return owned_view(before, use_gps, chat_key) != owned_view(after, use_gps, chat_key)


def _refused_cli(command: str) -> bool:
    cmd = command.strip().lower()
    return any(cmd == p.strip() or cmd.startswith(p) for p in CLI_REFUSED)


def _radio_change(conf: dict, data: dict) -> bool:
    rid = data.get("radio_id")
    if rid is not None and not (isinstance(rid, str) and not rid.strip()):
        return True                                    # radios[] are not LHPC's model; fail closed
    if any(key in data for key in RADIO_ROUTE_REMOVED):
        return True
    radio = conf.get("radio") or {}
    for key, coerce in RADIO_FIELDS.items():
        if key in data:
            try:
                changed = coerce(data[key]) != radio.get(key)
            except (TypeError, ValueError, OverflowError):
                changed = True                         # fail closed: never pass an owned field unchecked
            if changed:
                return True
    return False


def _restart(argv: list[str]):
    def restart_service():
        try:
            subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL)
        except OSError as exc:
            logger.error("LHPC restart could not be scheduled: %s", exc)
            return False, f"Restart failed: {exc}"
        logger.info("upstream restart request -> LHPC stack restart scheduled")
        return True, RESTARTED
    return restart_service


def targets_chat_node(data: dict, chat_key: str, chat_name: str, chat_pubkey: str) -> bool:
    """Does an identity request (create/update/delete) name LHPC's chat node? Upstream finds a
    companion by name, `identity_key`/`lookup_identity_key`, or `public_key_prefix`. A key or prefix
    naming the chat node is refused whatever `type` says or omits; a name only for a companion (or a
    request carrying a key), as room servers have their own names."""
    keys = {str(data.get(k) or "").strip().lower() for k in ("identity_key", "lookup_identity_key")}
    prefix = str(data.get("public_key_prefix") or "").strip().lower()
    if (chat_key and chat_key in keys) or (prefix and chat_pubkey and chat_pubkey.startswith(prefix)):
        return True
    companion = str(data.get("type") or "room_server").lower() == "companion"
    name = str(data.get("name") or "").strip()            # upstream strips the name before its lookup
    return bool((companion or data.get("identity_key")) and chat_name and name == chat_name)


def install(conf_ref, *, use_gps: bool, chat_key: str, chat_name: str, chat_pubkey: str,
            cli_lock: str, restart_argv: list[str]) -> None:
    """Bind the guards. `conf_ref()` returns the daemon's live config dict."""
    import cherrypy
    from repeater import main as rmain
    from repeater import service_utils
    from repeater.data_acquisition import glass_handler
    from repeater.handler_helpers import text as text_helper
    from repeater.handler_helpers.mesh_cli import MeshCLI
    from repeater.web import http_server

    class LhpcMeshCLI(MeshCLI):
        def _route_command(self, command, local=False):
            if _refused_cli(command):
                return f"Error: {NOTICE}"
            return super()._route_command(command, local=local)

    class LhpcGlassHandler(rmain.GlassHandler):
        def _apply_config_update(self, updates, merge_mode="patch"):
            if isinstance(updates, dict) and changes_owned(conf_ref(), updates, str(merge_mode).strip().lower(),
                                                           use_gps, chat_key):
                return False, NOTICE
            return super()._apply_config_update(updates, merge_mode)

    api_base = http_server.APIEndpoints

    def _refusal():
        return {"success": False, "error": NOTICE}

    def _names_chat_node(data: dict) -> bool:
        return targets_chat_node(data, chat_key, chat_name, chat_pubkey)

    class LhpcAPIEndpoints(api_base):
        @cherrypy.expose
        @cherrypy.tools.json_out()
        @cherrypy.tools.json_in()
        def update_radio_config(self):
            if _radio_change(conf_ref(), cherrypy.request.json or {}):
                return _refusal()
            return super().update_radio_config()

        @cherrypy.expose
        @cherrypy.tools.json_out()
        @cherrypy.tools.json_in()
        def create_identity(self):
            if _names_chat_node(cherrypy.request.json or {}):
                return _refusal()
            return super().create_identity()

        @cherrypy.expose
        @cherrypy.tools.json_out()
        @cherrypy.tools.json_in()
        def update_identity(self):
            if _names_chat_node(cherrypy.request.json or {}):
                return _refusal()
            return super().update_identity()

        @cherrypy.expose
        @cherrypy.tools.json_out()
        def delete_identity(self, name=None, type=None, lookup_identity_key=None, public_key_prefix=None):
            if _names_chat_node({"name": name, "type": type, "lookup_identity_key": lookup_identity_key,
                                 "public_key_prefix": public_key_prefix}):
                return _refusal()
            return super().delete_identity(name=name, type=type, lookup_identity_key=lookup_identity_key,
                                           public_key_prefix=public_key_prefix)

        @cherrypy.expose
        @cherrypy.tools.json_out()
        @cherrypy.tools.json_in()
        def config_import(self):
            body = cherrypy.request.json or {}
            doc = body.get("config", body) if isinstance(body, dict) else {}
            if isinstance(doc, dict) and changes_owned(conf_ref(), doc, "replace", use_gps, chat_key):
                return _refusal()
            return super().config_import()

    ws_base = http_server.CompanionFrameWebSocket

    class LhpcCompanionFrameWebSocket(ws_base):
        def opened(self):
            qs = self.environ.get("QUERY_STRING", "") if hasattr(self, "environ") else ""
            if parse_qs(qs).get("companion_name", [None])[0] == chat_name:
                fd = os.open(cli_lock, os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                    self.close(code=1008, reason=SLOT_BUSY)
                    return
                self._lhpc_slot_fd = fd
            try:
                super().opened()
            except BaseException:
                self._lhpc_release()                   # the lease never outlives a failed open
                raise
            if getattr(self, "_tcp", None) is None:
                self._lhpc_release()

        def closed(self, code, reason=None):
            try:
                super().closed(code, reason)
            finally:
                self._lhpc_release()

        def _lhpc_release(self):
            fd = getattr(self, "_lhpc_slot_fd", None)
            if fd is not None:
                self._lhpc_slot_fd = None
                os.close(fd)                           # closing the fd drops the flock lease

    text_helper.MeshCLI = LhpcMeshCLI
    rmain.GlassHandler = LhpcGlassHandler
    http_server.APIEndpoints = LhpcAPIEndpoints
    http_server.CompanionFrameWebSocket = LhpcCompanionFrameWebSocket
    restart = _restart(restart_argv)
    service_utils.restart_service = restart
    glass_handler.restart_service = restart
