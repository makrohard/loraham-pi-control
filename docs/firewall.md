# Firewalling the Pi

`lhpc` gates its **own** console (bind + source CIDR + client certificate). It cannot gate the
stacks — some open ports on **all interfaces** with no authentication at all. Two answers:

1. **The managed firewall** (this page's focus) — `lhpc` renders an nftables ruleset and you
   apply it with **one sudo command**. It lives in its own `table inet lhpc`, never edits your
   own firewall configuration, and its dashboard status is honest about what it has and has not
   verified.
2. **Do it yourself** — the raw `nft` commands are shown throughout so you can integrate them
   into an existing firewall instead.

Raspberry Pi OS trixie ships **nftables** (no `ufw`, no extra packages). `lhpc` uses native
nftables.

- [What actually listens](#what-actually-listens)
- [Strategy: default-deny vs close-what-we-open](#strategy)
- [The managed firewall — one command](#the-managed-firewall)
- [How it protects your existing configuration](#how-it-protects-your-existing-configuration)
- [Modes: secure-default vs compatibility](#modes)
- [The three status dimensions (and why green is strict)](#status)
- [Scenarios](#scenarios)
- [Reset / undo](#reset)
- [Doing it entirely by hand](#by-hand)

## What actually listens

| Port | Who | Bind | Auth | Managed-firewall row |
|---|---|---|---|---|
| 4403 | meshtasticd API | all interfaces | **none** | **deny-default** (checkbox to allow, with warning) |
| 9443 | meshtasticd web UI | all interfaces | **none** | **deny-default** (reach it via the `:8445` proxy) |
| 8001 | KISS/TCP TNC | loopback default | source allow-list | direct-access row |
| 5000 | MeshCore companion | loopback default | source allow-list | direct-access row |
| 7000 | MeshCom bridge | loopback default | password<sup>†</sup> | direct-access row |
| 18083/12323 | MeshCom QEMU | loopback hardcoded | — | already safe |
| 8443 | lhpc console (nginx) | loopback until exposed | mTLS | proxy ingress (auto-allowed when exposed) |
| 8444/8445 | stack proxies | loopback until exposed | mTLS | proxy ingress (auto-allowed when exposed) |

<sup>†</sup> **unless the stack is installed from the [binary channel](../README.md#binary-channel-prebuilt)**:
the published MeshCom firmware is built without a mesh password, so the bridge runs open and the
firewall model classifies this listener as `auth: none` — the direct-access checkbox then carries
the unauthenticated-exposure warning. Install meshcom from source to run it password-protected.

**meshtastic 4403/9443 are the reason this feature exists**: reachable from anywhere on your
network the moment the stack starts, with no upstream option to bind them to loopback. The
managed firewall blocks them by default; the mTLS-gated `:8445` proxy is the sanctioned path to
the web UI.

<a id="strategy"></a>
## Strategy: default-deny vs close-what-we-open

**Close-what-we-open** (allow everything, add targeted drops for the known-bad ports) fails
*open*: every future stack, package or misconfiguration that opens a port is exposed until
someone notices — the meshtasticd problem recurs forever.

**Default-deny** (block everything, allow only what is wanted) fails *closed*: a new listener is
unreachable until deliberately allowed, the ruleset *is* the inventory of intended exposure, and
a stale ruleset errs toward too-closed (an availability bug you notice) rather than too-open (a
security hole you don't). The cost is that you must enumerate wants — which `lhpc` already does,
because every wanted port comes from its own configuration, and the essential plumbing (loopback,
conntrack, ICMPv6/NDP, DHCP client, mDNS, SSH) is always allowed.

`lhpc`'s **secure-default** mode is default-deny. Its **compatibility** mode is the narrower
close-what-we-open form, for boxes that already run a custom firewall.

<a id="the-managed-firewall"></a>
## The managed firewall — one command

On the dashboard, open **Webserver → Firewall** (also `lhpc firewall`). Pick a mode, tick any
direct-access exceptions, then run the shown command:

```bash
sudo bash ~/loraham-pi-control/config/files/firewall/firewall-apply.sh
```

That script (rendered by `lhpc`, executed by you) installs a small **root-owned** helper and
three systemd units, then applies the ruleset and runs an immediate live check. `lhpc` itself
never runs a privileged command. To verify on demand:

```bash
sudo systemctl start lhpc-firewall-check.service
```

The dashboard's Firewall line then reads one of:

- `Firewall: Active — Secure default · Config ✓ · Boot ✓ · Live ✓`
- `Firewall: Active — Compatibility · unwanted stack ports blocked · Live ✓`
- `Firewall: Changes pending · Config ✗ · Boot ✓ · Live ?`
- `Firewall: Verification unavailable — setup required`
- `Firewall: Live rules missing or mismatched — LHPC protection unverified · Live ✗`

## How it protects your existing configuration

`lhpc` **never edits, overwrites, renames or deletes** your `/etc/nftables.conf`, any file under
`/etc/nftables.d/`, foreign tables/chains, or their service enabled-state. It confines itself to:

- a dedicated `table inet lhpc` (nftables tables are namespaced — applying, flushing or deleting
  ours can never modify rules in yours);
- root-owned artifacts under `/etc/lhpc/` with an explicit ownership record (a random
  installation ID stored in metadata **and** embedded as the table's comment). Before it ever
  replaces, rolls back or deletes the live table it reads that comment back — a table it cannot
  prove is lhpc's is refused (`not-owned`), untouched;
- its own `lhpc-firewall.service` loader, ordered **after** any existing `nftables.service`
  (your `flush ruleset`, if you have one, runs first) — it never enables or modifies that unit.

One honest interaction remains: **any base chain's `drop` beats another table's `accept`**. In
secure-default mode our input chain has `policy drop`, so it adds an effective default-deny over
the whole box — an accept rule in *your* table cannot open a port ours does not allow. If you run
a custom firewall, prefer **compatibility** mode (no default-drop; only lhpc-owned drops for the
unwanted stack ports). The apply script lists any foreign tables it detects and names this
semantic; it changes none of them.

And the converse honesty: an lhpc **allow** never *guarantees* reachability — a later foreign
base chain may still drop the packet.

<a id="modes"></a>
## Modes

- **Secure-default** (recommended for a dedicated box): `policy drop` input chain in the lhpc
  table. Automatically allows actual SSH ports, the external console/proxy ingress you exposed,
  the endpoints you ticked, plus the baseline (loopback, conntrack, ICMP/ICMPv6, DHCPv4 **and**
  DHCPv6 client replies, mDNS). Everything else is dropped.
- **Compatibility** (recommended when a foreign firewall exists): no default-drop policy — only
  lhpc-owned **non-loopback drops for every unselected direct stack listener**. Ticking an
  endpoint suppresses its drop. Your own rules keep authority.

**DHCP client traffic is always allowed in secure-default** (both IPv4 `67→68` and, when IPv6 is
enabled, DHCPv6 `547→546`) — independent of AP mode — so ordinary Ethernet/Wi-Fi lease
acquisition and renewal never break. **Access-Point** server rules (DHCP `68→67` interface-scoped,
DNS on UDP+TCP 53) are opt-in and require an explicit interface and CIDR.

<a id="status"></a>
## The three status dimensions (and why green is strict)

`lhpc` runs unprivileged, so it cannot read the live nftables ruleset directly. Instead a
root-owned checker (a ~1-minute timer, plus an immediate run after apply and after boot) compares
the **live** `table inet lhpc` against the accepted model — a normalized semantic comparison, not
a copied hash — and writes a receipt to `/run/lhpc-firewall/check.json`. The dashboard reads that
receipt (verifying it is root-owned and unforgeable) and reports three **independent** things:

- **Config** — your saved firewall intent matches what was applied.
- **Boot** — the loader + check timer are installed and enabled (survives reboot).
- **Live** — the checker verified the actual kernel table **this boot**, recently.

**Green requires Live.** Declared-and-persistent is *not* enough — after a reboot or a config
change the live state is unverified until the next check attests it, and the dashboard says so.
Freshness uses the boot id plus `CLOCK_BOOTTIME`, so a clock change or a suspend/resume cannot
fake a fresh result.

Because a valid current-boot receipt is required before `lhpc` binds a remote listener, exposing
the console/proxy is gated: if you change the webserver exposure but have not applied the firewall,
the webserver Apply is refused with *Firewall changes pending* and the exact command to run. At
boot, nginx will not bind a remote port until the firewall is verified — on failure it starts
**loopback-only** (recover over an SSH tunnel), and you re-apply.

<a id="scenarios"></a>
## Scenarios

Each is a configuration choice in the Firewall panel that regenerates the ruleset; the equivalent
raw `nft` is shown for the by-hand path.

**Local only** — the default. Nothing exposed; the console is loopback. Reach it with an SSH
tunnel: `ssh -N -L 8443:127.0.0.1:8443 pi@raspberrypi.local`.

**Your LAN** — expose the console to a CIDR: `lhpc webserver expose --cidr 192.168.0.0/24
--confirm-phrase enable-remote` then apply the firewall. The managed rule mirrors the CIDR:
`ip saddr 192.168.0.0/24 tcp dport 8443 accept`.

**Public internet** — forward only 8443 at your router; expose with `--cidr 0.0.0.0/0
--confirm-phrase enable-remote-danger`. Never forward 4403/9443/8001/5000/7000.

**Pi Wi-Fi AP + phone** — enable AP mode in the Firewall panel with the interface and CIDR (e.g.
`wlan0`, `10.42.0.0/24`). It allows AP DHCP (`68→67` on that interface) and DNS (UDP+TCP 53), plus
the console. Issue the phone certificate **before** exposing (see
[wifi-access-point.md](wifi-access-point.md)).

<a id="reset"></a>
## Reset / undo

```bash
sudo bash ~/loraham-pi-control/config/files/firewall/firewall-reset.sh
```

Removes **only** lhpc-owned artifacts (the `table inet lhpc`, the three units, the known files
under `/etc/lhpc/`), restores the prior `nftables.service` enabled-state, and leaves every foreign
file, table and unit exactly as it was. It removes named lhpc files and then `rmdir`s `/etc/lhpc`,
which succeeds only if the directory is empty — so any unexpected file left there is preserved,
never recursively deleted. All ownership and table checks live in the trusted root helper: if the
installed helper is missing, a symlink, not root-owned, or not executable, the reset **refuses**
(exit 13) and asks you to reinstall the current helper (re-run `firewall-apply.sh`) first — so a
live owned table is only ever removed by the proven code path, never stranded. Controller uninstall
refuses while any firewall residual (helper, candidate, metadata, snapshot, journal, or a unit)
remains and points you here first.

<a id="by-hand"></a>
## Doing it entirely by hand

If you would rather integrate the rules into your own firewall, `lhpc firewall --script` prints
the apply script (and `--reset-script` the undo) to stdout — read them, lift the `nft` rules you
want, and manage them yourself. The essential shape of the lhpc table (secure-default) is:

```
table inet lhpc {
    chain input {
        type filter hook input priority filter; policy drop;
        iif "lo" accept
        meta nfproto ipv4 tcp dport 4403 drop      # meshtasticd — unauthenticated
        meta nfproto ipv4 tcp dport 9443 drop
        ct state invalid drop
        ct state established,related accept
        meta l4proto ipv6-icmp accept              # NDP — mandatory for IPv6
        ip protocol icmp accept
        udp sport 67 udp dport 68 accept           # DHCPv4 client
        udp sport 547 udp dport 546 accept         # DHCPv6 client
        ip daddr 224.0.0.251 udp dport 5353 accept # mDNS (v4)
        ip6 daddr ff02::fb udp dport 5353 accept   # mDNS (v6)
        tcp dport 22 accept                        # SSH (or your configured ports)
        # ... your exposed console/proxy/endpoint allows ...
    }
}
```

`lhpc` never edits your firewall configuration; this is the raw material, yours to place.

## Scope and deliberate limitations

The managed firewall gates every path that can bind an externally reachable listener — the web
console, each stack proxy, **and stack starts/restarts**. A start is allowed only when the listener's
**complete scope** (protocol, address family, bind address, port, band and source CIDRs) exactly
matches a modeled candidate scope the live receipt vouches for; an ephemeral bind/port/CIDR change or
a non-default-band scope that isn't modeled is refused with *"Save the setting permanently, apply the
firewall, then start."* A TCP listener with no firewall metadata is treated as exposed and gated
(fail-closed), so a newly added listener can never slip out unprotected.

**Install channels change nothing here.** A stack installed from the binary channel is gated
exactly like a source-built one — the firewall reasons about *listeners*, not about how the
binary got onto the box. The one difference is truthfulness: while a binary receipt is valid the
meshcom bridge is modeled as unauthenticated (see the table above), because the published
firmware has no mesh password to authenticate against.

**Verified across updates.** The installed root helper stamps a revision (a hash of its own source)
into every receipt; after an lhpc update replaces the helper, the old attestation no longer matches,
so the dashboard shows *setup/update required* (never a stale green) until you re-apply. The operator
scripts and the lhpc-owned nginx unit that carries the boot gate are refreshed with the **new**
version's templates automatically — but by the freshly-restarted console *after* the update, not by
the pre-update process (which still holds the old code in memory and would otherwise emit the previous
version). If a self-update would advance into a state where remote web could come up ungated — a
foreign nginx unit while remote access is configured — it stops first and directs you to
`lhpc self-update --repair-integration`.

A few things are intentionally **out of scope** — they add complexity without materially improving
safety:

- **SSH scope is widened, not narrowed.** Automatically preserved SSH access resolves to a wildcard
  allow rather than an exact bind address/family. This can only ever allow *more* SSH access, never
  lock you out — the safe direction. Use `[firewall] ssh_ports` to pin specific ports.
- **Hostname binds** are treated as wildcard rather than resolved to addresses.
- **DHCP client replies** are accepted on any interface, not scoped per-interface.
- **Foreign-firewall detection** happens after the first apply (via the root receipt), so the
  "Compatibility recommended" hint appears once the firewall has run at least once.

None of these weakens the core guarantee: no lhpc-managed non-loopback listener comes up without a
current-boot, live-verified firewall receipt.
