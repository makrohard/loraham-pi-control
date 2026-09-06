# Stack: Chat (APRS, 433)

A daemon-backed APRS chat app on 433 MHz that transmits via the daemon. Its `call` setting
(base 3–6 chars + optional APRS SSID `-1`…`-15`, bare = SSID 0) inherits the global operator
base callsign while it is empty; with neither set the start is refused. It holds the 433 band
like every daemon client: one stack per band at a time.

## chat — `loraham-chat`

Interactive ncurses APRS/chat TUI (needs a real terminal; no headless mode).
`lhpc` ensures the daemon (433, MANAGED) and shows the command to run yourself —
it does not spawn the TUI.

| | |
|---|---|
| Build | `gcc lorachat_ncurses_113.c -o loraham_chat -lncurses -lpthread` |
| Config | `lorachat.conf` (env `KEY=VALUE`): `CALL`, `TX`/`RX` freq, `DEST` (default `ALL`), `PATH` (default `APRS,WIDE1-1`) |
| Sockets | `/tmp/lora433.sock`, `/tmp/loraconf433.sock` (hard-coded 433) |
