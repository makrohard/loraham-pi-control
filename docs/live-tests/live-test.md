# Live test, 0.13.0 — the release matrix, 2026-10-19

The release commit is `19f82b7` (`v0.13.0`), tested before the tag: the controller checkout of the Zero 2 W read
`19f82b7` before and after the rows. Fast lane ([test-matrix.md](../test-matrix.md#fast-lane)), on the Zero 2 W bench box
only (Lite image, LoRaHAM board: daemon on 433 and 868, a GPS receiver). The candidate was installed on the box's 0.12.10
checkout (detached at the release commit, the venv synced from its `requirements.lock`, then
`lhpc self-update --repair-integration`: all seven managed units read `ok`, the console answered on `:8443`).

The rows prove each stack's install, build, start and verify on the box. No peer radio took part in this run, so no
message was exchanged over RF.

## Rows ([test-matrix.md](../test-matrix.md))

| row | result | install / build / start |
|---|---|---|
| 1 daemon, binary | PASS: build refused as designed, both bands `RADIO=READY`, `lhpc daemon 433` answers | 6 s / refused / 9 s |
| 2 chat | PASS: the printed client runs in a terminal; it was ended by a 15 s timeout with exit code 0 (no quit command typed) | 5 s / 5 s / 6 s |
| 3 voice | PASS: the terminal variant built (`-DNO_GTK`) and runs as row 2; GTK skipped as designed (Lite) | 5 s / 6 s / 8 s |
| 4 kiss | PASS: `127.0.0.1:8001` answers | 4 s / 17 s / 6 s |
| 5 graywolf | PASS: started on kiss; web UI `:8080` answers 200 | 3 s / 5 s / 10 s |
| 6 reticulum | PASS: `rnstatus` lists `LoRaSPIInterface` with `Mode: Internal`; the ready marker present; MeshChat (started as its own component) answers 200 on `:8790`; the generated config is `0400`; sideband skipped (Lite) | 75 s / 259 s / 11 s |
| 7 meshcore (chat + repeater) | PASS: node and web UI verified on 868; `:8788` and `:8000` answer 200; `meshcore-cli` listed and launched (no client command typed) | 33 s / 410 s / 25 s |
| 8 meshtastic, binary | PASS: build refused as designed; `lhpc meshtastic --info` returns the node (firmware `2.7.26.54e0d8d`); web client 2.7.2 (binary, current) | 91 s / refused / 34 s |
| 9 meshtastic from source | not re-run (fast lane)¹ | — |
| 10 daemon from source | not re-run (fast lane)¹ | — |
| 11 meshcom, binary | FAIL on the first start after the purge (`meshcom-qemu did not start/verify` after 799 s, see the known issue); PASS on the next start: verified after 76 s, web UI `:18083` answered 200 at 67 s | 15 s / refused / 799 s, then 76 s |
| 12 meshcom from source | not re-run (fast lane)¹ | — |
| `dev` spot-check (kiss) | PASS: `--source dev` resolves the branch tip, which is the pin (`match`, v0.6.3, provenance `mutable-dev`); after the reinstall on the default channel `match` | 5 s / 18 s / 8 s |

¹ Waived on 2026-10-19. The measuring run is the 0.11.0 matrix (this file's history, 2026-09-28):
- row 9: 825 s at meshtastic `54e0d8d0`, the same pin;
- row 10: 317 s at daemon `1f1baf26`, pin now `74763114`;
- row 12: 839 s at meshcom-qemu `3e3c877e` and firmware `ba289816`, pins now `4369e529` and `f2b3a349`.

Rows 10 and 12 changed pins since that run.

No row ran out of memory. The slow-target entries of rows 6, 7 and 8 (row A) are in
`tests/data/slow-target-builds.toml`. Each build was timed alone with `lhpc build <component> --yes` on the same box at the
release commit. The `lxmd` and `meshcore-cli` checkouts measured below the log's 0.1 s resolution, so their earlier
entries stay.

Not run in this release: the other cross-cutting checks and the from-zero reinstall. The one-click self-update and
`calibrate.sh` are recorded after the tag.

## Known issue (also in the release notes)

On a Pi Zero 2 W, the first `lhpc stack start meshcom` after a purge or a fresh install can fail with
`meshcom-qemu did not start/verify`. Provisioning the node and its cold boot take longer than the start's 600 s readiness
window, while the stack's own start note gives about 6–14 minutes on a Zero. Start it again: the provisioned node then
boots in about a minute. On a Pi 5 the start note gives about one minute for the boot.

## Notes from the run

- The Meshtastic node-info measurement after a purge (cited by [meshtastic](../stacks/meshtastic.md)) was recorded in
  the 0.11.0 matrix; it was not repeated here.
- The rows ran with the console up, except during the MeshCom start (stopped while the QEMU node boots, as
  [maintenance](../maintenance.md#running-on-a-pi) advises on a Zero).

The 0.11.0 record (with its patch [0.11.3](patch-0.11.3-2026-09-29.md)) is this file at `v0.11.0`; the 0.10.0 record is
this file at `v0.10.0`.
