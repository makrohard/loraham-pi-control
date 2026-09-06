"""Input validation — the single place every user-controlled value is type-checked
before it is persisted to config or substituted into a command.

This is the remediation for shell injection and path escape: a value that fails
validation never reaches the filesystem or a shell. `shlex.quote()` is deliberately
NOT relied upon — values are validated by type and rejected, not quoted.

`safe_text` is the default for free-form string fields: it rejects control
characters, NUL, newlines, every shell metacharacter, and path separators, so a
value can neither alter a command's argv structure nor escape a config path. The
typed validators (callsign, freq, host, port, band, node_name) add
stricter, field-specific rules on top.
"""

from __future__ import annotations

import ipaddress
import re

MAX_LEN = 256

# Characters that must never appear in a value that may be substituted into a
# command line, plus path separators (no user string field legitimately needs a
# slash — real paths are manifest-owned, not user-entered).
_FORBIDDEN = set(";|&$`<>(){}[]!#*?~\\\"'/\n\r\t\x00")


class ValidationError(ValueError):
    """A user-supplied value failed validation (rejected, never quoted/escaped)."""


def _reject_control(s: str, field: str) -> None:
    if "\x00" in s:
        raise ValidationError(f"{field}: NUL byte not allowed")
    if any(ord(c) < 32 or ord(c) == 127 for c in s):
        raise ValidationError(f"{field}: control characters not allowed")


def safe_text(value, *, max_len: int = MAX_LEN, field: str = "value") -> str:
    """General safe string for any value that may reach a shell command line."""
    s = str(value)
    if len(s) > max_len:
        raise ValidationError(f"{field}: too long (max {max_len})")
    _reject_control(s, field)
    bad = sorted(_FORBIDDEN & set(s))
    if bad:
        raise ValidationError(f"{field}: illegal character(s): {''.join(bad)!r}")
    return s


# YOURCALL is the shell-safe replace-me token the refusal hints print — pasting a hint
# verbatim must never yield a transmitting identity (audit-found: bare "YOURCALL" passed
# the voice shape).
_PLACEHOLDER_BASES = ("N0CALL", "XX0XXX", "YOURCALL")


def _reject_placeholder(s: str, field: str) -> None:
    base = re.split(r"[-/]", s, maxsplit=1)[0]
    if base in _PLACEHOLDER_BASES:
        raise ValidationError(f"{field}: {s!r} is a placeholder, not a real callsign")


def callsign_base(value, *, field: str = "operator callsign", allow_empty: bool = True) -> str:
    """The GLOBAL operator identity: a bare licensed base callsign, 3-6 uppercase ASCII
    alphanumerics — no SSID, no portable suffix ('/P', '-P'), no placeholder. It is the
    fallback every LICENSED stack inherits while its own callsign field is empty, so it
    must stay in the one shape every licensed stack accepts."""
    s = str(value).strip().upper()
    if not s:
        if allow_empty:
            return ""
        raise ValidationError(f"{field}: required")
    # THE INTERSECTION every licensed stack accepts (audit-found dead end: a digit-less
    # "ABCDEF" saved globally, then MeshCom refused it and both printed remedies were the
    # same invalid string). MeshCom's firmware requires the digit-bearing amateur structure
    # (prefix, digit(s), 1-3 suffix letters) — every real base callsign has it, so nothing
    # legitimate is lost by requiring it here.
    # Suffix 1-3 letters — the INTERSECTION the global must satisfy, because MeshCom's pinned
    # firmware accepts at most three (audit-found: widening this to 4 let a global be set that
    # MeshCom would reject, so an "inheritable" global was not actually inheritable everywhere).
    _reject_placeholder(s, field)          # named as a placeholder, not refused on shape
    if not re.fullmatch(r"[A-Z0-9]?[A-Z]?[0-9]+[A-Z]{1,3}", s) or not (3 <= len(s) <= 6):
        raise ValidationError(
            f"{field}: {s!r} — must be your bare base amateur callsign: prefix, digit, then "
            "1-3 letters, 3-6 characters total — use your OWN call; no SSID, '/P' or '-P', "
            "set those per stack")
    return s


