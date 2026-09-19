# GPS Monitor — live proof, 2026-09-19

The GPS Monitor (`lhpc gps --monitor`, `/api/gps`, the Monitor section under Position (GPS)),
run on the reference box against a real receiver through gpsd. Measured values only; a row that
could not be proven on the available hardware says so and says why, rather than being dropped.

**Bench.** Box `lhpc-e293`, Pi Zero 2 W, Lite image, `lhpc hardware` = `uputronics` (433 + 868).
gpsd 3.25 runs as a system service and owns a u-blox 7 on `/dev/ttyACM0`; chrony takes the same
gpsd as a reference clock. The receiver sits indoors near a window. Global source `auto`, which
resolves to that gpsd. Only the daemon (868, managed TX) was running.

**How the feature reached the box.** The controller checkout moved to a throwaway integration
branch of `feature/high-power` (already on the box) and `feature/gps-monitor`; the only overlap
was the CHANGELOG, resolved by keeping both sections. It exists so the box keeps its daemon 1.2.0
and its high-power settings while carrying the Monitor; it is not for merge. `lhpc-web` was
restarted after each checkout. Nothing was installed, updated or configured on the box, and no
stack was started other than the one bridge in row 4. The run was made twice: in the morning on
the first feature commit, and in the afternoon on the commit amended after audit round 1.

Evidence is the CLI on the box, the endpoints over the console's unix socket, `lhpc status`,
`systemctl`, `chronyc`, and the console journal.

## The morning: 25 satellites, no fix — and why

For 21 hours the receiver had reported 24–25 satellites and used none; chrony had taken 8 GPS
samples in that time. The Monitor showed the reason: gpsd relayed a signal of 22–30 dB-Hz on
10–12 satellites the receiver itself computed as **below the horizon** (down to −86°), while the
satellites overhead read the same level or 0, and no satellite ever reached 30 dB-Hz over three
samples 20 s apart. That is noise being tracked as satellites, the documented signature of
interference — not a lack of sky. The dongle sat on a USB hub two ports from an active USB-2.0
gigabit Ethernet adapter, with the Pi's SoC a few centimetres away. The adapter was unplugged
(the hub reset took the receiver with it; the console's own Reboot action restored it), and
within minutes the receiver had a **3D fix**: 5–8 used of 19–20 seen, levels 12–28 dB-Hz on the
used ones and 0 on the impossible ones, chrony's GPS refclock reach back to 377. The Monitor
behaved correctly throughout: the skyview hides below-horizon satellites by design, the CLI
prints them as reported, which is how this became visible.

## Rows

| # | row | result | evidence |
|---|---|---|---|
| 1 | CLI monitor on the real receiver | pass | morning: `no fix`, `0 used of 25 seen`, receiver time with date, one line per satellite (elevation, azimuth, SNR). Afternoon: `3D fix`, position and `altitude: 487.6 m MSL`, `7 used of 19 seen`, `used` marked per satellite; three QZSS/SBAS entries without elevation printed as `-`. Wall time 4.3 s (a 2.5 s gpsd sample plus Python start-up) |
| 2 | Console page | pass | `/stacks?open=gps` carries `gps-monitor` before `gps-settings`, the skyview `svg`, the NMEA pane and `gps.js` (served 200) |
| 3 | `/api/gps` | pass | HTTP 200 in 2.51 s, `Cache-Control: no-store`. Morning: `state no-fix`, `mode 1`, `lat`/`lon`/`alt` null. Afternoon: `state 3d`, `label "3D fix"`, `mode 3`, `alt_kind msl`, coordinates and altitude present, `sats_used 8`, `sats_seen 19`, `nmea_ok true`, 19 satellite records, empty `note` and `error` |
| 3a | `/api/gps/nmea` | pass | 22 lines each read (`$GPRMC…` / `$GPZDA…` first), HTTP 200 in 1.2–2.0 s |
| 4 | Clients coexist: Monitor while a stack's bridge uses the same gpsd | pass | `lhpc stack start meshcom-gps --yes` (the MeshCom gpsd bridge alone; band `-`, no radio claim) → `running`; then `/api/gps` with the same shape as row 3 (afternoon: `3d`, 8 of 19), `/api/gps/nmea` 22 lines, `lhpc gps --monitor` in agreement; the bridge still `running` afterwards, its process alive |
| 5 | Existing gpsd stack path unchanged | pass | the bridge started (11.6 s) and stopped (6.1 s) through the normal commands, `stopped` afterwards; the Monitor read the same state again after the stop, empty note |
| 6 | The box's time-source gpsd keeps working | pass | `systemctl is-active gpsd` = `active` before, during and after; `chronyc sources` unchanged by the Monitor: the `GPS` refclock row and the same NTP peer selected (the refclock's reach went from 0 to 377 with the fix, see above) |
| 7 | Console journal | pass | no `error`, `traceback` or `exception` line in `lhpc-web` across both runs |
| 8 | Skyview input from real hardware | pass | negative elevations and SNR 0 on almanac entries (morning, 11 of 25) — the parser keeps them within −90…90, the skyview draws `el ≥ 0` only, the CLI prints the values as reported |
| 9 | Fix progression | pass | the same receiver went `no fix` (0 used, no coordinates) → `3D fix` (coordinates, `altMSL` → `alt_kind msl`, used count) once the interference was removed; witnessed on the CLI, `/api/gps` and beside the bridge (row 4) |
| 10 | Browser: poll stop, resume, stale discard | pass (headless Chromium, real `gps.js`) | the real `gps.js` served with the Monitor markup and a deliberately slow `/api/gps` (3 s): first poll rendered; the Monitor collapsed and reopened while request #2 was in flight; #2 settled and was **discarded** (never rendered); a fresh request #3 was issued and rendered, then #4 (polling continues); with the whole panel collapsed 0 requests in 7 s; reopened → exactly one new request. The same script against the pre-audit `gps.js` stalls after the reopen (polling dead) — the audit-round-1 P1 |

## Not proven here

- **`fixed` and `off` labels.** Proving them on the box means changing the box's global source;
  it was left at `auto`. Both are unit-tested.
- **Native gpsd clients beside the Monitor** (graywolf, Meshtastic). Graywolf's TNC needs the
  daemon on 433 and Meshtastic needs the 868 radio claim; the box's daemon held 868 for the
  maintainer and was not moved. The Monitor never talks to those clients — it is a gpsd client
  like them — so the mechanism proven in row 4 is the same one.
- **Direct-NMEA rows: none on hardware.** No NMEA chip exists on the bench, and gpsd owns the
  only receiver. The pty and FIFO tests validate the software's behaviour only — the tee to the
  feed's monitor socket, the operation-scoped claim, the idle sample with its budgets, the
  runtime-only holder re-check under the claim, the waits in `_acquire_key` — not any receiver's
  baud, modem line, reset or cold-start behaviour.
- **Browser row 10 ran on this PC**, not on the box: the served `gps.js` is byte-identical, the
  console's real endpoints are rows 3 and 3a.
