# Stack: Reticulum (RNS)

A [Reticulum](https://reticulum.network) node driving the LoRa radio **directly over SPI** on 868
(default) or 433 MHz. No rnoded, no RNode firmware, no KISS layer: one LoRa packet is one RNS
packet. The driver is [loraham-rns-interface](https://github.com/makrohard/loraham-rns-interface).

| | |
|---|---|
| Components | `rns` (main — owns the radio and the shared instance; runs `loraham-rns-node`) · `rns-lora-interface` (library: the direct-SPI driver, RNS interface and service runner; a build-time dependency) · `nomadnet` (optional, interactive) · `lxmd` (optional; propagation **off** by default) · `sideband` (optional desktop GUI, `--with-gui` only) · `meshchat` (optional browser GUI) |
| Source / pin | `src/reticulum` ← `markqvist/Reticulum` `1.5.2` · `src/loraham-rns-interface` ← `makrohard/loraham-rns-interface` `3fef542` · `src/nomadnet` `ad10301` · `src/lxmf` `795fdaa` · `src/sideband` `2.1.0` (installed as `sbapp==1.9.2` from PyPI — a source install drops every `.kv` layout) · `src/meshchat` ← `liamcottle/reticulum-meshchat` `v2.4.0` @ `45f89a8` |
| Build | a venv with `--system-site-packages` (the SPI/GPIO bindings come from the `python3-libgpiod` + `python3-spidev` system packages, so the node needs no compiler — what makes it installable on a Pi Zero); Reticulum + the driver; the interface copied to `state/reticulum/interfaces/LoRaSPIInterface.py`; an import probe before the marker |
| Run | `.venv/bin/loraham-rns-node --config <runtime>/state/reticulum --interface LoRa --ready-file <runtime>/state/reticulum/ready --client-allow <allow-list>` — it exits rather than staying up without a radio |
| Endpoints | shared instance `127.0.0.1:37428` · instance control `:37429` · client access `:4242` · optional outbound TCP to an internet peer (no listener) · MeshChat `127.0.0.1:8790` (loopback, reached through the LHPC proxy) · readiness = the `ready` file, written only after the node owns the instance **and** the radio is online |
| Config | `<runtime>/state/reticulum/config` (0400) from `lhpc/data/bases/reticulum.conf`, regenerated on every start — edit it through lhpc. Read-only even to its owner, so a client that offers to edit interfaces cannot; lhpc rewrites it by renaming a fresh file over it, which needs permission on the directory, not the file |
| Resources | `loraham.radio.868` + `.433` exclusive · `spi.bus.0` cooperative · `spi.bus.0.unlocked` exclusive · `tcp.port.37428` / `.37429` / `.4242` / `.8790` exclusive |
| System | `/dev/spidev0.0` with `dtoverlay=spi0-0cs` (`bootstrap-deps.sh --spi-mode soft-cs`); `spi` + `gpio` groups; `python3-libgpiod`, `python3-spidev` |
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
| `airtime_limit_short` / `airtime_limit_long` | 868: 5 % (15 s) / 1 % (1 h) · 433: 10 % / 10 % | advanced |
| `rns_allow` | `127.0.0.1` | client-access allow-list; drives the managed firewall |
| `enable_transport` | `No` | relay OTHER nodes' traffic between this node's interfaces — see [Internet and transport](#internet-and-transport) |
| `rf_log` | on | RF log (`logs/rf-reticulum.log`) at the LoRa interface: every packet received (RSSI/SNR) or sent (`ok` on the radio's TX-done; `unconfirmed` when the window elapsed — the airtime was charged and it may have gone out; a duty-dropped packet writes nothing). Raw Reticulum packets, i.e. ciphertext — sizes, timing and signal, not contents. With transport on, relayed packets appear too. Read at the next start |
| `lora_announce_relay` | `internal` | whether the public mesh's announces may go out over the radio; `gateway` relays them. Advanced, and only read while transport is on |
| `internet_enabled` | `no` | the optional `[[Internet]]` TCP interface |
| `internet_host` / `internet_port` | unset | its endpoint; both are required once it is enabled |
| `internet_ifac_netname` | unset | its **own** IFAC — empty for a public hub (see the table below) |
| `rns_bind` | `127.0.0.1` | the only offered value — see [Clients](#clients) |
| `ifac_netname` | unset | the LoRa interface's IFAC network name, written as RNS's `networkname` |
| `use_gps` | on | Sideband's position switch |

The IFAC passphrase lives only in `<runtime>/config/secrets.toml` (`[reticulum] ifac_netkey`),
written as RNS's `passphrase`; lhpc refuses to load that file with any group/other permission
bit (`install -m 0600 /dev/null <runtime>/config/secrets.toml` before editing). Set **both** the network name and the passphrase, or neither — a half-configured IFAC fails the start ("interface … was not registered"); a missing key never becomes an empty key.
Pins, chip type, TCXO and PA settings are **not** settings: they come from `lhpc hardware`,
because a wrong PA or TCXO value can damage the module.

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
TCXO reports `XOSC_START_ERR` and would sit in `STBY_RC` with `SetTx` accepted but never started,
so the driver falls back to the crystal and logs one notice. The BUSY line is read through
libgpiod 2.x, whose `gpiod.line.Value` is not int-convertible — the driver reads its `.value`
(the SX1262 is the only profile with a BUSY line); a BUSY stuck high for 1 s is a radio error.

## Position (GPS)

Only Sideband reads position: its location plugin (`lhpc_location.py` from the driver, enabled
by `enable_sideband_plugins.py` at build) reads `LHPC_LOCATION_CONF` →
`<runtime>/state/sideband/location.conf`, generated at stack start from the global plan
(source, gpsd host/port, NMEA device/baud, fixed lat/lon/alt — all controller-owned; `max_age` 30 s is the one advanced setting). A change to `lhpc gps` takes effect on the next start. `rns`, nomadnet and
lxmd read none, so a start without Sideband brings up no feed. Model: [GPS](../gps.md).

## Clients

**NomadNet** is an ncurses browser: lhpc shows the command (Dashboard card, `lhpc status
reticulum`) and you run it in a terminal. The generated command wraps it in
`loraham-rns-client --configdir <state/reticulum> --wait 10 -- .venv/bin/nomadnet …`, a guard that
proves an authenticated shared instance exists (exit 3 otherwise); `lxmd` and Sideband launch
through the same guard. Never run `.venv/bin/nomadnet` directly against the owner's config: with
`rns` absent, Reticulum makes the first process the shared-instance owner, so NomadNet would load
the LoRa interface and take the radio outside lhpc's arbitration. While it is open your node serves
its pages and files.

**Client access (TCP 4242)** is what an EXTERNAL client attaches to — Sideband on your laptop,
another RNS node of yours. The bundled MeshChat does not use it: running on the box, it joins the
node's shared instance directly (`LocalInterface[37428]`), which is why restarting `rns` shows it
reconnecting there rather than on 4242. It is loopback-only: the
setting offers no other value and the node refuses a non-loopback bind even from a hand-edited
config, because the port has no authentication of its own and an allow-list is firewall intent,
not enforcement. From another machine use an [SSH tunnel](../ssh-tunnel.md); add IFAC keys to
authenticate the interface itself.

**Sideband** is best run off the Pi (~277 MB resident, needs a display), pointed at the
client-access port. On-box it is installed only where `bootstrap-deps.sh --with-gui` has run —
gated by `python3-dev` (`sbapp` pulls `materialyoucolor`, a C++ extension without an aarch64 wheel)
and `libx11-dev` (the `--with-gui` marker; Kivy vendors its own SDL2); without `--with-gui` it is
skipped, never a build error.

RF logging is at the shared RNS LoRa interface; MeshChat traffic therefore appears in the
Reticulum RF log as raw Reticulum packets — there is no separate MeshChat log.

**MeshChat** is a browser client for the same node: an aiohttp backend on `127.0.0.1:8790` plus a
prebuilt web frontend, published through the LHPC proxy ([webserver](../webserver.md)). It has no
authentication of its own, so the proxy is the only public path. It starts through the same
`loraham-rns-client` guard as NomadNet, and for the same reason — started bare with `rns` absent it
would take the shared instance and try to load the LoRa interface. Its venv is built **without**
system site-packages, so even then it could not import the SPI driver.

MeshChat reads the interface list **once, at startup**. After an interface change through lhpc,
restart it (`lhpc stack restart meshchat`) or its Interfaces page keeps showing the previous state
— including the confusing pairing of a `Disabled` label with live `Connected` counters, because
the counters come from the running instance while the list does not. Its own banner says as much.

*Interfaces and transport stay LHPC-owned.* They are rendered from `bases/reticulum.conf` on every
start, so a MeshChat edit would look accepted and be reverted at the next start. The `0400` config
refuses the write, and the proxy refuses (404) the seven routes that attempt it — the component's
`proxy_deny_paths` in the manifest; re-audit that list at every version bump. Reading still works:
the Interfaces page lists what lhpc configured, including `LoRa` even when nothing is running.

The frontend is **shipped prebuilt** as package data — upstream gitignores it and there is no npm on
the box — and the backend is installed from `meshchat-constraints.txt`, an exact closure whose `rns`
may be a version ahead of the node's; that skew is deliberate and re-checked at each bump.

MeshChat carries its own **propagation-node** switch, off by default. It lives in MeshChat's SQLite
settings and is reachable over its WebSocket, so no proxy rule can cover it; LHPC does not claim
exclusive ownership. Turning it on while `lxmd` runs gives the node two propagation nodes —
duplicate storage and announces, wasteful rather than dangerous.

## Internet and transport

Two switches, both **off** by default, and deliberately independent — an internet link for
*your own* traffic is a different decision from relaying *other people's*.

| state | what it means |
|---|---|
| `internet_enabled = yes`, `enable_transport = No` | **this node** reaches the wider Reticulum network through the TCP interface. Nothing is relayed. |
| `enable_transport = Yes` | third-party traffic may cross **between** this node's interfaces — radio ↔ internet, and to clients on `:4242`. |

**The Internet interface** is an outbound `TCPClientInterface` to a public hub or to another node
of yours: set `internet_host` and `internet_port`, then enable it. It opens no listener and needs
no firewall rule.

That order matters **from the CLI**, where `lhpc config` sets one parameter per call
([cli](../cli.md)), so enabling first would be a save with no endpoint yet:

```bash
lhpc config reticulum internet_host <host>
lhpc config reticulum internet_port <port>
lhpc config reticulum internet_enabled yes
lhpc stack restart rns --yes
```

The stack's Settings page saves the whole form in one submission, so there the order is free.
Enabling it without a complete endpoint is refused when you save it — RNS builds
the interface at start and would fail there, where an unreachable target (which the node tolerates,
`panic_on_interface_error = No`) looks nothing like a malformed one. Once connected, your
announces reach the internet-side mesh and theirs reach you.

**IFAC on that interface is its own**, never the radio's:

| the interface points at | IFAC |
|---|---|
| a public hub or testnet | **none** — the hub does not have your passphrase, and an IFAC'd link would pass nothing |
| another node of yours | shared with that peer; legitimately the same value as LoRa if you treat them as one private network |

Set `internet_ifac_netname` **and** `[reticulum] internet_ifac_netkey` in
`config/secrets.toml`, or neither. Upstream accepts either half on its own — it derives the
interface's authentication from whatever it is given — so both-or-neither is **LHPC's policy**,
not an RNS error: it removes a whole class of link that looks configured and is not. What actually
drops every packet is a MISMATCH, two peers whose pair differs, and the symptom is silent from
both ends. lhpc refuses to generate the config rather than start a half-configured link, and the
start is blocked with that message.

**Interface modes are LHPC's** — with one exception. The client door is `gateway` and the
internet side is `boundary` with `recursive_prs`; neither is a setting. The radio's mode **is**
one, `lora_announce_relay`, because it is a policy question rather than a correctness one:

| `lora_announce_relay` | what goes out over the radio |
|---|---|
| `internal` *(default)* | your own announces, your clients', and those heard on the radio — **not** the public mesh's |
| `gateway` | those too, so radio peers discover internet-side nodes by themselves |

Both relay traffic in both directions and both answer path requests in both directions; measured
against the pinned Reticulum 1.5.2, the only difference is the unsolicited announces. On a
3.12 kbps link with a 1 % hourly budget that difference is the expensive part, which is why the
default keeps them off the air — the same trade an APRS igate makes. What `internal` costs is
radio-side *discovery*: a peer on the radio can still reach any internet address it knows, and
paths still resolve on demand, but internet nodes no longer appear by themselves.

Reticulum filters announces on the **outgoing** interface, so this is the radio's own mode and not
the internet side's; `recursive_prs` there is what keeps the radio discoverable from the internet
while it is `internal`. With transport off the modes are indistinguishable.

**What gateway traffic cannot do** is bypass the radio's airtime limiter: `airtime_limit_short`
and `airtime_limit_long` are enforced and persisted by our own LoRa interface, so relayed traffic
is queued or dropped at the limit rather than flooding the band. That bounds the airtime you
donate — it does not decide whether you want to donate it.

## Band limits

| band | default | limit | licence |
|---|---|---|---|
| 868 | 868.500 MHz | 25 mW ERP (14 dBm), duty per sub-band | none (SRD) |
| 433 | 434.500 MHz | **10 mW ERP (10 dBm)**, 10 % duty | none (SRD / LPD433) |

The 433 default stays clear of the LoRaHAM APRS channel (433.775/433.900) and MeshCom (433.175)
while remaining inside 433.050–434.790 MHz. 10 mW is ERP — it includes antenna gain: 10 dBm into a
unity-gain whip is just inside; with a gain antenna turn the power down. The stack cannot know your
antenna.

**Permitted segments** (driver `PERMITTED_SEGMENTS`, BNetzA Vfg. 91/2025 / ERC 70-03). The driver
knows them and their hourly duty ceilings and refuses anything else; an operator limit may only
tighten a ceiling, never widen it:

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

**Duty cycle.** Airtime is reserved *before* transmitting and written to disk, so a restart or a
crash loop cannot wipe the hour's accounting. An unconfirmed transmission stays charged. Corrupt
accounting state blocks transmit but never receive.

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
