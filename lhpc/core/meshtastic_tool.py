"""`lhpc meshtastic ...` — a thin, guarded passthrough to the LHPC-managed Meshtastic CLI.

Runs the EXACT pinned Meshtastic Python CLI against the local LHPC-managed node. Every argument is
forwarded UNCHANGED except the few that conflict with LHPC's ownership of the local node:

  * connection selectors (transport/address) are refused — the target is always the local node;
  * LHPC-owned settings on the LOCAL node (LoRa region, node owner name/short incl. `--set-ham`,
    GPS mode, fixed position) are refused and point at the matching LHPC command;
  * factory-reset operations get a warning + normal [y/N] confirmation (`--yes` skips it);
  * broad config mutators (`--configure`/`--import-config` and the channel-URL setters, which carry
    a full LoRa config incl. region) run normally, then LHPC AUTOMATICALLY re-asserts only what it
    owns (region/name/GPS) via the stack's post-start convergence — no prompt, no payload parsing.

Everything else is upstream Meshtastic behaviour, untouched — including the node-free `--support` /
`--test`, which run without the stack. This is NOT a reimplementation of the Meshtastic CLI: it
understands only the handful of arguments above and never enumerates the rest.

Policy is pinned to the managed CLI version (MESHTASTIC_CLI_VERSION); test_meshtastic_tool asserts
it matches the manifest so an upstream bump forces a review of these aliases.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

# Kept in lockstep with the manifest's `meshtastic==<ver>` pin — the drift test enforces it, so an
# upstream bump forces a review of the transport / owned-setting / factory-reset aliases below.
MESHTASTIC_CLI_VERSION = "2.7.11"
MANAGED_CLI_REL = ("build", "tools", "meshtastic-cli", ".venv", "bin", "meshtastic")
LOCAL_API = "127.0.0.1:4403"

# Connection selectors (meshtastic 2.7.11 connection group). Every long flag takes an OPTIONAL
# value (nargs="?"), so `--host`, `--host foo`, `--host=foo` and the `-t`/`-s`/`-b` short aliases
# (optionally with an attached value, e.g. `-tfoo`) all select a transport. A token that is a
# non-empty prefix of one of these long flags either resolves to it (argparse abbreviation) or is
# ambiguous and errors in upstream anyway — and no non-transport option shares a transport flag's
# prefix space — so prefix matching is safe and has no false positives.
_TRANSPORT_LONG = ("--host", "--tcp", "--port", "--serial", "--ble", "--ble-scan")
_TRANSPORT_SHORT = ("-t", "-s", "-b")

# LHPC-owned `--set FIELD VALUE` fields on the local node -> where to change them instead.
_OWNED_SET_FIELDS = {
    "lora.region": "lhpc config meshtastic region <value>",
    "position.gps_mode": "lhpc gps ...   (or the stack's use_gps setting)",
    "position.fixed_position": "lhpc gps ...   (LHPC manages the fixed position)",
}
# LHPC-owned standalone flags on the local node -> where to change them instead.
# `--set-ham` sets a licensed callsign as the node OWNER (upstream setOwner(is_licensed=True)) and
# turns off primary-channel encryption — it mutates the node name LHPC owns. Ham mode is not an
# LHPC-managed setting, so it is refused here rather than given its own subsystem.
_OWNED_FLAGS = {
    "--set-owner": "lhpc config meshtastic node_name <value>",
    "--set-owner-short": "lhpc config meshtastic node_short <value>",
    "--set-ham": "lhpc config meshtastic node_name <value>   (ham mode is not LHPC-managed)",
    "--setlat": "lhpc gps --source fixed --lat <deg>",
    "--setlon": "lhpc gps --source fixed --lon <deg>",
    "--setalt": "lhpc gps --source fixed --alt <m>",
    "--remove-position": "lhpc gps ...   (LHPC manages the fixed position)",
}
# Factory-reset operations: warn + confirm. `--factory-reset-device` also clears BLE bonds / PKI.
_FACTORY_FLAGS = ("--factory-reset", "--factory-reset-config", "--factory-reset-device")
_FACTORY_DEVICE = "--factory-reset-device"

# Broad LOCAL mutations that can silently overwrite LHPC-owned region/name/GPS. We do NOT parse their
# payload; we run them normally and then AUTOMATICALLY reuse LHPC's post-start convergence to
# reassert only what LHPC owns. Besides --configure/--import-config (full local config), the channel
# URL setters count too: upstream setURL() sends the URL's ENTIRE lora_config (incl. region) to the
# node even in add-only mode, so `--ch-add-url` can change region behind LHPC's back.
_RECONVERGE_FLAGS = ("--configure", "--import-config", "--seturl", "--ch-set-url", "--ch-add-url")
# Exact upstream options that are PREFIXES of a reconverge flag but are their OWN distinct commands:
# `--ch-add` adds a channel and `--ch-set` edits channel fields — neither carries a full LoRa config,
# so they pass through untouched. argparse resolves an exact flag to itself; we mirror that, so only
# a UNIQUE abbreviation of the `-url` variant (e.g. `--ch-add-u`) still triggers reconvergence.
_RECONVERGE_EXACT_SIBLINGS = frozenset({"--ch-add", "--ch-set"})


@dataclass
class Decision:
    """Outcome of classifying the forwarded argv against LHPC's boundary."""

    action: str                       # "pass" | "block" | "confirm"
    message: str = ""                 # block: the error to print; confirm: the warning to show
    device_reset: bool = False        # confirm: stronger wording (BLE bonds / PKI cleared)
    bulk_config: bool = False         # pass/confirm: guide a re-apply of LHPC settings afterwards