def callsign(value, *, field: str = "callsign", allow_empty: bool = True) -> str:
    """An APRS/AX.25 station callsign (chat, Graywolf): base of 3-6 uppercase
    letters/digits plus an optional numeric SSID -1..-15. A bare callsign means SSID 0 —
    an SSID is NOT required. Portable/compound forms ('/P', 'EA4/...') are not valid on
    AX.25 addressing and are refused."""
    s = str(value).strip().upper()
    if not s:
        if allow_empty:
            return ""
        raise ValidationError(f"{field}: required")
    if not re.fullmatch(r"[A-Z0-9]{3,6}(-(1[0-5]|[1-9]))?", s):
        raise ValidationError(
            f"{field}: {s!r} — must be a base callsign of 3-6 letters/digits with an "
            "optional APRS SSID -1..-15 (bare = SSID 0), shaped like N0CALL-10 — use your own call")
    _reject_placeholder(s, field)
    return s


def callsign_voice(value, *, field: str = "voice callsign", allow_empty: bool = True) -> str:
    """The Voice station label: 1-11 uppercase characters of letters, digits, '/' and '-'
    (the app transmits at most 11 characters and would silently truncate more). Portable
    and compound forms (N0CALL/P, N0CALL-P, EA0/N0CALL) are fine within that limit."""
    s = str(value).strip().upper()
    if not s:
        if allow_empty:
            return ""
        raise ValidationError(f"{field}: required")
    # Checked against the pinned Voice source (loraham_voice_v107.c): it copies exactly 11
    # bytes into the transmitted header (`strncpy(h.callsign, CFG.callsign, 11)`), so 11 is
    # the wire limit, and it upper-cases in place. It validates no charset of its own, and its
    # config reader does `strncpy(CFG.callsign, val, 12)` into a 12-byte buffer, which leaves
    # no room for a terminator at exactly 12 — refusing anything longer than 11 keeps that
    # unreachable.
    if len(s) > 11:
        raise ValidationError(
            f"{field}: {s!r} is {len(s)} characters — Voice transmits at most 11")
    if not re.fullmatch(r"[A-Z0-9/-]{1,11}", s):
        raise ValidationError(
            f"{field}: {s!r} — letters, digits, '/' and '-' only, shaped like N0CALL/P "
            "or EA0/N0CALL — use your own call")
    _reject_placeholder(s, field)
    return s


def callsign_meshcom(value, *, field: str = "MeshCom callsign", allow_empty: bool = True) -> str:
    """A MeshCom OPERATOR-IDENTITY callsign — the deliberate identity SUBSET of the pinned
    firmware's own check, NOT a mirror of it: a digit-bearing amateur call (up to two prefix
    characters, at least one digit, 1-3 suffix letters) with an optional numeric suffix
    -1..-99 (e.g. N0CALL, N0CALL-10, N0CALL-99), plus the firmware's one whitelisted real
    station callsign OE2YOTA-1.

    Checked against the firmware source (icssw-org/MeshCom-Firmware, src/regex_functions.cpp):
        ^[0-9A-Z]?[A-Z]?[0-9]+[A-Z][A-Z]?[A-Z]?[%-]?[0-9]?[0-9]?$
    The call shape matches ours exactly. We are deliberately STRICTER in two places, because
    the firmware's suffix is "optional dash, then 0-2 optional digits":
      * a bare trailing dash ("N0CALL-") is an identity with no SSID at all — refused;
      * "-0" is refused, because MeshCom's own documentation requires an SSID of -1..-99 and
        -0 is indistinguishable from no SSID.
    We also refuse every protocol-control token the firmware whitelists (*, H, HG, TEST,
    TESTER, WLNK-1, APRS2SOTA, "BOT GATE"; it separately rejects "DE") — those are message
    routing identifiers, not operator identities."""
    s = str(value).strip().upper()
    if not s:
        if allow_empty:
            return ""
        raise ValidationError(f"{field}: required")
    # The firmware's one whitelisted REAL station callsign (a 4-letter-suffix special event
    # call) is accepted verbatim; its protocol-control tokens are not (see the docstring).
    _reject_placeholder(s, field)
    if s != "OE2YOTA-1" and not re.fullmatch(
            r"[A-Z0-9]?[A-Z]?[0-9]+[A-Z]{1,3}(-[1-9][0-9]?)?", s):
        raise ValidationError(
            f"{field}: {s!r} — MeshCom needs a digit-bearing callsign (prefix, digit, then "
            "1-3 letters) with an optional numeric suffix -1..-99 — use your own call")
    return s


