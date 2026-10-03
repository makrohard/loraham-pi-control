# Firewalling the Pi

`lhpc` gates its **own** console (bind + source CIDR + client certificate), but some stacks open
ports on **all interfaces** with no authentication. Either let `lhpc` render an nftables ruleset
in its own `table inet lhpc` and apply it with **one sudo command** (the managed firewall), or
lift the raw `nft` rules into your own firewall. Raspberry Pi OS trixie ships nftables; nothing
else is needed.

## Contents

- [What actually listens](#what-actually-listens)
- [Strategy: default-deny vs close-what-we-open](#strategy-default-deny-vs-close-what-we-open)
- [The managed firewall: one command](#the-managed-firewall-one-command)
- [How it protects your existing configuration](#how-it-protects-your-existing-configuration)
- [Modes](#modes)
- [The three status dimensions (and why green is strict)](#the-three-status-dimensions-and-why-green-is-strict)
- [Scenarios](#scenarios)
- [Reset / undo](#reset--undo)
- [Doing it entirely by hand](#doing-it-entirely-by-hand)
- [Scope and deliberate limitations](#scope-and-deliberate-limitations)

## What actually listens

| Port | Who | Bind | Auth | Managed-firewall row |
|---|---|---|---|---|
| 4403 | meshtasticd API | all interfaces | **none** | **deny-default** (checkbox to allow, with warning) |
| 9443 | meshtasticd web UI | all interfaces | **none** | **deny-default** (reach it via its proxy page, `8447` by default) |
| 8001 | KISS/TCP TNC | loopback default | source allow-list | direct-access row |
| 5000 | MeshCore companion | loopback default | source allow-list | direct-access row |
| 8000 | openHop repeater dashboard (MeshCore repeater modes) | loopback hardcoded | password (JWT) | already safe (reach it via its `meshcore-meshcore-node` proxy page) |
| 7000 | MeshCom bridge | loopback default | password<sup>†</sup> | direct-access row |
| 8080 | Graywolf web UI | loopback hardcoded | password | already safe (reach it via its `graywolf` proxy page) |
| 8788 | MeshCore Web UI | loopback hardcoded | **none** | already safe (reach it via its `meshcore` proxy page) |
| 8790 | MeshChat (reticulum) | loopback hardcoded | **none** | already safe (reach it via its `reticulum` proxy page) |
| 4242 | Reticulum client access | loopback (bind locked) | source allow-list (`rns_allow`), no app auth | direct-access row |
| 18083/12323 | MeshCom QEMU | loopback hardcoded | — | already safe |
| 8443 | lhpc console (nginx) | loopback until exposed | mTLS | proxy ingress (auto-allowed when exposed) |
| 8444–8448 (+1 per further page, e.g. 8449) | stack proxy pages | loopback until exposed | mTLS | proxy ingress (auto-allowed when exposed) |

<sup>†</sup> `auth: none` while meshcom is installed from the [binary channel](provenance.md)
([why](stacks/meshcom.md)); the direct-access checkbox then carries the unauthenticated-exposure
warning.

meshtastic 4403/9443 have no upstream option to bind to loopback; the managed firewall blocks
them by default, and the web UI's sanctioned path is its mTLS-gated
[proxy page](webserver.md#stack-web-ui-proxies).

## Strategy: default-deny vs close-what-we-open

**Close-what-we-open** (drop the known-bad ports) fails open: every future listener is exposed.
**Default-deny** fails closed, and `lhpc` knows every wanted port from its own configuration.
Secure-default mode is default-deny; compatibility mode is close-what-we-open, for boxes with a
custom firewall.

## The managed firewall: one command

On the **Apps** page, open the controller row's **Firewall** panel (the Dashboard's Firewall
line links there; CLI: `lhpc firewall`). Pick a mode, tick any direct-access exceptions, then run
the two shown commands — the root script, then the Webserver Apply that activates the listeners
the firewall was gating:

```bash
sudo bash ~/loraham-pi-control/config/files/firewall/firewall-apply.sh
lhpc webserver apply
```

The script (rendered by `lhpc`, run by you) installs a root-owned helper and three systemd
units, applies the ruleset and runs a live check. **Until you run it, nothing is filtered**, and
`lhpc firewall` and the dashboard say *setup required*.

To verify on demand:

```bash
sudo systemctl start lhpc-firewall-check.service
```

The dashboard's Firewall line then reads one of:

- `Firewall: Active — Secure default · Config ✓ · Boot ✓ · Live ✓`
- `Firewall: Active — Compatibility · unwanted stack ports blocked · Config ✓ · Boot ✓ · Live ✓`
- `Firewall: Update required — re-apply the firewall after the update · Config ✓ · Boot ✓ · Live ?`
- `Firewall: Changes pending · Config ✗ · Boot ✓ · Live ?`
- `Firewall: Verification unavailable — setup required`
- `Firewall: Live rules missing or mismatched — LHPC protection unverified · Live ✗`

## How it protects your existing configuration

`lhpc` **never edits, overwrites, renames or deletes** `/etc/nftables.conf`, anything under
`/etc/nftables.d/`, foreign tables/chains, or their service enabled-state. It uses only:

- its own `table inet lhpc` (flushing or deleting it cannot touch your tables);
- root-owned files under `/etc/lhpc/` with an ownership record: a random installation ID stored
  in metadata **and** as the table's comment. Before replacing, rolling back or deleting the
  live table it reads that comment back; a table it cannot prove is its own is refused
  (`not-owned`) and left alone;
- its own `lhpc-firewall.service` loader, ordered **after** any `nftables.service` (whose
  `flush ruleset` therefore runs first), which it never enables or modifies.

**Any base chain's `drop` beats another table's `accept`.** In secure-default mode the lhpc input
chain has `policy drop`, so an accept in *your* table cannot open a port lhpc does not allow;
with a custom firewall prefer compatibility mode. The apply script lists the foreign tables it
detects and changes none of them. Conversely, an lhpc allow cannot guarantee reachability past a
foreign drop.

## Modes

- **Secure-default** (a dedicated box): `policy drop` input chain. Allows the actual SSH ports,
  the console/proxy ingress you exposed, the endpoints you ticked, and the baseline (loopback,
  conntrack, ICMP/ICMPv6, DHCPv4 `67→68` and, with IPv6, DHCPv6 `547→546` client replies on any
  interface, mDNS). Everything else is dropped.
- **Compatibility** (a foreign firewall exists): no default drop; only lhpc-owned non-loopback
  drops for every unselected direct stack listener. Ticking an endpoint suppresses its drop.

**Access-point** server rules (DHCP `68→67` on the AP interface, DNS on UDP+TCP 53) are opt-in and
need an explicit interface and CIDR.

## The three status dimensions (and why green is strict)

`lhpc` runs unprivileged and cannot read the live ruleset. A root-owned checker (a 60 s timer,
plus a run after apply and after boot) compares the **live** `table inet lhpc` semantically with
the accepted model and writes a receipt to `/run/lhpc-firewall/check.json`, which the dashboard
reads after checking it is root-owned. Three independent dimensions:

- **Config**: your saved firewall intent matches what was applied.
- **Boot**: the loader + check timer are installed and enabled.
- **Live**: the checker verified the kernel table **this boot**, recently.

**Green requires Live**: after a reboot or a config change, state is unverified until the next
check. Freshness uses the boot id plus `CLOCK_BOOTTIME`, so a clock change or suspend cannot fake
it.

**Exposure is gated on a valid current-boot receipt.** A webserver or proxy Apply that would
expose something the firewall has not applied is refused with *Firewall changes pending* and the
command to run; that refused Apply completes automatically once Config and Live are verified (a
later edit needs its own Apply). At boot, nginx binds a remote port only once the firewall is
verified; otherwise it starts **loopback-only** — recover over an [SSH tunnel](ssh-tunnel.md)
and re-apply.

## Scenarios

Each is a choice in the Firewall panel that regenerates the ruleset. Exposing the console itself
is the [remote exposure runbook](webserver.md#remote-exposure-runbook).

**Local only** (default): nothing exposed; use an [SSH tunnel](ssh-tunnel.md).

**Your LAN**: expose the console to a CIDR, then apply the firewall. The rule mirrors the CIDR
(`ip saddr 192.168.0.0/24 tcp dport 8443 accept`); with the AP rules enabled it is unscoped
(`meta nfproto ipv4 tcp dport 8443 accept`) and nginx's allow-list keeps the CIDR.

**Public internet**: forward only 8443 at your router (the exposure needs the elevated confirm
phrase). Never forward 4403/9443/8001/5000/7000/8000.

**Pi Wi-Fi AP + phone**: enable AP mode with the interface and CIDR in the Firewall panel or with
`lhpc firewall --ap on --ap-interface wlan0 --ap-cidr 10.42.0.0/24`, then apply — BEFORE the radio
becomes an AP, or the phone gets no DHCP lease and cannot reach the console. The AP itself:
[Wi-Fi](wifi-access-point.md).

## Reset / undo

```bash
sudo bash ~/loraham-pi-control/config/files/firewall/firewall-reset.sh
```

Removes **only** lhpc-owned artifacts (the `table inet lhpc`, the three units, the named files
under `/etc/lhpc/`, then `rmdir /etc/lhpc`, which keeps any unexpected file). The checks run in
the installed root helper: if it is missing, a symlink, not root-owned or not executable, the
reset **refuses** (exit 13) until you re-run `firewall-apply.sh`. Controller uninstall refuses
while any firewall residual (helper, candidate, metadata, snapshot, journal, transition record
or unit) remains and points here.

## Doing it entirely by hand

`lhpc firewall --script` prints the apply script (`--reset-script` the undo); lift the `nft` rules
you want. The shape of the lhpc table (secure-default):

```
table inet lhpc {
    chain input {
        type filter hook input priority filter; policy drop;
        iif "lo" accept
        tcp dport 4403 drop                        # meshtasticd — unauthenticated
        tcp dport 9443 drop
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

## Scope and deliberate limitations

The gate covers the console, each stack proxy **and stack starts/restarts**, whatever the install
channel. A start is allowed only when the listener's complete scope (protocol, address family,
bind address, port, band, source CIDRs) matches a modeled scope the live receipt vouches for:

- a saved bind/port/CIDR change not yet applied: *"Firewall changes pending — the listener was NOT
  started. Apply the firewall first, then start '<target>' again."*
- a scope the applied firewall does not model (e.g. a non-default-band listener): *"The saved
  listener is not covered by the applied firewall — apply the firewall, then start."*

A restart refused by the gate says *restart was not performed; the running stack was left up* with
the same reason; the dry run (`lhpc stack start <id>` / `lhpc stack restart <id>` without `--yes`)
shows the same refusal.

A TCP listener with no firewall metadata is treated as exposed and gated.

**Across updates.** Every receipt carries the installed helper's revision (a hash of its
source); after an update replaces the helper the dashboard shows *Update required* until you
re-apply. The operator scripts and the `lhpc-nginx` unit that carries the boot gate are
refreshed from the new templates by the restarted console after the update. A self-update that
would let remote web come up ungated (a foreign nginx unit while remote access is configured)
stops first and directs you to `lhpc self-update --repair-integration`.

Out of scope:

- **SSH scope is widened, not narrowed.** SSH ports without an explicit `ListenAddress` (unit
  `-p`, `ssh.socket`, live sshd sockets, `[firewall] ssh_ports`) get a wildcard allow; an
  `sshd -T` `ListenAddress` keeps its address and family. Pin ports with `[firewall] ssh_ports`.
- **Hostname binds** are treated as wildcard.
- **DHCP client replies** are accepted on any interface.
- **Foreign-firewall detection** comes from the root receipt, so the "Compatibility recommended"
  hint appears only after the first apply.

No lhpc-managed non-loopback listener comes up without a current-boot, live-verified receipt.