# ---------------------------------------------------------------------------
# Token helpers (structural — never substring matching)
# ---------------------------------------------------------------------------

def _opt(tok: str) -> tuple[str, bool]:
    """Return (option_name, is_option). For `--flag=value` the name is the part before `=`."""
    if not tok.startswith("-") or tok == "-" or tok == "--":
        return tok, False
    name = tok.split("=", 1)[0] if tok.startswith("--") else tok
    return name, True


def _prefix_of(opt: str, flag: str) -> bool:
    """True if `opt` (a `--long` option) is a non-empty prefix of `flag` (argparse abbreviation)."""
    return opt.startswith("--") and len(opt) > 2 and flag.startswith(opt)


def _is_transport(opt: str, tok: str) -> bool:
    if opt.startswith("--"):
        return any(_prefix_of(opt, f) for f in _TRANSPORT_LONG)
    # short: exact `-t`/`-s`/`-b`, or with an attached value like `-tfoo`
    return len(tok) >= 2 and tok[:2] in _TRANSPORT_SHORT


def _norm_field(field_name: str) -> str:
    """Normalise a `--set` field to snake_case + lowercase (upstream accepts snake OR camelCase).

    Split only true camelCase humps (a lowercase letter followed by an uppercase one), so a
    word-start capital after `.` is not turned into a spurious `_`, and lowercase throughout so
    stray capitalisation cannot slip an owned field past the guard.
    """
    out = []
    for i, ch in enumerate(field_name):
        if ch.isupper() and i > 0 and field_name[i - 1].islower():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


_BROADCAST_ADDR = 0xFFFFFFFF  # meshtastic broadcast — reaches the LOCAL node too


def _is_broadcast(val: str) -> bool:
    """True if `val` is the Meshtastic broadcast address (0xffffffff) as !hex / 0xhex / decimal."""
    if val.startswith("!"):
        raw, base = val[1:], 16
    elif val.startswith(("0x", "0X")):
        raw, base = val, 16
    else:
        raw, base = val, 10
    try:
        return int(raw, base) == _BROADCAST_ADDR
    except ValueError:
        return False


def _dest_value(argv: list[str]) -> str | None:
    """The value of the LAST `--dest` (argparse `store` keeps the last), or None if absent."""
    val: str | None = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        name = tok.split("=", 1)[0]
        if name == "--dest" or _prefix_of(name, "--dest"):
            if "=" in tok:
                val = tok.split("=", 1)[1]
            elif i + 1 < len(argv):
                val = argv[i + 1]
                i += 1
            else:
                val = ""
        i += 1
    return val