def freq(value, *, field: str = "frequency", lo: float = 1.0, hi: float = 6000.0) -> str:
    s = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,4}(\.[0-9]{1,6})?", s):
        raise ValidationError(f"{field}: invalid frequency {s!r}")
    if not (lo <= float(s) <= hi):
        raise ValidationError(f"{field}: out of range [{lo},{hi}] MHz")
    return s


def host(value, *, field: str = "host") -> str:
    s = str(value).strip()
    if not s or len(s) > 253:
        raise ValidationError(f"{field}: invalid host")
    # Hostname or IPv4/IPv6 literal — letters/digits/.-: only (no metacharacters).
    if not re.fullmatch(r"[A-Za-z0-9.:_-]+", s):
        raise ValidationError(f"{field}: invalid host {s!r}")
    return s


def port(value, *, field: str = "port") -> str:
    s = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,5}", s) or not (1 <= int(s) <= 65535):
        raise ValidationError(f"{field}: invalid port {s!r}")
    return s


def cidr(value, *, field: str = "cidr", allow_ipv6: bool = False) -> str:
    """A single allowed-source CIDR block (network/prefix), parsed and NORMALIZED to
    canonical network form (`192.168.0.5/24` -> `192.168.0.0/24`) via stdlib `ipaddress`.

    IPv6 remote CIDRs are REJECTED by default (the webserver's explicit IPv6 policy: `::1`
    loopback is honored for LOCAL access only; remote IPv6 exposure is not supported this
    milestone). A prefix length is REQUIRED — a bare address is rejected, so a config can
    never silently widen to a single host or (worse) a default route. `0.0.0.0/0` parses
    here (it is a syntactically valid CIDR); the DANGER of exposing it is gated by an
    elevated confirmation in the service/adapters, not by this syntactic check."""
    s = str(value).strip()
    if not s:
        raise ValidationError(f"{field}: empty CIDR")
    if len(s) > 64:
        raise ValidationError(f"{field}: too long")
    _reject_control(s, field)
    # CIDR is digits/hex, dots, colons and exactly one '/prefix' — no shell metacharacters.
    if not re.fullmatch(r"[0-9A-Fa-f:.]+/[0-9]{1,3}", s):
        raise ValidationError(f"{field}: invalid CIDR {s!r} (expected network/prefix)")
    try:
        net = ipaddress.ip_network(s, strict=False)   # normalize; host bits allowed then masked
    except ValueError as exc:
        raise ValidationError(f"{field}: invalid CIDR {s!r}") from exc
    if net.version == 6 and not allow_ipv6:
        raise ValidationError(
            f"{field}: IPv6 remote CIDRs are not supported (IPv4 only) — got {s!r}")
    return str(net)


