# GPS: one position source for every stack

Position is a **global** setting, like the radio hardware: Meshtastic, MeshCom, MeshCore,
Sideband and Graywolf all take it from one source. Per-stack settings only turn GPS **on or
off**. Commands: [cli](cli.md#gps).

**Coordinates are never logged.** LHPC shows them only on the console's GPS Monitor (under the
console's [access mode](webserver.md#access-modes), `Cache-Control: no-store`), the GNSS row of
the dashboard's System box (the same `/api/gps` snapshot, fetched only while the box is open) and
`lhpc gps --monitor`. A configured fixed position and the generated Sideband config hold
coordinates by necessity; upstream applications keep their own logging policies.

## Contents

- [Monitor](#monitor)
- [Two settings, not one](#two-settings-not-one)
- [Choosing a source](#choosing-a-source)
- [gpsd is yours](#gpsd-is-yours)
- [Per stack](#per-stack)
- [A u-blox that has met gpsd stays in binary mode](#a-u-blox-that-has-met-gpsd-stays-in-binary-mode)
- [Health, and what the console shows](#health-and-what-the-console-shows)
- [Refusals you may hit](#refusals-you-may-hit)

## Monitor

Under *Position (GPS)* on the console, **Monitor** comes before **Settings**. `lhpc gps
--monitor` prints the same snapshot (`--sats` adds the satellite table); it is read-only and
refuses every setting flag.

The Monitor shows the receiver's state, coordinates, altitude **with its datum** (`512.3 m MSL`,
`560.8 m HAE`, or `(legacy alt)`), the receiver's reported time (never the box's clock; without an
RMC date it reads `12:34:56 (date unavailable)`), satellites used of seen, a **Skyview** pane
(azimuth clockwise from north, elevation towards the centre, filled = used, size = SNR) and an
**NMEA stream** pane. It polls only while open, one request at a time.

| state | meaning |
|---|---|
| `3D fix` / `2D fix` | the receiver's fix; gpsd `mode` 3 / 2, or a direct receiver's GSA |
| `fix (dimension unknown)` | a direct receiver reports a usable position but no recent GSA |
| `no fix` | the receiver talks, no usable position; with mode 0 or 1 no coordinate is shown |
| `no gpsd device` | gpsd lists no device |
| `gpsd device present, no position data yet` | a device is listed but produced no position report in the 2.5 s budget — a listed path may be RTCM or AIS, so it is not called a receiver until it reports |
| `several position sources` | two gpsd devices produced positions, or, with no device-tagged report, an untagged one sits beside several devices; nothing is merged and the NMEA pane is disabled |
| `gpsd unavailable` | no connection, protocol failure, or an unsupported protocol major |
| `stale` | a direct receiver sent no navigation sentence for 20 s |
| `fixed position (configured)` | the manual source; altitude is MSL (that is how the fixed feed emits it) |
| `held` | a direct receiver is read by something else right now (below) |

**gpsd sources.** One gpsd connection per poll, 2.5 s total for name resolution, connects and
reads; reports are newline-framed in a bounded buffer and a truncated or malformed report is
dropped, never guessed. Reports are correlated **per device**; an untagged report is attributed
only when gpsd lists exactly one device. The NMEA pane shows **gpsd's NMEA output** (pseudo-NMEA
for a receiver gpsd runs in binary mode), combined across devices. Watching may activate a
receiver on a gpsd running without `-n`; LHPC's own time-source gpsd runs with `-n`.

**Direct receiver (`nmea`).** A serial port has one reader, so the Monitor has three states:

* **via the feed**: a MeshCom or MeshCore feed owns the device; the Monitor reads that feed's
  `monitor.sock` (a broken one shows "feed running; monitor unavailable", and the device is never
  opened beside it).
* **held**: Meshtastic, graywolf or Sideband reads the device natively and shares no port, so no
  live position or skyview. Also `held` while local gpsd owns the receiver ("cannot establish
  that the device is free": use the gpsd source).
* **one sample**: nobody holds it. The Monitor takes the lifecycle's device claim, re-checks from
  process and unit evidence that nothing started (the hold is bounded at 4.5 s), reads for 2.5 s,
  closes and releases. A stack start meeting that claim waits up to 5 s instead of failing. The
  NMEA pane shows the sample's lines; `/api/gps/nmea` never opens a serial device. No command is
  sent to the receiver.

A stack whose config cannot be read counts as a holder; only a saved `use_gps = off` frees a
native consumer. Coordinates and altitude disappear as soon as a navigation sentence reports no
fix; every retained value (altitude, satellite counts, each GSA and GSV group) ages out
separately at 20 s.

## Two settings, not one

1. The **global source**: where position comes from, for the whole box. Default `auto`
   ([below](#choosing-a-source)).
2. A **per-stack switch**: whether that stack uses it. Default **on**.

```
lhpc config meshtastic use_gps on      # also: meshcom, meshcore, reticulum (Sideband), graywolf
lhpc config meshtastic use_gps off     # opt out again
```

With a receiver and gpsd running, every stack reports position; without gpsd everything starts
without position and the **Position (GPS)** card says so. In the console the card (LHPC row) sets
the source and each stack's Settings carries its `use_gps`.

A source you *named* that cannot be used (a malformed `[gps]` section, an unresolvable `nmea`
device) refuses the start; `auto` finding no gpsd, or `off`, starts without position.

The switch is stored **once per stack, band-less** (like autostart), so a band change keeps it.
It cannot be set for a single start, and neither it nor the source can change while a stack using
them runs ([refusals](#refusals-you-may-hit)).

GPS follows what a start **actually brings up**. Naming a component (`lhpc config meshcom-qemu
use_gps on`, `lhpc stack start meshcom-qemu`) resolves to the stack's switch and the feed its plan
needs. Components that read no position (the MeshCom bridge and firmware, Reticulum without
Sideband) bring up no feed, claim no receiver and are never refused over GPS. `lhpc stack start
meshcom-gps` is refused unless the current plan uses that feed.

## Choosing a source

| Source | Use when | Notes |
|---|---|---|
| `auto` | default | gpsd on `127.0.0.1:2947` if one is listening, otherwise no position; never refuses a start |
| `gpsd` | almost always | USB receiver, HAT, or a GPS server on the network all look the same through gpsd; `--host` reaches a gpsd on another box |
| `nmea` | no gpsd, one consumer | opens the device directly, so gpsd must **not** also own it. Bootstrap skips gpsd once `source = nmea` is saved (or pass `--no-time-source`) — see below |
| `fixed` | the station does not move | no receiver needed |
| `off` | no position | explicit |

`auto` looks at localhost only: a remote gpsd or a serial device is an explicit decision.

## gpsd is yours

lhpc keeps the setting, starts each stack's feed, writes the device into each app's config,
applies the position mode, refuses unsafe combinations and reports what is wrong. It **does not
configure gpsd for position**. `bootstrap-deps.sh` installs gpsd by default and adds `-n` to its
options for the time source ([Clock](operations.md#clock)); it never touches `DEVICES` or
`USBAUTO`.

```
sudo apt install gpsd gpsd-clients          # gpsd-clients only for gpspipe/cgps
sudo systemctl enable --now gpsd
```

The time-source flags: [deps](cli.md#deps). For **position**, gpsd is needed only when the source is a gpsd on
**this** box; `lhpc deps` lists the package only then.

For a USB receiver Debian's default `USBAUTO="true"` is usually enough. For a network GPS server,
point gpsd at the device's **raw NMEA stream** in `/etc/default/gpsd`, e.g.
`DEVICES="tcp://gps-server.lan:<raw-nmea-port>"`, then restart gpsd (not port 2947, which is
gpsd's own protocol; a remote *gpsd* is reached with `--host`). If lhpc cannot reach gpsd,
`lhpc doctor` says so and names the fix.

HAT serial wiring, `dialout` membership and antenna placement are outside lhpc. **Cold start
takes minutes**: `source reachable, waiting for a fix` is a warning, not a failure.

## Per stack

Each stack consumes the resolved source its own way; specifics are on the stack's page.

| Stack | Feed component | Page |
|---|---|---|
| Meshtastic | `meshtastic-gps` (gpsd only) | [meshtastic](stacks/meshtastic.md) |
| MeshCom | `meshcom-gps` | [meshcom](stacks/meshcom.md) |
| MeshCore | `meshcore-gps` (live sources) | [meshcore](stacks/meshcore.md) |
| Sideband (`reticulum`) | none | [reticulum](stacks/reticulum.md) |
| Graywolf | none | [graywolf](stacks/graywolf.md) |

Stale per-stack position keys still saved are listed by `lhpc gps` and `lhpc doctor` as ignored.

## A u-blox that has met gpsd stays in binary mode

gpsd switches u-blox receivers into **UBX binary** and they stay there after gpsd stops. A
`nmea` source then reads a live byte stream containing no NMEA at all, and lhpc reports

```
device is sending binary, not NMEA (a u-blox left in UBX mode by gpsd does this)
```

Use `--source gpsd`, or put the receiver back into NMEA mode with its own tool (`ubxtool`,
u-center) before selecting `nmea`. Meshtastic reading the receiver directly is unaffected
(meshtasticd speaks UBX); only the shared NMEA feed is.

## Health, and what the console shows

The feed's health is its **upstream source**, never the existence of its endpoint (a PTY
exists long before position flows).

| State | Meaning |
|---|---|
| `running` | position is flowing: sentences that carry a valid fix |
| `running` (warning) | source reachable, no fix yet; a cold receiver needs minutes |
| `degraded` | the source went away, or stopped delivering |
| start refused | the source was unreachable at startup; the feed is cleaned up rather than left inert |

When the source returns, the stack goes back to `running` without a restart. A stopped feed
removes its endpoint and readiness marker together.

Readiness rests on **checksum-valid navigation sentences** (GGA/RMC/GLL/GNS with legal status
fields); "flowing" also needs the fix flag *and* coordinates. A GGA with fix quality `0`, or an
RMC/GLL flagged `V`, is navigation without a fix (the warning state); GSV/GSA and a u-blox's lone
`$GPTXT` admit nothing. The marker reports `sentences`, `nav` and `fixes` separately.

gpsd accepts connections with no receiver (`devices: []`) and then sends nothing, so a feed stays
**pre-admission** until validated navigation traffic arrives; with source `gpsd`, `lhpc doctor`
asks gpsd for its devices in one bounded query. A live feed refreshes its marker every few seconds;
a marker older than a minute, or not naming a live feed process, reads as `degraded` and cannot
approve a start.

## Refusals you may hit

- **time source skipped because `[gps] source = nmea`**: bootstrap does not install gpsd beside
  a direct reader ([Clock](operations.md#clock)), which would also leave a u-blox in
  [binary mode](#a-u-blox-that-has-met-gpsd-stays-in-binary-mode). Switch to `gpsd` and re-run,
  or keep `nmea` without GPS time.
- **`nmea` while gpsd owns the receiver**: two readers lose fixes, so lhpc refuses.
  `/dev/ttyACM0` and `/dev/serial/by-id/...` are recognised as the same receiver (`st_rdev`).
- **Ownership cannot be proven**: refused rather than assumed safe.
- **Changing the source or a switch while a stack that uses it runs**: stop the stack first
  (its feed, claims and generated config came from the current setting). "In use" covers its
  position readers and feed in any live or undeterminable state; a stack running with
  `use_gps = off` does not block.
- **A malformed `[gps]` section**: position is disabled and stacks that would use it refuse to
  start.