def _has_remote_dest(argv: list[str]) -> bool:
    """True if the user explicitly targets a REMOTE node via --dest (a `!`/`0x`/numeric id).

    LHPC owns only the LOCAL node, so owned-setting guards are skipped when a remote node is the
    destination. `^local`, `^all`, the broadcast address, or no --dest all mean the local node is
    (also) targeted. The LAST --dest wins to match upstream argparse `store`, so a trailing local
    --dest cannot be masked by an earlier remote one (and vice versa).

    Residual (documented, accepted): a --dest that names the LOCAL node by its OWN numeric/`!hex`
    id is treated as remote and skips the guards, yet applies locally. Closing it would require the
    wrapper to resolve the local node id (a live connection). This is a deliberate act by an
    operator who already has shell access — the guard targets accidental/casual local mutation, not
    a determined bypass — so it is documented rather than blocked.
    """
    val = _dest_value(argv)
    if val is None:
        return False
    val = val.strip()
    if not val or val.startswith("^"):    # ^local / ^all -> local is (also) targeted
        return False
    if _is_broadcast(val):                # !ffffffff / 0xffffffff / 4294967295 -> reaches local too
        return False
    return val.startswith(("!", "0x")) or val.lstrip("-").isdigit()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _match_owned_flag(name: str) -> str | None:
    """The owned flag this option resolves to (exact or abbreviation), or None. `--set` is handled
    separately (with its FIELD), so it is never matched here."""
    if name == "--set":
        return None
    for flag in _OWNED_FLAGS:
        if name == flag or _prefix_of(name, flag):
            return flag
    return None


def _match_factory_flag(name: str) -> str | None:
    """`--factory-reset-device` for a device reset, `--factory-reset` for a config reset, else
    None. Only `--factory-reset-device` lives under the `--factory-reset-d…` prefix."""
    if not any(name == f or _prefix_of(name, f) for f in _FACTORY_FLAGS):
        return None
    if name == _FACTORY_DEVICE or name.startswith("--factory-reset-d"):
        return _FACTORY_DEVICE
    return "--factory-reset"


def classify(argv: list[str]) -> Decision:
    """Classify forwarded argv (WITHOUT the wrapper's own options) against LHPC's boundary."""
    remote = _has_remote_dest(argv)
    factory: str | None = None
    bulk = False
    i = 0
    while i < len(argv):
        name, is_opt = _opt(argv[i])
        if not is_opt:
            i += 1
            continue
        if _is_transport(name, argv[i]):
            return Decision(
                "block",
                "connection/transport selection is managed by LHPC — `lhpc meshtastic` always "
                "targets the local node. To operate another Meshtastic device, run a standalone "
                "Meshtastic CLI outside `lhpc meshtastic`.",
            )
        if name == "--set":
            # Handle the exact `--set` FIELD VALUE setter FIRST (before the abbreviation-based checks
            # below) — `--set` is a PREFIX of `--seturl`/`--set-owner`/… but argparse resolves an
            # exact flag to itself, so `--set` must never be mistaken for one of those. Form is either
            # `--set FIELD VALUE` (3 tokens) or `--set=FIELD VALUE` (=-form; VALUE is still separate,
            # upstream --set is nargs=2). Advance PAST the whole option so a VALUE beginning with `-`
            # is not re-inspected as an option.
            tok = argv[i]
            if "=" in tok:
                field, consume = tok.split("=", 1)[1], 2   # --set=FIELD  VALUE
            else:
                field, consume = (argv[i + 1] if i + 1 < len(argv) else ""), 3  # --set FIELD VALUE
            norm = _norm_field(field)
            if not remote and norm in _OWNED_SET_FIELDS:
                return Decision("block", _owned_msg(norm, _OWNED_SET_FIELDS[norm]))
            i += consume
            continue
        # Broad local mutators need reconvergence — but only when they hit the LOCAL node (a remote
        # --dest routes elsewhere, so LHPC's local settings are untouched). Exact `--ch-add`/`--ch-set`
        # are their own commands (they are excluded), so only the -url variants / their unique
        # abbreviations reconverge.
        if (not remote and name not in _RECONVERGE_EXACT_SIBLINGS
                and any(name == f or _prefix_of(name, f) for f in _RECONVERGE_FLAGS)):
            bulk = True
        if not remote:
            owned = _match_owned_flag(name)
            if owned:
                return Decision("block", _owned_msg(owned.lstrip("-"), _OWNED_FLAGS[owned]))
        fac = _match_factory_flag(name)
        if fac is not None and (factory is None or fac == _FACTORY_DEVICE):
            factory = fac
        i += 1

    if factory is not None:
        device = factory == _FACTORY_DEVICE
        return Decision("confirm", _factory_msg(device), device_reset=device, bulk_config=bulk)
    return Decision("pass", bulk_config=bulk)


