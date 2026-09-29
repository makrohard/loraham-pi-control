# Live test, 0.11.3 — the patch's rows, 2026-09-29

The release commit is `9a6359f` (`v0.11.3`). A patch runs no release matrix (the waiver of 2026-09-28); each item was
proven when it was made, and the release itself on one box. Box: the Pi 5 test box (Desktop image, LoRaHAM board). A
Station G2 (Meshtastic) was the peer for one item. The Pi 5 never used `--high-power` or 20 dBm; on 433 it transmitted
only in the rows that say so, with the existing power guard. No RF test ran in the release rows: every one recorded
0 TX.

## The release rows

| row | what | result |
|---|---|---|
| candidate, detached (without C12) | the candidate installed detached on the box at 0.11.2; `lhpc --version`, `lhpc status --versions`, `/healthz`, `lhpc webserver verify` (the real root, no apply; the chain check `ok`); kiss started and stopped with no client (0 TX) | PASS |
| candidate, detached (with C12) | the same, short: version, `/healthz`, and the Meshtastic post steps as the installed controller resolves them (fixed position → region → GPS mode → node identity) | PASS |
| the console on the candidate | read only: footer `v0.11.3`, no error banner or disk notice, the MeshCore mode hint (C08), the web-server panel with no certificate-replacement sentence, the update area | PASS |
| the real upgrade (step 7a) | from 0.11.2 on `main`: `lhpc self-update --apply` (6 s) to `9a6359f` on `main`; one reboot (the box back after 45 s); boot restore with nothing to restore (`no-plan`, 0 restored, 0 failed); `lhpc webserver verify` OK; the Meshtastic step order as released; kiss started and stopped with no client (0 TX) | PASS |
| the console after the upgrade | read only: footer `v0.11.3 @9a6359f8e`, identity ok, up to date, no banner, no disk notice, the web-server panel | PASS |

## The items' own rows (made on the box when each item was)

| item | row | result |
|---|---|---|
| C03 hidden unit commands | the four unit commands run by hand in a shell: each refused with its hint (no invocation marker) | PASS |
| C04 expose and the certificate | a scratch runtime root: a due replacement off a terminal refuses and writes nothing; with `--replace-certificate` it replaces, keeping the key; a covered certificate is left untouched | PASS |
| C05 verify and the chain | a scratch root with a foreign leaf: before the fix `verify` did not flag it (and `openssl verify` rejected the same file); after the fix `server_chain` is failed with its reason, and `tls-renew` clears it | PASS |
| C08 MeshCore repeater name | the CLI refusal in a throwaway root names Settings → Repeater and the CLI command; the console shows the hint under the mode | PASS |
| C09 known-working label | the console's row read; the CLI path on the box was not run (below) | PASS (console) |
| C10 disk warning | a fill file on the box: the warning at low and critical, `lhpc doctor` non-OK at critical, one log line per change, back to ok after the file was removed; the console notice read at low | PASS |
| C11 optional parts at boot | kiss with its serial part started on its own, two reboots: the part came back after the stack; after it was stopped, it stayed stopped | PASS |
| C12 Meshtastic identity last | the node started with the new step order; the owner push completed with no setting after it, and the Station G2 listed the node with its name and key | PASS |
| C06 TX confirmation | not on a box (it would need a transmitting run); tests only, as decided | tests only |
| C01, C02, D1, D2 | no box row (a removal proven by its tests, a race proven by its tests, and two docs changes) | — |

## Not run

- The console path of the one-click update (the upgrade ran through `lhpc self-update --apply`).
- C09 on a box through the CLI (recording a new known-working composition was not part of the row).
- C07's live row (C07 is not in this release).
