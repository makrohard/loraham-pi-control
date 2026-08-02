# GPS — one position source for every stack

Position is a **global** setting, like the radio hardware. Meshtastic, MeshCom and Sideband
all take it from the same place, so they can never disagree about where the box thinks it
is. Per-stack settings only turn GPS **on or off**.

```
lhpc gps                                        # show
lhpc gps --source off                           # default
lhpc gps --source gpsd                          # gpsd on this box
lhpc gps --source gpsd --host 192.168.1.5       # gpsd on another box
lhpc gps --source nmea --device /dev/ttyACM0    # a receiver directly, no gpsd
lhpc gps --source fixed --lat 51.4779 --lon -0.0015 --alt 45
```

Coordinates are never echoed back — not by the CLI, the console, or any log.

## Two settings, not one

Position takes **both**:

1. the **global source** above — where position comes from, for the whole box;
2. a **per-stack switch** — whether that stack uses it. Default **off**.

```
lhpc config meshtastic use_gps on      # Meshtastic reports position
lhpc config meshcom     use_gps on
lhpc config reticulum   use_gps on     # Sideband
lhpc config meshtastic use_gps off     # opt out again
```

In the console: the **Position (GPS)** card in the LHPC row sets the source; each stack's
Settings carries its own `use_gps`. Everything on the card is available from the CLI —
`--source --host --port --device --baud --lat --lon --alt` — and both surfaces call the same
code, so validation and refusals are identical.

Why two: setting a source must not silently start every stack beaconing a position. And a
stack with `use_gps = on` while the source is `off` **refuses to start**, naming both
settings, rather than coming up silently blind.

The switch is stored **once per stack, band-lessly** (like autostart), so moving 868 ↔ 433 does
not quietly revert it. It cannot be set for a single start — a launch that differed from the
saved state would leave claims and generated config describing something else — and it cannot
be changed while the stack is running, because that stack's feed, resource claims and generated
config all came from the current setting. Stop the stack first.

Naming a component works the same way: `lhpc config meshcom-qemu use_gps on` sets the *stack's*
switch, because there is only one. Starting a consumer on its own — `lhpc stack start
meshcom-qemu` — brings up the position feed its stack's plan calls for, so a direct start is
never quietly position-blind. GPS applies to what a start **actually brings up**, not to stack
membership: the MeshCom bridge and firmware read no position, and a Reticulum start without
Sideband reads none either, so none of them bring up a feed, claim the receiver, or are refused
over GPS settings they never consult. The feed itself is not something to start by hand: on its
own it would claim the receiver and publish an endpoint for a consumer that is not coming, so
`lhpc stack start meshcom-gps` is refused unless the current plan actually uses it. The fixture
relay claims nothing at all.

## Choosing a source

| Source | Use when | Notes |
|---|---|---|
| `gpsd` | almost always | USB receiver, HAT, or a GPS server on the network — they all look the same through gpsd |
| `nmea` | no gpsd, one consumer | opens the device directly, so gpsd must **not** also own it |
| `fixed` | the station does not move | no receiver needed |
| `off` | no position | default |

**gpsd is usually the right answer.** How gpsd gets its data is gpsd's business: a USB
receiver, a serial HAT, or a hardware GPS server elsewhere on the network all reach lhpc the
same way. Point `--host` at another box and every stack follows.

## What lhpc does, and what it does not

lhpc **does**: keep one setting, start the feed each stack needs, write the right device into
each app's config, apply the position mode to the Meshtastic node, refuse unsafe combinations,
and report what is wrong.

lhpc **does not configure gpsd itself** — that is a system service and stays yours:

```
sudo apt install gpsd gpsd-clients          # gpsd-clients only if you want gpspipe/cgps
sudo systemctl enable --now gpsd
```

For a USB receiver, Debian's default `USBAUTO="true"` is usually enough. For a **network GPS
server**, point gpsd at it in `/etc/default/gpsd`, e.g.
`DEVICES="tcp://gps-server.lan:2947"`, then restart gpsd. lhpc only ever *reads* gpsd; if it
cannot reach it, `lhpc doctor` says so and names the fix.

Wiring a receiver to a HAT's serial pins, `dialout` group membership, and antenna placement
are likewise outside lhpc.

**Cold start takes minutes.** A receiver that has been off or indoors can need several minutes
for its first fix. `gpsd reachable but no fix` is reported as a warning, not a failure.

## Per stack

**Meshtastic.** With `gpsd`, lhpc runs a small feed that presents the stream as a serial
device, because meshtasticd reads only `GPS: SerialPath:` — it speaks neither gpsd nor TCP.
Expect roughly **37 seconds** of `No GNSS Module` warnings after start: meshtasticd probes for
a specific GPS chip, nothing answers a passive stream, and it then proceeds and parses the
NMEA normally. This is expected, not a fault.