def _owned_msg(field_name: str, guidance: str) -> str:
    return (f"{field_name} is managed by LHPC for the local node.\n\nUse:\n  {guidance}\n\n"
            f"(A remote node targeted with --dest is not affected.)")


def _factory_msg(device: bool) -> str:
    extra = ("\nThis also clears the device's BLE bonds and PKI keys (upstream "
             "--factory-reset-device semantics)." if device else "")
    return ("WARNING: this resets Meshtastic configuration.\n"
            "LHPC-managed Meshtastic settings (region / name / GPS) may need to be reapplied."
            + extra + "\n\nProceed?")


# ---------------------------------------------------------------------------
# Managed executable + wrapper argv
# ---------------------------------------------------------------------------

def resolve_managed_cli(runtime_root) -> str | None:
    """The LHPC-provisioned Meshtastic CLI, or None if it is not built. No PATH/unmanaged fallback."""
    p = os.path.join(str(runtime_root), *MANAGED_CLI_REL)
    return p if os.path.isfile(p) and os.access(p, os.X_OK) else None


def split_wrapper_args(argv: list[str]) -> tuple[bool, list[str]]:
    """Extract the ONLY LHPC wrapper option (`--yes`) — upstream 2.7.11 has none — and return
    (yes, forwarded). `--yes` is never passed upstream. A `--yes` AFTER a `--` end-of-options
    marker is data, not the flag, so it is forwarded verbatim."""
    yes = False
    fwd: list[str] = []
    end_of_opts = False
    for tok in argv:
        if not end_of_opts and tok == "--yes":
            yes = True
            continue
        if tok == "--":
            end_of_opts = True
        fwd.append(tok)
    return yes, fwd


# Upstream actions handled BEFORE any Meshtastic interface is created, so they need no local node:
# help/version print and exit; --support prints support info and exits; --test runs its own USB
# two-radio serial test (meshtastic.test.testAll(), not the LHPC TCP node). --reply/--tunnel are NOT
# here — those use the connected interface, so they correctly require the stack running.
_NO_NODE_LONG = ("--help", "--version", "--support", "--test")


def _is_no_node_action(tok: str) -> bool:
    """True for -h and the no-node long actions / their argparse abbreviations (`--ver`, `--sup`)."""
    return tok == "-h" or any(_prefix_of(tok, f) for f in _NO_NODE_LONG)