def bind(value, *, field: str = "bind") -> str:
    """A source-IP allow-list for a no-authentication service port: a bare IPv4 address
    (treated as a single host, /32) OR an IPv4 CIDR. `127.0.0.1` (the default) keeps the
    port on loopback; a LAN CIDR or `0.0.0.0/0` exposes it (the app derives the bind
    address and filters connecting peers by this list). IPv4 only — the consumers
    (loraham-kiss-tnc `--bind`, meshcore `wifi.allow`) reject IPv6, so we do too. A CIDR is
    normalized to its network (`192.168.0.5/24` -> `192.168.0.0/24`); a bare address is
    kept bare. `0.0.0.0/0` parses (the DANGER of a public bind is surfaced by the dashboard
    exposure line, not blocked here)."""
    s = str(value).strip()
    if not s:
        raise ValidationError(f"{field}: empty bind")
    if len(s) > 32:
        raise ValidationError(f"{field}: too long")
    _reject_control(s, field)
    # A bare IPv4 or IPv4/prefix — digits, dots and at most one '/prefix'. No metacharacters,
    # no IPv6 (colons rejected here before ip_network ever sees them).
    if not re.fullmatch(r"[0-9.]+(?:/[0-9]{1,2})?", s):
        raise ValidationError(f"{field}: invalid bind {s!r} (IPv4 address or CIDR)")
    try:
        net = ipaddress.ip_network(s, strict=False)   # bare address -> /32, host bits masked
    except ValueError as exc:
        raise ValidationError(f"{field}: invalid bind {s!r}") from exc
    if net.version != 4:
        raise ValidationError(f"{field}: IPv4 only — got {s!r}")
    # Bare `0.0.0.0` (and the equivalent `/32`) is ambiguous: the common bind idiom MEANS
    # "everyone", but as an allow-list it is a /32 matching NO real peer — and the exposure
    # pill would show a misleading yellow "LAN". Refuse with the two honest spellings instead
    # of silently promoting a fail-closed value to a fully open one.
    if str(net) == "0.0.0.0/32":
        raise ValidationError(f"{field}: bare 0.0.0.0 is ambiguous — use 0.0.0.0/0 to allow "
                              "everyone, or 127.0.0.1 for this Pi only")
    text = str(net)
    if "/" not in s and text.endswith("/32"):
        text = text[: -len("/32")]                    # keep a bare address bare for the UI
    return text


_BANDS = ("433", "868")


def band(value, *, field: str = "band", allow_both: bool = True) -> str:
    s = str(value).strip()
    allowed = _BANDS + (("both",) if allow_both else ())
    if s not in allowed:
        raise ValidationError(f"{field}: invalid band {s!r} (allowed: {', '.join(allowed)})")
    return s


def _printable_utf8(s: str, max_bytes: int, field: str, what: str) -> str:
    if not s:
        raise ValidationError(f"{field}: must not be empty")
    _reject_control(s, field)
    n = len(s.encode("utf-8"))
    if n > max_bytes:
        raise ValidationError(
            f"{field}: {s!r} is {n} UTF-8 bytes — {what} allows at most {max_bytes}")
    return s


def node_name(value, *, field: str = "node name") -> str:
    """A MeshCore advertised node name: 1-31 printable UTF-8 BYTES (the advert packet's
    32-byte app-data budget minus its flags byte; longer values would be silently
    truncated over the air). This is a LOCAL identity — it never inherits the operator
    callsign."""
    return _printable_utf8(str(value).strip(), 31, field, "a MeshCore advert")


def node_long(value, *, field: str = "node name (long)") -> str:
    """A Meshtastic OWNER LONG name: 1-39 printable UTF-8 BYTES (upstream's 40-byte
    buffer incl. NUL; longer values would be silently truncated). LOCAL identity —
    never inherits the operator callsign."""
    return _printable_utf8(str(value).strip(), 39, field, "Meshtastic")


def node_short_name(value, *, field: str = "short node name") -> str:
    """A Meshtastic OWNER SHORT name: 1-4 printable UTF-8 BYTES (upstream buffer is 5
    bytes incl. NUL and silently truncates — which would leave the node advertising an
    identity the operator never chose, so overlong values are REJECTED here)."""
    return _printable_utf8(str(value).strip(), 4, field, "the short name")


_PATH_PLACEHOLDERS = ("{runtime}", "{source}", "{band}")


