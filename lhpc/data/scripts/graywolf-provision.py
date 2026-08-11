#!/usr/bin/env python3
"""Provision a running graywolf instance from LHPC stack params (idempotent).

graywolf keeps its configuration in a SQLite database behind its web API, not in a
config file, so the graywolf stack cannot express its settings as FileParams. This
script is the bridge: LHPC passes the params as argv, and every start re-applies
them through graywolf's REST API. Re-running it changes nothing when the live
config already matches, so it is safe as a required post-start step.

What it ensures, in order:
  1. an admin user exists (created on first run; the generated password is written
     to <state-dir>/graywolf-admin.txt, mode 0600, and reused afterwards);
  2. a "KISS-TNC only" channel exists (no audio device, no modem, no PTT);
  3. a tcp-client KISS interface dials the LoRaHAM KISS TNC and is bound to that
     channel, with TX enabled unless --rx-only;
  4. the station callsign is set;
  5. the GPS source follows LHPC's ONE global position plan (gpsd / serial NMEA / none);
  6. the iGate is configured (and left disabled unless --igate).

Exits non-zero with a one-line reason on any failure, so a failed provisioning
fails the stack start instead of leaving graywolf running unconfigured.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

CHANNEL_MODEM_TYPE = "kiss-only"   # audio-less channel serviced by a KISS TNC
KISS_INTERFACE_TYPE = "tcp-client"  # graywolf dials the TNC; the TNC listens
KISS_MODE = "tnc"                  # the peer is a TNC, not another modem
ADMIN_USER = "admin"
CRED_FILE = "graywolf-admin.txt"


class ProvisionError(Exception):
    """A provisioning step failed; the message is the operator-facing reason."""


class Api:
    """Minimal authenticated JSON client for graywolf's REST API."""

    def __init__(self, base: str, timeout: float):
        self.base = base.rstrip("/")
        self.timeout = timeout
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar))

    def call(self, method: str, path: str, payload=None):
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(self.base + path, data=data,
                                     headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise ProvisionError(
                f"{method} {path} -> HTTP {exc.code}: {detail[:200]}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ProvisionError(f"{method} {path} -> {exc}") from exc

        if not body:
            return None
        try:
            return json.loads(body)
        except ValueError as exc:
            raise ProvisionError(f"{method} {path}: response is not JSON") from exc


def wait_ready(api: Api, deadline: float) -> None:
    """Block until the API answers, so provisioning never races graywolf's start."""
    last = "no attempt made"
    while time.monotonic() < deadline:
        try:
            api.call("GET", "/api/auth/setup")
            return
        except ProvisionError as exc:
            last = str(exc)
            time.sleep(1.0)
    raise ProvisionError(f"graywolf API not ready: {last}")


def read_password(state_dir: str) -> str | None:
    # O_NOFOLLOW: a credential-path symlink must never be read THROUGH (it would leak
    # whatever it points at as the password). A symlink leaf raises ELOOP -> None.
    try:
        fd = os.open(os.path.join(state_dir, CRED_FILE), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError:
        return None
    return value or None


def write_password(state_dir: str, password: str) -> None:
    """Persist the generated admin password 0600 — the operator needs it to reach
    the web UI, and the next start needs it to log in again. A write failure is a
    ProvisionError, never a traceback: it must be reported, not swallowed."""
    path = os.path.join(state_dir, CRED_FILE)
    try:
        os.makedirs(state_dir, exist_ok=True)
        # O_NOFOLLOW so a pre-placed credential-path symlink cannot redirect the write
        # (O_TRUNC through a symlink would overwrite its target and 0600 it). A symlink
        # leaf raises ELOOP; write atomically via a temp file + rename so a crash mid-write
        # never leaves a truncated password.
        tmp = path + ".new"
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(password + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        raise ProvisionError(f"cannot write {path}: {exc}") from exc


def authenticate(api: Api, state_dir: str) -> None:
    setup = api.call("GET", "/api/auth/setup") or {}
    password = read_password(state_dir)

    if setup.get("needs_setup") is True:
        # First run on a fresh database: create the one admin account.
        #
        # ORDER MATTERS. The password file is written BEFORE the account exists, because
        # the failure that cannot be recovered from is "account created, password lost":
        # graywolf would then hold an admin nobody can log in as, and the only way out is
        # deleting the config database (and its packet log). Writing first is replay-safe —
        # an existing file is reused as the setup password on the next attempt, so a crash
        # between the two steps converges instead of locking the operator out.
        password = password or secrets.token_urlsafe(18)
        write_password(state_dir, password)
        api.call("POST", "/api/auth/setup",
                 {"username": ADMIN_USER, "password": password})
    elif password is None:
        raise ProvisionError(
            f"graywolf already has an admin account but {CRED_FILE} is missing; "
            f"cannot authenticate (restore the file, or delete the graywolf database "
            f"to re-provision — that discards the packet log)")

    try:
        api.call("POST", "/api/auth/login",
                 {"username": ADMIN_USER, "password": password})
    except ProvisionError as exc:
        if "HTTP 401" not in str(exc):
            raise
        raise ProvisionError(
            f"graywolf rejected the credentials in {CRED_FILE}. LHPC owns this account: "
            f"if the password was changed in the web UI, write the new one into that file "
            f"(one line), or delete the graywolf database to re-provision from scratch "
            f"(that discards the packet log)") from exc


def ensure_channel(api: Api, name: str) -> int:
    """Return the id of the KISS-only channel called `name`, creating or repairing it.

    Two things must hold or the station silently stops working:
      * `modem_type` must be kiss-only — an audio-backed channel has no TNC behind it;
      * the mode must still carry APRS. graywolf has three (`aprs`, `packet`, `aprs+packet`)
        and logs "beacon skipped: channel mode is packet" for a pure packet channel. So a
        packet-only mode is repaired back to `aprs`, while `aprs+packet` is LEFT ALONE: that is
        a deliberate operator choice (connected-mode sessions) and APRS still works.
    """
    for chan in api.call("GET", "/api/channels") or []:
        if chan.get("name") != name:
            continue
        broken = {}
        if chan.get("modem_type") != CHANNEL_MODEM_TYPE:
            broken["modem_type"] = CHANNEL_MODEM_TYPE
        if "aprs" not in str(chan.get("mode", "")):
            broken["mode"] = "aprs"
        if broken:
            # Copy the live channel so operator-set fields survive, then drop the keys the
            # RESPONSE carries but the REQUEST refuses: graywolf's PUT decoder uses
            # DisallowUnknownFields, so echoing `id`, `backing` or `ptt` back turns the repair
            # into a 400. `ptt` is `omitempty`, so it only appears once that channel has a PTT
            # row — i.e. the failure would show up on someone else's box, not a fresh one.
            desired = dict(chan)
            for response_only in ("id", "backing", "ptt"):
                desired.pop(response_only, None)
            desired.update(broken)
            api.call("PUT", f"/api/channels/{chan['id']}", desired)
            fixed = ", ".join(f"{k}={v}" for k, v in sorted(broken.items()))
            print(f"[graywolf] repaired channel {name}: {fixed}")
        return int(chan["id"])

    created = api.call("POST", "/api/channels",
                       {"name": name, "mode": "aprs",
                        "modem_type": CHANNEL_MODEM_TYPE})
    return int(created["id"])


def ensure_kiss_interface(api: Api, host: str, port: int, channel_id: int,
                          allow_tx: bool) -> int:
    """Point a tcp-client KISS interface at the TNC and bind it to `channel_id`.

    graywolf derives the interface name from host+port and enforces it unique, so
    one interface per TNC endpoint is the natural identity here.
    """
    desired = {
        "type": KISS_INTERFACE_TYPE,
        "remote_host": host,
        "remote_port": port,
        "channel": channel_id,
        "mode": KISS_MODE,
        "enabled": True,
        "allow_tx_from_governor": allow_tx,
    }

    existing = None
    stale = []
    for iface in api.call("GET", "/api/kiss") or []:
        if iface.get("type") != KISS_INTERFACE_TYPE:
            continue                       # a server/serial interface is not ours to touch
        if (iface.get("remote_host") == host
                and int(iface.get("remote_port", 0)) == port):
            existing = iface
        elif int(iface.get("channel", 0)) == channel_id:
            # A tcp-client on OUR channel pointing somewhere else can only be a previous
            # tnc_host/tnc_port we provisioned. Left enabled it keeps dialling, and since the
            # TNC accepts a single KISS client it can hold the slot and starve the new
            # interface — so a changed endpoint would never converge across restarts.
            stale.append(iface)

    for iface in stale:
        api.call("DELETE", f"/api/kiss/{iface['id']}")
        print(f"[graywolf] removed stale KISS interface "
              f"{iface.get('remote_host')}:{iface.get('remote_port')}")

    if existing is None:
        created = api.call("POST", "/api/kiss", desired)
        return int(created["id"])

    iface_id = int(existing["id"])
    if any(existing.get(k) != v for k, v in desired.items()):
        api.call("PUT", f"/api/kiss/{iface_id}", desired)
    return iface_id


def apply_station(api: Api, callsign: str) -> None:
    api.call("PUT", "/api/station/config", {"callsign": callsign})


def apply_igate(api: Api, args, channel_id: int) -> None:
    """Write the iGate config, preserving every field LHPC does not own.

    The endpoint is a FULL REPLACEMENT: PUTting only the LHPC-owned keys resets the rest
    (simulation_mode, is_tx_via, software_name/version) on every restart — the opposite of what
    the stack docs promise. So read first, overlay what LHPC owns, and put the whole object
    back. `id` is response-only and is dropped.

    graywolf derives the APRS-IS passcode from the station callsign itself, so no passcode is
    ever handled here.
    """
    current = api.call("GET", "/api/igate/config") or {}
    if not isinstance(current, dict):
        raise ProvisionError("GET /api/igate/config did not return an object")

    desired = dict(current)
    desired.pop("id", None)                       # response-only
    desired.update({
        "enabled": args.igate,
        "server": args.igate_server,
        "port": args.igate_port,
        "server_filter": args.igate_filter,
        "gate_rf_to_is": args.gate_rf_to_is,
        "gate_is_to_rf": args.gate_is_to_rf,
        "rf_channel": channel_id,
        "tx_channel": channel_id,
    })
    api.call("PUT", "/api/igate/config", desired)


def apply_gps(api: Api, args) -> str:
    """Point graywolf's GPS at the position source LHPC resolved, or turn it off.

    graywolf reads gpsd (host/port) and a serial NMEA device natively, so no bridge is needed —
    the controller-resolved plan maps straight onto `/api/gps`. Applied in BOTH directions: a
    global source turned off, or this stack opting out, must actively push `none`, or a station
    enabled earlier keeps reporting a position from its old source.

    The endpoint is a full replacement and its fields are all LHPC-owned here, so unlike the
    iGate config there is nothing of the operator's to preserve. Returns a one-word summary.
    """
    if args.gps_source == "gpsd":
        body = {"source": "gpsd", "serial_port": "", "baud_rate": 9600,
                "gpsd_host": args.gps_host, "gpsd_port": args.gps_port}
        summary = f"gpsd {args.gps_host}:{args.gps_port}"
    elif args.gps_source == "serial":
        body = {"source": "serial", "serial_port": args.gps_device,
                "baud_rate": args.gps_baud, "gpsd_host": "localhost", "gpsd_port": 2947}
        summary = f"serial {args.gps_device}@{args.gps_baud}"
    else:
        # graywolf's own "GPS disabled" payload — the same one its UI sends.
        body = {"source": "none", "serial_port": "", "baud_rate": 9600,
                "gpsd_host": "localhost", "gpsd_port": 2947}
        summary = "off"
    api.call("PUT", "/api/gps", body)
    return summary


def onoff(value: str) -> bool:
    return str(value).strip() in {"1", "true", "yes", "on"}


def main() -> int:
    ap = argparse.ArgumentParser()
    # host:port, not a URL: LHPC passes every value as its own argv token and never
    # merges one into a longer string, so the scheme is added here.
    ap.add_argument("--api-addr", default="127.0.0.1:8080")
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--channel-name", default="LoRaHAM KISS")
    ap.add_argument("--tnc-host", default="127.0.0.1")
    ap.add_argument("--tnc-port", type=int, default=8001)
    ap.add_argument("--callsign", default="N0CALL")
    ap.add_argument("--rx-only", default="0")
    ap.add_argument("--igate", default="0")
    ap.add_argument("--igate-server", default="rotate.aprs2.net")
    ap.add_argument("--igate-port", type=int, default=14580)
    ap.add_argument("--igate-filter", default="")
    ap.add_argument("--gate-rf-to-is", default="1")
    ap.add_argument("--gate-is-to-rf", default="0")
    # Controller-resolved GPS plan (the {gps_args} token) — never an operator choice here: the
    # position source is ONE global decision, and this stack only opts in or out of it.
    ap.add_argument("--gps-source", default="none", choices=("none", "gpsd", "serial"))
    ap.add_argument("--gps-host", default="localhost")
    ap.add_argument("--gps-port", type=int, default=2947)
    ap.add_argument("--gps-device", default="")
    ap.add_argument("--gps-baud", type=int, default=9600)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--ready-timeout", type=float, default=60.0)
    args = ap.parse_args()

    args.rx_only = onoff(args.rx_only)
    args.igate = onoff(args.igate)
    args.gate_rf_to_is = onoff(args.gate_rf_to_is)
    args.gate_is_to_rf = onoff(args.gate_is_to_rf)

    api = Api(f"http://{args.api_addr}", args.timeout)
    try:
        wait_ready(api, time.monotonic() + args.ready_timeout)
        authenticate(api, args.state_dir)
        channel_id = ensure_channel(api, args.channel_name)
        iface_id = ensure_kiss_interface(api, args.tnc_host, args.tnc_port,
                                        channel_id, not args.rx_only)
        apply_station(api, args.callsign)
        gps = apply_gps(api, args)
        apply_igate(api, args, channel_id)
    except ProvisionError as exc:
        print(f"[graywolf] provisioning failed: {exc}", file=sys.stderr)
        return 1

    print(f"[graywolf] provisioned: channel={channel_id} kiss_iface={iface_id} "
          f"tnc={args.tnc_host}:{args.tnc_port} callsign={args.callsign} "
          f"tx={'off' if args.rx_only else 'on'} "
          f"igate={'on' if args.igate else 'off'} gps={gps}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
