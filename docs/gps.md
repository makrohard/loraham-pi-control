# GPS: one position source for every stack

Position is a **global** setting, like the radio hardware. Meshtastic, MeshCom, MeshCore,
Sideband and Graywolf all take it from the same place, so they can never disagree about where
the box thinks it is. Per-stack settings only turn GPS **on or off**. The commands:
[cli](cli.md#gps).

**Coordinates are never logged.** They are displayed only on explicit monitor surfaces to the
operator: the GPS Monitor on the console (subject to the console's configured access policy —
productive serving is HTTPS, the default `local-open-remote-auth` is open on loopback and requires a
client certificate remotely, and a `no-auth` mode exists as the operator's explicit choice — with
`Cache-Control: no-store` as on every page) and `lhpc gps --monitor` in the operator's terminal. No
other LHPC monitor output, page, log line or state file introduced by the Monitor carries them; the
bridge's rule is unchanged. (A configured fixed position and the generated Sideband configuration
necessarily hold configured coordinates, and upstream applications keep their own logging policies.)

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

Under *Position (GPS)* on the console the first sub-section is **Monitor**, the second **Settings**
(the form). `lhpc gps --monitor` prints the same snapshot; `--sats` adds the satellite table. Both are
read-only: `--monitor` refuses every setting flag.

The Monitor shows the receiver's state, coordinates, altitude **with its datum** (`512.3 m MSL`,
`560.8 m HAE`, or `(legacy alt)`), the receiver's reported time (never the box's clock; without an
RMC date it reads `12:34:56 UTC (date unavailable)`), satellites used of seen, a **Skyview** pane
(azimuth clockwise from north, elevation towards the centre, filled = used, size = SNR) and an
**NMEA stream** pane. It polls only while it is open, and only after the previous request settled.

| state | meaning |
|---|---|
| `3D fix` / `2D fix` | the receiver's fix; gpsd `mode` 3 / 2, or a direct receiver's GSA |
| `fix (dimension unknown)` | a direct receiver reports a usable position but no recent GSA |
| `no fix` | the receiver talks, no usable position; with mode 0 or 1 no coordinate is shown |
| `no gpsd device` | gpsd lists no device |
| `gpsd device present, no position data yet` | a device is listed but produced no position report in the 2.5 s budget — a listed path may be RTCM or AIS, so it is not called a receiver until it reports |
| `several position sources` | two gpsd devices produced positions, or an untagged report sits beside several devices; nothing is merged and the NMEA pane is disabled |
| `gpsd unavailable` | no connection, protocol failure, or an unsupported protocol major |
| `stale` | a direct receiver sent no navigation sentence for 20 s |
| `fixed position (configured)` | the manual source; altitude is MSL (that is how the fixed feed emits it) |
| `held` | a direct receiver is read by something else right now (below) |

**gpsd sources.** The console is a disposable gpsd client: one connection per poll, a total budget
of 2.5 s covering name resolution, every address attempt and every read, newline-framed with a bounded
buffer (gpsd reports can be split across reads or truncated at 10 240 characters — a truncated report
is dropped, never guessed). Reports are correlated **per device**; a report without a `device` tag is
attributed only when gpsd lists exactly one device. The NMEA pane shows **gpsd's NMEA output** (for a
receiver gpsd runs in binary mode that is pseudo-NMEA), combined across devices. Watching may
activate a gpsd-managed receiver on a server not running gpsd with `-n`; it changes no LHPC
configuration. LHPC's own time-source gpsd runs with `-n`.

**Direct receiver (`nmea`).** A serial port has one reader, so the Monitor has three states:

