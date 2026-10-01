# Stack: Reticulum (RNS)

A [Reticulum](https://reticulum.network) node driving the LoRa radio **directly over SPI** on 868
(default) or 433 MHz. No rnoded, no RNode firmware, no KISS layer: one LoRa packet is one RNS
packet. The driver is [loraham-rns-interface](https://github.com/makrohard/loraham-rns-interface).

| | |
|---|---|
| Components | `rns` (main — owns the radio and the shared instance; runs `loraham-rns-node`) · `rns-lora-interface` (library, build-time: the direct-SPI driver, RNS interface and service runner) · `nomadnet` (optional, interactive) · `lxmd` (optional; propagation **off** by default) · `sideband` (optional desktop GUI, `--with-gui` only) · `meshchat` (optional browser GUI) |
| Source / pin | `src/reticulum` ← `markqvist/Reticulum` · `src/loraham-rns-interface` ← `makrohard/loraham-rns-interface` · `src/nomadnet` · `src/lxmf` · `src/sideband` (installed as `sbapp==1.9.2` from PyPI — a source install drops every `.kv` layout) · `src/meshchat` ← `liamcottle/reticulum-meshchat` |
| Build | a venv with `--system-site-packages` (SPI/GPIO bindings from the `python3-libgpiod` + `python3-spidev` packages, so the node needs no compiler); Reticulum + the driver; the interface copied to `state/reticulum/interfaces/LoRaSPIInterface.py`; an import probe before the marker |
| Run | `.venv/bin/loraham-rns-node --config <runtime>/state/reticulum --interface LoRa --ready-file <runtime>/state/reticulum/ready --client-allow <allow-list>` — it exits rather than staying up without a radio |
| Endpoints | shared instance `127.0.0.1:37428` · instance control `:37429` · client access `:4242` · optional outbound TCP to an internet peer (no listener) · MeshChat `127.0.0.1:8790` (via the LHPC proxy) · readiness = the `ready` file, written only after the node owns the instance **and** the radio is online |
| Config | `<runtime>/state/reticulum/config` (0400) from `lhpc/data/bases/reticulum.conf`, regenerated on every start — change it through lhpc. Read-only even to its owner, so a client that offers to edit interfaces cannot; lhpc replaces it by rename |
| Resources | `loraham.radio.868` + `.433` exclusive · `spi.bus.0` cooperative · `spi.bus.0.unlocked` exclusive · `tcp.port.37428` / `.37429` / `.4242` / `.8790` exclusive |
| System | `/dev/spidev0.0` (`dtoverlay=spi0-0cs`); `spi` + `gpio` groups; `python3-libgpiod`, `python3-spidev` |
| State | `state/reticulum` (transport identity, path tables, config), `state/meshchat`, `state/nomadnet`, `state/lxmd`, `state/sideband` (their LXMF identities = the addresses contacts know). Updates and `uninstall` keep them; `lhpc clean reticulum --purge` removes them, so a reinstall gets new addresses |
| Install channel | source only |

## Contents

- [Settings](#settings)
- [Hardware](#hardware)
- [Position (GPS)](#position-gps)
- [Clients](#clients)
- [Internet and transport](#internet-and-transport)
- [Band limits](#band-limits)
- [Notes](#notes)
- [Conflicts](#conflicts)

## Settings

`lhpc config reticulum` or the stack's Settings panel:

| setting | default | notes |
|---|---|---|
| `frequency` | 868 500 000 Hz / 434 500 000 Hz | per band — see [Band limits](#band-limits) |
| `bandwidth` · `spreadingfactor` · `codingrate` | 125 000 Hz · 8 · 5 | BW 62.5–500 kHz, SF 7–12 (SF6 needs implicit-header mode, unsupported), CR 4/5–4/8 |
| `txpower` | 14 dBm (868) / 10 dBm (433) | the driver refuses anything above the band policy: 14 dBm on 868, 10 dBm on 433 |
| `rnode_framing` | no | talk to RNode-firmware devices: the driver adds the RNode header byte to every frame, splits packets at 254 bytes and programs the RNode preamble for the SF/BW (18 at SF8/BW125; an SX127x hears an RNode only with at least that). A framed box and a bare box cannot hear each other. The start refuses, before anything is stopped, while the built driver predates the switch (`lhpc update rns-lora-interface` + `lhpc build reticulum`). In the RF log a split packet is two lines; Decrypt strips and reassembles them |
| `airtime_limit_short` / `airtime_limit_long` | 868: 33 % (15 s) / 1 % (1 h) · 433: 33 % / 10 % | advanced — the short window is a burst guard (Reticulum's RNode example value); the long one is the legal duty cycle |
| `rns_allow` | `127.0.0.1` | client-access allow-list; drives the managed firewall |
| `enable_transport` | `No` | relay OTHER nodes' traffic between this node's interfaces — see [Internet and transport](#internet-and-transport) |
| `rf_log` | on | `logs/rf-reticulum.log` at the shared LoRa interface (MeshChat's and relayed traffic included; no separate MeshChat log): every packet received (RSSI/SNR) or sent (`ok` on the radio's TX-done; `unconfirmed` when the window elapsed — the airtime was charged and it may have gone out; a duty-dropped packet writes nothing). Raw Reticulum packets: sizes, timing and signal, not contents. Read at the next start; Decrypt: [maintenance](../maintenance.md#rf-logs) |
| `lora_announce_relay` | `internal` | whether the public mesh's announces may go out over the radio; `gateway` relays them. Advanced, and only read while transport is on |
| `internet_enabled` | `no` | the optional `[[Internet]]` TCP interface |
| `internet_host` / `internet_port` | unset | its endpoint; both are required once it is enabled |
| `internet_ifac_netname` | unset | its **own** IFAC — empty for a public hub (see the table below) |
| `rns_bind` | `127.0.0.1` | the only offered value — see [Clients](#clients) |
| `ifac_netname` | unset | the LoRa interface's IFAC network name, written as RNS's `networkname` |
| `use_gps` | on | Sideband's position switch |

The IFAC passphrase lives only in `<runtime>/config/secrets.toml` (`[reticulum] ifac_netkey`),
written as RNS's `passphrase`; lhpc refuses to load that file with any group/other permission
bit (`install -m 0600 /dev/null <runtime>/config/secrets.toml` before editing). Set **both** the
network name and the passphrase, or neither — a half-configured IFAC fails the start ("interface
… was not registered"); a missing key never becomes an empty key. Pins, chip type, TCXO and PA
come from `lhpc hardware`, not settings: a wrong PA or TCXO value can damage the module.

## Hardware

| `lhpc hardware` | bands | chip | driver profile |
|---|---|---|---|
| `loraham` | 433 + 868 | SX1278 / SX1276 | RESET wired (GPIO 5 / 6); tested on the air |
| `uputronics` (and `-433` / `-868`) | 433 / 868 | SX127x | no RESET line — soft reset; tested on the air |
| `waveshare-433` | 433 | SX1262 | tested on the air |
| `waveshare-868` | 868 | SX1262 | **not tested on silicon** |

**SX1262 (Waveshare LoRaWAN Node HAT).** The LF and HF boards are pin-identical (CS 21, IRQ 16,
RESET 18, BUSY 20, TXEN 6) and both carry an SX1262 — 433 does not imply an SX1268. DIO2 drives the
RF switch. The profile hints a 1.8 V TCXO on DIO3 and the driver **probes** it: a board without a
TCXO reports `XOSC_START_ERR`, so the driver falls back to the crystal and logs one notice. A BUSY
line stuck high for 1 s is a radio error.

## Position (GPS)

Only Sideband reads position: its location plugin (`lhpc_location.py` from the driver, enabled
by `enable_sideband_plugins.py` at build) reads `LHPC_LOCATION_CONF` →
`<runtime>/state/sideband/location.conf`, generated at stack start from the global plan
(controller-owned; `max_age` 30 s is the one advanced setting), so an `lhpc gps` change applies at
the next start. A start without Sideband brings up no feed. Model: [GPS](../gps.md).

## Clients

**NomadNet** is an ncurses browser: lhpc shows the command (Dashboard card, `lhpc status
reticulum`) and you run it in a terminal, wrapped in
`loraham-rns-client --configdir <state/reticulum> --wait 10 -- .venv/bin/nomadnet …`, a guard that
proves an authenticated shared instance exists (exit 3 otherwise); `lxmd`, Sideband and MeshChat
launch through the same guard. Never run `.venv/bin/nomadnet` directly against the owner's config:
with `rns` absent, the first process becomes the shared-instance owner and would take the radio
outside lhpc's arbitration. While it is open your node serves its pages and files.

**Client access (TCP 4242)** is for EXTERNAL clients — Sideband on your laptop, another RNS node
of yours. MeshChat joins the shared instance directly (`LocalInterface[37428]`) instead. 4242 is
loopback-only: the setting offers no other value and the node refuses a non-loopback bind even
from a hand-edited config, because the port has no authentication of its own. From another
machine use an [SSH tunnel](../ssh-tunnel.md); add IFAC keys to authenticate the interface itself.

**Sideband** is best run off the Pi (~277 MB resident, needs a display), pointed at the
client-access port. On-box it is installed only where `--with-gui` has run ([cli](../cli.md#deps)) —
gated by `python3-dev` (`sbapp` pulls `materialyoucolor`, a C++ extension without an aarch64 wheel)
and `libx11-dev` (the `--with-gui` marker; Kivy vendors its own SDL2); without `--with-gui` it is
skipped, never a build error.

**MeshChat** is an optional browser client for the same node: `lhpc stack start reticulum` starts
the node only; start MeshChat with `lhpc stack start meshchat` or its console row. An aiohttp
backend on `127.0.0.1:8790` plus a prebuilt frontend, with no authentication of its own, published
through the LHPC proxy ([webserver](../webserver.md#stack-web-ui-proxies)). Its venv is built
**without** system site-packages, so it cannot import the SPI driver.

MeshChat reads the interface list **once, at startup**: after an interface change, restart it
(`lhpc stack restart meshchat`), or its Interfaces page shows the old list next to live counters
(e.g. `Disabled` with `Connected`).

*Interfaces and transport stay LHPC-owned* (rendered from `bases/reticulum.conf` on every start).
The `0400` config refuses a MeshChat edit, and the proxy refuses (404) the seven routes that
attempt it — the component's `proxy_deny_paths`; re-audit that list at every version bump.
Reading works: the Interfaces page lists what lhpc configured.

The frontend is **shipped prebuilt** as package data (no npm on the box); the backend is installed
from `meshchat-constraints.txt`, an exact closure re-checked at each bump; its `rns` must match the node's.

**Versions.** lxmd, nomadnet and Sideband install RNS from the `src/reticulum` checkout, so they run
the node's Reticulum; a moved pin marks them for a rebuild. LXMF comes from PyPI (1.1.1) for
nomadnet, Sideband and MeshChat, while lxmd runs the `src/lxmf` checkout (1.1.0): that pinned
commit lacks PyPI 1.1.1's LXMPeer sync-backoff fix.
`lhpc status --versions` shows each client's `rns`/`lxmf` and names a package that differs.

MeshChat's own **propagation-node** switch (off by default) lives in its SQLite settings and is
reachable over its WebSocket, so no proxy rule covers it. On while `lxmd` runs, the node has two
propagation nodes — duplicate storage and announces.

## Internet and transport

Two independent switches, both **off** by default: an internet link for *your own* traffic, and
relaying *other people's*.

| state | what it means |
|---|---|
| `internet_enabled = yes`, `enable_transport = No` | **this node** reaches the wider Reticulum network through the TCP interface. Nothing is relayed. |
| `enable_transport = Yes` | third-party traffic may cross **between** this node's interfaces — radio ↔ internet, and to clients on `:4242`. |

**The Internet interface** is an outbound `TCPClientInterface` to a public hub or to another node
of yours: set `internet_host` and `internet_port`, then enable it. It opens no listener and needs
no firewall rule.

From the CLI ([config](../cli.md#config)) set the endpoint before enabling it:

```bash
lhpc config reticulum internet_host <host>
lhpc config reticulum internet_port <port>
lhpc config reticulum internet_enabled yes
lhpc stack restart rns --yes
```

Enabling it without a complete endpoint is refused on save (an unreachable target is tolerated,
`panic_on_interface_error = No`). Once connected, your announces reach the internet-side mesh and
theirs reach you.

**IFAC on that interface is its own**, never the radio's:

| the interface points at | IFAC |
|---|---|
| a public hub or testnet | **none** — the hub does not have your passphrase, and an IFAC'd link would pass nothing |
| another node of yours | shared with that peer; legitimately the same value as LoRa if you treat them as one private network |

Set `internet_ifac_netname` **and** `[reticulum] internet_ifac_netkey` in `config/secrets.toml`,
or neither: lhpc refuses to generate a half-configured link and blocks the start (upstream would
accept either half alone). Two peers whose pair differs drop every packet, silently on both ends.

**Interface modes are LHPC's** — client access `gateway`, the internet side `boundary` with
`recursive_prs` (keeps the radio discoverable from the internet). The one setting is the radio's
mode, `lora_announce_relay`, read only while transport is on:

| `lora_announce_relay` | what goes out over the radio |
|---|---|
| `internal` *(default)* | your own announces, your clients', and those heard on the radio — **not** the public mesh's; paths to internet nodes still resolve on demand |
| `gateway` | those too, so radio peers discover internet-side nodes by themselves |

Measured against Reticulum 1.5.2, the only difference is those unsolicited announces — on a
3.12 kbps link with a 1 % hourly budget, the expensive part.

Relayed traffic cannot bypass the airtime limiter: `airtime_limit_short`/`_long` are enforced and
persisted by the LoRa interface, so it is queued or dropped at the limit.

## Band limits

| band | default | limit | licence |
|---|---|---|---|
| 868 | 868.500 MHz | 25 mW ERP (14 dBm), duty per sub-band | none (SRD) |
| 433 | 434.500 MHz | **10 mW ERP (10 dBm)**, 10 % duty | none (SRD / LPD433) |

The 433 default stays clear of the LoRaHAM APRS channel (433.775/433.900) and MeshCom (433.175)
inside 433.050–434.790 MHz. 10 mW is ERP, antenna gain included: 10 dBm into a unity-gain whip is
just inside; with a gain antenna turn the power down.

**Permitted segments** (driver `PERMITTED_SEGMENTS`, BNetzA Vfg. 91/2025 / ERC 70-03). The driver
refuses anything else; an operator limit may only tighten a ceiling:

| segment | hourly duty ceiling |
|---|---|
| 863.0–865.0 MHz | 0.1 % |
| 865.0–868.6 MHz | 1 % |
| 868.6–868.7 MHz | *alarm systems only — refused* |
| 868.7–869.2 MHz | 0.1 % |
| 869.2–869.4 MHz | *alarm systems only — refused* |
| 869.4–869.65 MHz | 10 % |
| 869.65–869.7 MHz | *alarm systems only — refused* |
| 869.7–870.0 MHz | 1 % |
| 433.050–434.790 MHz | 10 % |

The **whole occupied bandwidth** must fit inside one segment: 869.500 MHz is legal at 125 kHz but
not at 500 kHz, which spills outside the 250 kHz-wide 10 % segment. A channel that matches no
segment is refused, never defaulted.

**Duty cycle.** Airtime is reserved *before* transmitting and written to disk, so a restart or
crash loop cannot wipe the hour's accounting; an unconfirmed transmission stays charged. Corrupt
accounting state blocks transmit, never receive.

## Notes

| symptom | cause |
|---|---|
| start fails, "another Reticulum shared instance already owns this configuration" | a stray `rnsd`/node is running; stop it |
| start fails, "interface … was not registered" | the radio or the SPI lock failed — the log above it names which |
| start fails, "client access is bound to … loopback only" | a non-loopback bind |
| "SPI bus lock not acquired within 2s" | a peer holding `spi0.lock` is wedged (usually a stuck daemon) |
| TX seems to stall | the duty-cycle limiter is holding packets; check the airtime limits |

- `rnstatus` interface counters are the RX/TX evidence; `Valid announce` is not logged at the
  default log level, so grepping the node log proves nothing.

## Conflicts

- The stack claims its band **exclusively**: not with the daemon serving that band (and so not
  with its clients), not with meshtastic on that band. Opposite bands coexist — the 433 daemon and
  Reticulum on 868 run together.
- `spi.bus.0.unlocked`: `meshtastic + reticulum` is refused on any band pair
  ([architecture](../architecture.md#radios-bands-and-resource-claims)).