With `nmea` it reads the receiver **directly**, which detects a real chip and skips that probe
entirely. With `fixed` it uses its own fixed-position support and needs no feed at all.

Enabling GPS also sets the node's `position.gps_mode`; turning it off sets `NOT_PRESENT` and
clears any stored fixed position, so a node never keeps beaconing a position you turned off.

**MeshCom.** Fed by the same mechanism over the QEMU node's UART. The `MeshCom GPS relay
(fixture)` component is a **test facility** that replays a synthetic file — it is never part
of a normal start and cannot stand in for a real source. Run it explicitly if you want it:
`lhpc stack start meshcom-gps-relay`.

**Sideband.** Its location plugin is a native gpsd/NMEA client and takes the resolved values
directly. Its old per-stack position fields no longer apply; if any are still saved, `lhpc gps`
and `lhpc doctor` list them and say they are ignored.

## A u-blox that has met gpsd stays in binary mode

gpsd switches u-blox receivers into **UBX binary** to get better data, and they stay there
after gpsd stops. A `nmea` source then reads a live byte stream containing no NMEA at all:
lhpc reports

```
device is sending binary, not NMEA (a u-blox left in UBX mode by gpsd does this)
```

Use `--source gpsd` (the simplest answer), or put the receiver back into NMEA mode with its
own tool (`ubxtool`, u-center) before selecting `nmea`.

Meshtastic is unaffected when it reads the receiver **directly**: meshtasticd speaks UBX and
configures the chip itself. It is only the shared feed — which forwards NMEA — that cannot
use a binary stream.

## Health, and what the console shows

The feed's health is its **upstream source**, never the fact that an endpoint exists — a PTY
exists the moment it is created, long before any position flows.

| State | Meaning |
|---|---|
| `running` | **position** is flowing — sentences that carry a valid fix |
| `running` (warning) | source reachable, no fix yet — a cold receiver needs minutes |
| **`degraded`** | the source went away, or stopped delivering |
| start refused | the source was unreachable *at startup* — the feed is cleaned up rather than left inert |

Recovery needs **no restart**: when the source returns, the stack goes back to `running` on
its own. A stopped feed removes its endpoint and its readiness marker together, so nothing is
left that reads as a live GPS.

Readiness rests only on **checksum-valid navigation sentences** — GGA/RMC/GLL/GNS with legal
status fields. "Flowing" additionally requires the fix flag set *and* populated coordinates: an
RMC can claim `A` with empty coordinates, and that is not a position.

Satellites-in-view (GSV/GSA) and a GGA whose fix quality is `0` are navigation traffic without a
fix — the cold-receiver case, reported as the warning state, which still starts the stack. The
lone `$GPTXT` a u-blox emits in UBX **binary** mode is not navigation traffic at all: it never
admits a start, and the feed goes on to the binary diagnosis and is cleaned up. The marker
reports `sentences`, `nav` and `fixes` so the three are distinguishable.

Reaching the source is not the same as having one. Opening the serial device, or completing the
TCP handshake with gpsd, proves nothing about what arrives afterwards — gpsd accepts connections
even when it owns no receiver (`devices: []`) and then sends nothing. A feed stays
**pre-admission** until validated navigation traffic actually arrives.

A live feed refreshes its readiness every few seconds — as a **heartbeat**, whether or not
sentences are arriving, so a reachable receiver that has not got a fix yet keeps saying so
instead of going quiet. A marker that has not been refreshed within a minute, or that does not
name a live feed process, belongs to a **previous** run and is ignored: it reads as `degraded`,
and it cannot approve a new start. A feed killed hard enough to skip its own cleanup therefore
still cannot leave a `ready` marker behind that makes the next start believe position is flowing.

## Installing gpsd

`gpsd` is not part of the default bootstrap — most boxes never use a local receiver. Opt in:

```
./bootstrap-deps.sh --spi-mode soft-cs --with-gps
```

Only needed when the source is a gpsd on **this** box. A remote gpsd, a directly-read device
or a fixed position need nothing installed, and `lhpc deps` only mentions the package when it
is genuinely required.

## Refusals you may hit

- **`nmea` while gpsd owns the receiver** — two readers on one device lose fixes
  intermittently rather than failing cleanly, so lhpc refuses. `/dev/ttyACM0` and
  `/dev/serial/by-id/...` are recognised as the *same* receiver.
- **Ownership cannot be proven** — refused rather than assumed safe.
- **Changing the source while a stack that uses it is running** — stop the stack first; its
  claims and generated config came from the current source.
- **A malformed `[gps]` section** — position is disabled (fail closed) and stacks that would
  use it refuse to start, rather than starting silently blind.
