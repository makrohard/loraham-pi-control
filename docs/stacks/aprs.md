# Stacks: Chat & iGate (APRS, 433)

Two daemon-backed APRS apps on 433 MHz. Both TX via the daemon; both take the
operator callsign from local config (default `N0CALL`). Don't run them together —
both retune the 433 radio.

## chat — `loraham-chat`

Interactive ncurses APRS/chat TUI (needs a real terminal; no headless mode).
`lhpc` ensures the daemon (433, MANAGED) and shows the command to run yourself —
it does not spawn the TUI.

| | |
|---|---|
| Build | `gcc lorachat_ncurses_113.c -o loraham_chat -lncurses -lpthread` |
| Config | `lorachat.conf` (env `KEY=VALUE`): `CALL`, `TX`/`RX` freq, `DEST` (default `ALL`), `PATH` (default `APRS,WIDE1-1`) |
| Sockets | `/tmp/lora433.sock`, `/tmp/loraconf433.sock` (hard-coded 433) |

## igate — `loraham-igate`

APRS iGate. **Beacons RF on start** (433.900) — use a dummy load.

| | |
|---|---|
| Build | `gcc … -o loraham_igate` |
| Run | `./loraham_igate -c {callsign} -t 433.900 -r 433.775 …` |
| TX / RX | TX 433.900, RX 433.775 |