def path_value(value, *, field: str = "path") -> str:
    """A filesystem path argument (e.g. a socket path, or a generated-config path such as
    meshtasticd's SSL key). Allows `/`, the safe path characters, and the controller-derived
    placeholders {runtime}/{source}/{band} — but rejects shell metacharacters, control/NUL,
    stray braces, and `..` traversal."""
    s = str(value).strip()
    if not s:
        return ""
    if len(s) > MAX_LEN:
        raise ValidationError(f"{field}: too long")
    _reject_control(s, field)
    # Strip ONLY the exact controller placeholders before the metacharacter check; a stray
    # '{'/'}' (not part of one of these tokens) is still rejected.
    probe = s
    for ph in _PATH_PLACEHOLDERS:
        probe = probe.replace(ph, "")
    bad = sorted((_FORBIDDEN - set("/")) & set(probe))
    if bad:
        raise ValidationError(f"{field}: illegal character(s): {''.join(bad)!r}")
    if any(part == ".." for part in s.split("/")):
        raise ValidationError(f"{field}: path traversal not allowed")
    return s


def remote_url(value, *, field: str = "remote") -> str:
    """A Git remote override, restricted to a safe documented policy: https(s) or
    scp-style ssh (git@host:path). Rejects option-like, file://, ext::, control
    chars and metacharacters that could reach Git as flags or shell."""
    s = str(value).strip()
    if not s:
        return ""
    if len(s) > 512:
        raise ValidationError(f"{field}: too long")
    _reject_control(s, field)
    if s.startswith("-"):
        raise ValidationError(f"{field}: option-like value not allowed")
    if any(c in s for c in " \t;|&$`<>()\\\"'\n"):
        raise ValidationError(f"{field}: illegal character(s)")
    if re.fullmatch(r"https://[A-Za-z0-9._~:/?#@!$&'()*+,;=%-]+", s):
        return s
    if re.fullmatch(r"git@[A-Za-z0-9.-]+:[A-Za-z0-9._/-]+", s):
        return s
    raise ValidationError(f"{field}: only https:// or git@host:path remotes are allowed")


def path_component(value, *, field: str = "id") -> str:
    """A single logical id used to build a filename: no separators, no traversal,
    no NUL/control. Used for stack/component ids, band, job-log names."""
    s = str(value)
    _reject_control(s, field)
    if not s or s in (".", ".."):
        raise ValidationError(f"{field}: empty or traversal component {s!r}")
    if "/" in s or "\\" in s or "\x00" in s:
        raise ValidationError(f"{field}: path separator not allowed in {s!r}")
    if not re.fullmatch(r"[A-Za-z0-9._@-]+", s):
        raise ValidationError(f"{field}: illegal character(s) in {s!r}")
    return s


def aprs_symbol(value, *, field: str = "value") -> str:
    """A single APRS symbol character — one printable ASCII glyph (0x21–0x7E), e.g. `&` (I-gate),
    `#` (digi), `R`. APRS symbols are intentionally punctuation, so the generic safe-text rules do
    not apply. Blank is allowed (means: leave the source default). The daemon uses the first char."""
    s = str(value).strip()
    if s == "":
        return ""
    if len(s) != 1 or not (0x21 <= ord(s) <= 0x7E):
        raise ValidationError(f"{field}: must be a single printable APRS symbol character")
    return s


def aprs_filter(value, *, field: str = "value") -> str:
    """An APRS-IS server filter expression, e.g. `r/48.46/9.96/100 p/DL/DK b/N0CALL*`.

    The syntax is built out of `/`-separated tokens, so the generic safe-text rules (which
    forbid `/` and `*`) cannot apply. This stays a strict allow-list — letters, digits and
    the punctuation the filter grammar actually uses (`/ - . , * space`) — so nothing that
    could act as shell or protocol syntax survives, even though the value only ever travels
    as one argv token to a `shell=False` child. Blank means: no filter."""
    s = str(value).strip()
    if s == "":
        return ""
    if len(s) > 256:
        raise ValidationError(f"{field}: too long (max 256)")
    _reject_control(s, field)
    if not re.fullmatch(r"[A-Za-z0-9/*.,\- ]+", s):
        raise ValidationError(f"{field}: invalid APRS-IS filter {s!r} — allowed: letters, "
                              f"digits and / * . , - and spaces")
    # A value is always its own argv token, so one starting with '-' would be read as an
    # OPTION by the consumer, not as data — the same rule validate_param applies to bare
    # positional text. APRS-IS negation filters (`-b/N0CALL*`) are therefore refused here;
    # set those in graywolf's own UI, where no argv boundary is in the way.
    if s.startswith("-"):
        raise ValidationError(
            f"{field}: a filter may not start with '-' (it would be parsed as an option) — "
            f"set negation filters in the application's own UI")
    return s


