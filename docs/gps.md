# GPS: one position source for every stack

Position is a **global** setting, like the radio hardware. Meshtastic, MeshCom, MeshCore,
Sideband and Graywolf all take it from the same place, so they can never disagree about where
the box thinks it is. Per-stack settings only turn GPS **on or off**.

```
lhpc gps                                        # show (incl. what `auto` resolved to)
lhpc gps --source auto                          # gpsd on this box if one runs, else no position (default)
lhpc gps --source off                           # no position, explicitly
lhpc gps --source gpsd                          # gpsd on this box
lhpc gps --source gpsd --host 192.168.1.5       # gpsd on another box
lhpc gps --source nmea --device /dev/ttyACM0    # a receiver directly, no gpsd (--baud optional)
lhpc gps --source fixed --lat 51.4779 --lon -0.0015 --alt 45
```

Coordinates are never echoed back: not by the CLI, the console, or any log.

## Contents

- [Two settings, not one](#two-settings-not-one)
- [Choosing a source](#choosing-a-source)
- [gpsd is yours](#gpsd-is-yours)
- [Per stack](#per-stack)
- [A u-blox that has met gpsd stays in binary mode](#a-u-blox-that-has-met-gpsd-stays-in-binary-mode)
- [Health, and what the console shows](#health-and-what-the-console-shows)
- [Refusals you may hit](#refusals-you-may-hit)

## Two settings, not one

1. The **global source** above: where position comes from, for the whole box. Default
   `auto`: use a gpsd listening on this box (`127.0.0.1:2947`), otherwise run without position.
2. A **per-stack switch**: whether that stack uses it. Default **on**.

```
lhpc config meshtastic use_gps on      # also: meshcom, meshcore, reticulum (Sideband), graywolf
lhpc config meshtastic use_gps off     # opt out again
```

Out of the box: plug in a receiver, run gpsd, and every stack reports position. No gpsd?
Everything still starts, without position, and the **Position (GPS)** card says so. In the
console the card (LHPC row) sets the source and each stack's Settings carries its `use_gps`;
CLI and console call the same code, so validation and refusals are identical.

Fail-closed protection follows **explicit intent**: a source you *named* that cannot be used
(a malformed `[gps]` section, an `nmea` device that cannot be resolved) refuses the start. The
soft cases, `auto` finding no gpsd or an explicit `off`, start the stack without position.

The switch is stored **once per stack, band-lessly** (like autostart), so a band change does
not revert it. It cannot be set for a single start, and neither it nor the source can change
while a stack that uses them is running: that stack's feed, resource claims and generated config
came from the current setting. Stop the stack first.

GPS applies to what a start **actually brings up**, not to stack membership. Naming a component
(`lhpc config meshcom-qemu use_gps on`, `lhpc stack start meshcom-qemu`) resolves to the stack's
one switch and brings up the feed its plan calls for. Components that read no position (the
MeshCom bridge and firmware, a Reticulum start without Sideband) bring up no feed, claim no
receiver and are never refused over GPS. A feed is not something to start by hand: `lhpc stack
start meshcom-gps` is refused unless the current plan uses it.

## Choosing a source

| Source | Use when | Notes |
|---|---|---|
| `auto` | default | gpsd on `127.0.0.1:2947` if one is listening, otherwise no position; never refuses a start |
| `gpsd` | almost always | USB receiver, HAT, or a GPS server on the network all look the same through gpsd; `--host` reaches a gpsd on another box |
| `nmea` | no gpsd, one consumer | opens the device directly, so gpsd must **not** also own it |
| `fixed` | the station does not move | no receiver needed |
| `off` | no position | explicit |

`auto` looks at localhost only: a remote gpsd or a serial device is an explicit decision.

## gpsd is yours

lhpc keeps the one setting, starts the feed each stack needs, writes the right device into each
app's config, applies the position mode to the node, refuses unsafe combinations and reports
what is wrong. It **does not configure gpsd**: that is a system service.

```
sudo apt install gpsd gpsd-clients          # gpsd-clients only for gpspipe/cgps
sudo systemctl enable --now gpsd
```

gpsd is not part of the default bootstrap; opt in with
`./bootstrap-deps.sh --spi-mode <mode> --with-gps`. It is only needed when the source is a gpsd
on **this** box; a remote gpsd, a directly read device or a fixed position install nothing, and
`lhpc deps` mentions the package only when it is required.

For a USB receiver Debian's default `USBAUTO="true"` is usually enough. For a network GPS server,
point gpsd at the device's **raw NMEA stream** in `/etc/default/gpsd`, e.g.
`DEVICES="tcp://gps-server.lan:<raw-nmea-port>"`, then restart gpsd. That is not port 2947:
2947 is gpsd's own protocol port, and a remote *gpsd* is reached with `--host` instead. lhpc only
reads gpsd; if it cannot reach it, `lhpc doctor` says so and names the fix. When the source is
`gpsd`, `lhpc doctor` also asks that gpsd whether it owns a receiver, because a gpsd that answers
while owning nothing yields no position at all.

Wiring a receiver to a HAT's serial pins, `dialout` group membership and antenna placement are
likewise outside lhpc. **Cold start takes minutes**: `gpsd reachable but no fix` is a warning,
not a failure.

## Per stack

Each stack has its own switch (`lhpc config <stack> use_gps on|off`) and consumes the resolved
source in its own way. Stack specifics live in the stack's page.

| Stack | What it consumes | Feed component | Page |
|---|---|---|---|
| Meshtastic | `gpsd`: the feed presents NMEA on a PTY, because meshtasticd reads only `GPS: SerialPath:`. `nmea`: reads the receiver directly (detects the chip, skips the probe). `fixed`: meshtasticd's own fixed position, no feed. `position.gps_mode` is pushed in both directions; off also clears a stored fixed position | `meshtastic-gps` (gpsd only) | [meshtastic](stacks/meshtastic.md) |
| MeshCom | every enabled source through the feed, on the QEMU node's UART1 (a UNIX socket). `meshcom-gps-relay` is a **test fixture** that replays a synthetic file: never part of a normal start, run it explicitly with `lhpc stack start meshcom-gps-relay` | `meshcom-gps` | [meshcom](stacks/meshcom.md) |
| MeshCore | live sources (`gpsd`, `auto`→gpsd, `nmea`) as a line-JSON position feed on a UNIX socket, so the node's position follows the box and clears when the source goes stale. `fixed`: coordinates in its config, no feed | `meshcore-gps` (live sources) | [meshcore](stacks/meshcore.md) |
| Sideband (`reticulum`) | its location plugin is a native gpsd/NMEA client and takes the resolved values directly. Stale per-stack position keys still saved are listed by `lhpc gps` and `lhpc doctor` as ignored | none | [reticulum](stacks/reticulum.md) |
| Graywolf | native gpsd or serial-NMEA client (`--gps-source gpsd` or `serial`), applied in both directions. `fixed` maps to `none`: graywolf has no fixed-position mode, and a fixed station's coordinates belong to its beacons, which are yours to set | none | [graywolf](stacks/graywolf.md) |

Expect roughly **37 seconds** of `No GNSS Module` warnings after a Meshtastic start on the
`gpsd` feed: meshtasticd probes for a specific GPS chip, nothing answers a passive stream, then
it parses the NMEA normally. This is expected, not a fault.

## A u-blox that has met gpsd stays in binary mode

gpsd switches u-blox receivers into **UBX binary** and they stay there after gpsd stops. A
`nmea` source then reads a live byte stream containing no NMEA at all, and lhpc reports

```
device is sending binary, not NMEA (a u-blox left in UBX mode by gpsd does this)
```

Use `--source gpsd`, or put the receiver back into NMEA mode with its own tool (`ubxtool`,
u-center) before selecting `nmea`. Meshtastic reading the receiver **directly** is unaffected:
meshtasticd speaks UBX and configures the chip itself. Only the shared feed, which forwards NMEA,
cannot use a binary stream.

## Health, and what the console shows

The feed's health is its **upstream source**, never the fact that an endpoint exists: a PTY
exists the moment it is created, long before any position flows.

| State | Meaning |
|---|---|
| `running` | position is flowing: sentences that carry a valid fix |
| `running` (warning) | source reachable, no fix yet; a cold receiver needs minutes |
| `degraded` | the source went away, or stopped delivering |
| start refused | the source was unreachable at startup; the feed is cleaned up rather than left inert |

Recovery needs no restart: when the source returns, the stack goes back to `running` on its
own. A stopped feed removes its endpoint and its readiness marker together.

Readiness rests on **checksum-valid navigation sentences** (GGA/RMC/GLL/GNS with legal status
fields); "flowing" additionally requires the fix flag set *and* populated coordinates. A GGA with fix quality `0` (or an RMC/GLL flagged `V`) is navigation traffic without a fix (the warning state); GSV/GSA count as sentences only and admit nothing. The
lone `$GPTXT` a u-blox emits in UBX mode is not navigation traffic and never admits a start. The
marker reports `sentences`, `nav` and `fixes` so the three are distinguishable.

Reaching the source is not the same as having one: gpsd accepts connections even when it owns
no receiver (`devices: []`) and then sends nothing, so a feed stays **pre-admission** until
validated navigation traffic arrives. A live feed refreshes its marker every few seconds as a
heartbeat. A marker not refreshed within a minute, or not naming a live feed process, belongs to
a previous run: it reads as `degraded` and cannot approve a new start.

## Refusals you may hit

- **`nmea` while gpsd owns the receiver**: two readers on one device lose fixes intermittently,
  so lhpc refuses. `/dev/ttyACM0` and `/dev/serial/by-id/...` are recognised as the same
  receiver (resolved through the device identity, `st_rdev`).
- **Ownership cannot be proven**: refused rather than assumed safe.
- **Changing the source or a switch while a stack that uses it is running**: stop the stack
  first. "In use" covers the stack's position readers and its feed in any live state,
  including a component whose state cannot be determined; a stack running with
  `use_gps = off` does not block the change.
- **A malformed `[gps]` section**: position is disabled (fail closed) and stacks that would use
  it refuse to start rather than starting silently blind.