* **via the feed** — a MeshCom or MeshCore feed owns the device: the Monitor reads that feed's own
  `monitor.sock` (best effort; a feed with a broken monitor shows "feed running; monitor
  unavailable" and the device is never opened beside it).
* **held** — Meshtastic, graywolf or Sideband reads the device natively: no live position or skyview
  during that operation, because none of those programs share their port. Also `held` while
  local gpsd owns the receiver ("cannot establish that the device is free" — use the gpsd source).
* **one sample** — nobody holds it: the Monitor takes the lifecycle's own device claim, re-checks
  from process and unit evidence alone that nothing started meanwhile (no source probe runs under
  the claim, so the hold is bounded at 4.5 s), reads the receiver for 2.5 s, closes, releases. A
  stack start that meets the Monitor's claim waits for it (at most 5 s) instead of failing. The NMEA pane then shows
  the sample's own lines; `/api/gps/nmea` never opens a serial device. Opening a tty configures it;
  no command is sent to the receiver.

A stack whose config cannot be read counts as a holder; only a positively saved `use_gps = off` frees
a native consumer. Coordinates and altitude disappear the moment a navigation sentence reports no
fix, and every retained value (altitude, satellite counts, each GSA and GSV group) ages out
separately at 20 s.

## Two settings, not one

1. The **global source**: where position comes from, for the whole box. Default `auto`
   ([below](#choosing-a-source)).
2. A **per-stack switch**: whether that stack uses it. Default **on**.

```
lhpc config meshtastic use_gps on      # also: meshcom, meshcore, reticulum (Sideband), graywolf
lhpc config meshtastic use_gps off     # opt out again
```

Out of the box: plug in a receiver, run gpsd, and every stack reports position. No gpsd?
Everything still starts, without position, and the **Position (GPS)** card says so. In the
console the card (LHPC row) sets the source and each stack's Settings carries its `use_gps`.

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
| `nmea` | no gpsd, one consumer | opens the device directly, so gpsd must **not** also own it. Bootstrap it with `--no-time-source`, or it will install gpsd — see below |
| `fixed` | the station does not move | no receiver needed |
| `off` | no position | explicit |

`auto` looks at localhost only: a remote gpsd or a serial device is an explicit decision.

## gpsd is yours

lhpc keeps the one setting, starts the feed each stack needs, writes the right device into each
app's config, applies the position mode to the node, refuses unsafe combinations and reports
what is wrong. It **does not configure gpsd for position**: that is a system service.

One exception, and it is narrow: `bootstrap-deps.sh` installs gpsd and adds `-n` to its options so
**chrony** can take the receiver's time ([Clock](operations.md#clock)). It never touches `DEVICES`
or `USBAUTO`, and it never changes which source lhpc uses for position.

```
sudo apt install gpsd gpsd-clients          # gpsd-clients only for gpspipe/cgps
sudo systemctl enable --now gpsd
```

**gpsd is installed by default** as of the time-source feature, because a fresh image with a receiver
must be able to set its clock without the operator opting in. `--no-time-source` skips both it and
chrony; `--with-gps` still works and now only prints a note, since gpsd is no longer opt-in
([deps](cli.md#deps)). For **position** it is only needed when
the source is a gpsd on **this** box; a remote gpsd, a directly read device or a fixed position
install nothing, and `lhpc deps` mentions the package only when it is required.

For a USB receiver Debian's default `USBAUTO="true"` is usually enough. For a network GPS server,
point gpsd at the device's **raw NMEA stream** in `/etc/default/gpsd`, e.g.
`DEVICES="tcp://gps-server.lan:<raw-nmea-port>"`, then restart gpsd. That is not port 2947:
2947 is gpsd's own protocol port, and a remote *gpsd* is reached with `--host` instead. lhpc only
reads gpsd; if it cannot reach it, `lhpc doctor` says so and names the fix.

Wiring a receiver to a HAT's serial pins, `dialout` group membership and antenna placement are
likewise outside lhpc. **Cold start takes minutes**: `gpsd reachable but no fix` is a warning,
not a failure.

## Per stack

Each stack has its own switch (`lhpc config <stack> use_gps on|off`) and consumes the resolved
source in its own way. Stack specifics live in the stack's page.

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

gpsd accepts connections even when it owns no receiver (`devices: []`) and then sends nothing —
when the source is `gpsd`, `lhpc doctor` asks it exactly that, one bounded query — so a feed
stays **pre-admission** until validated navigation traffic arrives. A live feed refreshes its marker every few seconds as a
heartbeat. A marker not refreshed within a minute, or not naming a live feed process, belongs to
a previous run: it reads as `degraded` and cannot approve a new start.

## Refusals you may hit

- **the time source skipped because `[gps] source = nmea`**: `bootstrap-deps.sh` refuses to install
  gpsd when lhpc is configured to read the receiver directly. This is not only about the two of them
  fighting over the device — gpsd switches u-blox receivers into UBX binary mode and they **stay**
  there (below), so a later `nmea` read would find no NMEA at all until the chip is reset with an
  external tool. Switch the source to `gpsd` and re-run, or keep `nmea` and accept no GPS time.
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