def _needs_node(forwarded: list[str]) -> bool:
    """Node ops need the API up. If ANY token is a terminal no-node action (help/version/support/
    test), upstream exits BEFORE constructing an interface, so the whole command is node-free —
    regardless of accompanying modifiers like `--debug`. Those also skip the forced local --host, so
    --test reaches its own USB serial test. (A unique transport abbreviation like `--ho`/`--tc` is
    NOT a no-node prefix, so it still hits the transport guard; only ambiguous `--t`/`--h`, which
    upstream rejects anyway, fall through here.)"""
    return bool(forwarded) and not any(_is_no_node_action(t) for t in forwarded)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(argv: list[str], *, runtime_root, stack_running: Callable[[str], bool],
        confirm: Callable[[str], bool], reconverge: Callable[[], bool] | None = None,
        out=None, err=None) -> int:
    """Guarded foreground passthrough. Returns the upstream CLI's exit code, or an LHPC code.

    `reconverge` re-asserts LHPC-owned settings (region/name/GPS) via the stack's post-start path;
    it is called automatically after a broad LOCAL mutator (see _RECONVERGE_FLAGS), returning True
    on success. When None, reconvergence is skipped (with a warning to run it manually)."""
    out = out or sys.stdout
    err = err or sys.stderr

    exe = resolve_managed_cli(runtime_root)
    if exe is None:
        err.write("ERR  Managed Meshtastic CLI is not installed/built.\n\n"
                  "Next:\n  lhpc build meshtastic\n")
        return 1

    yes, forwarded = split_wrapper_args(argv)

    # help/version (and a bare invocation) only need the binary — no guards, no readiness.
    if not _needs_node(forwarded):
        return _exec_foreground(exe, forwarded)

    decision = classify(forwarded)
    if decision.action == "block":
        err.write(f"ERR  {decision.message}\n")
        return 2

    if not stack_running("meshtastic"):
        err.write("ERR  Meshtastic is not running.\n\nNext:\n  lhpc stack start meshtastic\n")
        return 1

    if decision.action == "confirm" and not yes and not confirm(f"\n{decision.message} [y/N] "):
        out.write("Aborted.\n")
        return 1

    # Force the local node. PREPEND the transport so a user `--` end-of-options marker in `forwarded`
    # cannot demote it to a positional; upstream `--host host:port` splits the port itself. User
    # transport flags were already refused by classify(), so there is never a competing --host.
    final = ["--host", LOCAL_API, *forwarded]
    if decision.bulk_config:
        # We do not parse Meshtastic's config/URL formats. Run the mutator normally, then reuse
        # LHPC's post-start convergence to reassert ONLY what LHPC owns (region/name/GPS). Run via
        # subprocess (not execv) so control returns. Reconvergence is a VERIFIED part of the
        # operation, not best-effort:
        #   * Ctrl-C in the mutator OR in reconvergence -> clean rc 130 (no traceback);
        #   * upstream failed -> preserve its rc (reconvergence still attempted);
        #   * upstream succeeded but reconvergence is NOT PROVEN -> rc 1 (never a false green).
        interrupted = False
        rc = 0
        try:
            rc = subprocess.run([exe, *final], check=False).returncode
        except KeyboardInterrupt:
            interrupted = True
        # Always attempt reconvergence — a partial/failed/interrupted mutation is exactly when
        # LHPC-owned values may have drifted. A Ctrl-C here surfaces as a clean 130, not a traceback.
        reconverged = False
        try:
            reconverged = _reconverge(reconverge, out, err)
        except KeyboardInterrupt:
            interrupted = True
        if interrupted:
            return 130
        if rc != 0:
            return rc                      # upstream failure preserved (reconvergence was attempted)
        if not reconverged:
            err.write("\nERR  the change was applied but LHPC could NOT verify region/name/GPS were "
                      "reasserted.\n\nRe-apply:\n  lhpc stack poststart meshtastic\n")
            return 1                       # upstream 0 but reconvergence unproven -> not a success
        return 0
    return _exec_foreground(exe, final)


def _reconverge(reconverge: Callable[[], bool] | None, out, err) -> bool:
    """Reassert LHPC-owned region/name/GPS (incl. node identity — the caller uses require_all).
    Returns True ONLY when reconvergence is PROVEN. A KeyboardInterrupt propagates so run() renders a
    clean 130; any other failure returns False (never masked as success)."""
    if reconverge is None:
        err.write("\nWARN  no reconvergence hook — reassert manually:\n"
                  "  lhpc stack poststart meshtastic\n")
        return False
    try:
        ok = bool(reconverge())
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # a reconvergence failure must fail closed, never a false green
        err.write(f"\nWARN  reasserting LHPC-managed settings failed ({exc}).\n")
        return False
    if ok:
        out.write("\nLHPC  Reasserted LHPC-managed settings (region / name / GPS).\n")
    else:
        err.write("\nWARN  LHPC could not verify region/name/GPS were reasserted.\n"
                  "Run:\n  lhpc stack poststart meshtastic\n")
    return ok


def _exec_foreground(exe: str, forwarded: list[str]) -> int:
    """Replace this process with the managed CLI: native stdin/stdout/stderr, Ctrl-C and exit code.

    execv holds no LHPC lock and never returns, so a long `--listen`/`--tunnel` is a plain
    foreground process. Falls back to a stdio-inheriting subprocess if execv is unavailable.
    """
    argv = [exe, *forwarded]
    try:
        os.execv(exe, argv)  # noqa: S606 (deliberate: exec the managed CLI directly, no shell)
    except OSError:
        return subprocess.run(argv, check=False).returncode