def sync_word(value, *, field: str = "sync word") -> str:
    """A LoRa sync word: a single byte written as hex (e.g. `0x12`, range 0x00–0xFF). Blank is
    allowed (means: leave the source default)."""
    s = str(value).strip()
    if s == "":
        return ""
    if not re.fullmatch(r"0[xX][0-9a-fA-F]{1,2}", s) or not (0 <= int(s, 16) <= 0xFF):
        raise ValidationError(f"{field}: must be a hex byte like 0x12 (0x00–0xFF)")
    return s


# Named validators selectable from the manifest via a param's `validator` field.
_NAMED = {
    "callsign": callsign,
    "freq": freq,
    "host": host,
    "port": port,
    "cidr": cidr,
    "bind": bind,
    "band": band,
    "node": node_name,
    # The MeshCore node-name RULE without the identity-enforcement meaning `node` carries: the
    # repeater's own name is required only in the repeater modes (checked by the mode helper).
    "repeater_name": node_name,
    "node_long": node_long,
    "node_short": node_short_name,
    "callsign_base": callsign_base,
    "callsign_voice": callsign_voice,
    "callsign_meshcom": callsign_meshcom,
    "path": path_value,
    "aprs_symbol": aprs_symbol,
    "aprs_filter": aprs_filter,
    "sync": sync_word,
    "text": safe_text,
}


def validate_param(param, value) -> str:
    """Validate a RunParam/FileParam value by its declared kind (and optional
    `validator`). Returns the cleaned value or raises ValidationError. flag values
    are returned as-is (their truthiness is handled by emit_param)."""
    name = getattr(param, "name", "value")
    kind = getattr(param, "kind", "str")
    if kind == "flag":
        return str(value)
    if kind == "int":
        s = str(value).strip()
        if not re.fullmatch(r"-?[0-9]{1,9}", s):
            raise ValidationError(f"{name}: not an integer ({value!r})")
        n = int(s)
        lo, hi = getattr(param, "min", None), getattr(param, "max", None)
        if lo is not None and n < lo:
            raise ValidationError(f"{name}: below minimum {lo}")
        if hi is not None and n > hi:
            raise ValidationError(f"{name}: above maximum {hi}")
        return s
    if kind == "float":
        s = str(value).strip()
        if not re.fullmatch(r"-?[0-9]{1,9}(\.[0-9]{1,9})?", s):
            raise ValidationError(f"{name}: not a number ({value!r})")
        # AUDIT IN3: enforce declared min/max like the int branch (was skipped).
        fv = float(s)
        lo, hi = getattr(param, "min", None), getattr(param, "max", None)
        if lo is not None and fv < lo:
            raise ValidationError(f"{name}: below minimum {lo}")
        if hi is not None and fv > hi:
            raise ValidationError(f"{name}: above maximum {hi}")
        return s
    if kind == "enum":
        choices = getattr(param, "choices", ())
        if str(value) not in choices:
            raise ValidationError(f"{name}: {value!r} not in {choices}")
        return str(value)
    # kind == "str": a named validator if declared, else the safe-text default.
    vname = getattr(param, "validator", "") or ""
    fn = _NAMED.get(vname, safe_text)
    cleaned = fn(value, field=name)
    # AUDIT S2: a POSITIONAL free-text param (no `arg` flag prefix, no named validator)
    # emitted as a bare token starting with '-' would be parsed as an option by a GNU
    # target. Reject it — the value stays exactly one data token, never a flag. Named
    # validators (callsign/host/…) already constrain their charset, so only the
    # unconstrained positional-text case needs this guard.
    if not vname and not getattr(param, "arg", "") and cleaned.startswith("-"):
        raise ValidationError(f"{name}: a positional value may not start with '-'")
    return cleaned
