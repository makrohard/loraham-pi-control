# Stacks: Chat & iGate (APRS, 433)

Two daemon-backed APRS apps on 433 MHz. Both TX via the daemon. Each has its own
`call` setting (base 3–6 chars + optional APRS SSID `-1`…`-15`, bare = SSID 0); while
it is empty the stack inherits the global operator base callsign, and with neither set
the start is refused. Don't run them together — both retune the 433 radio.

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

> **Deprecated — use [graywolf](graywolf.md) instead.** Three reasons: `loraham_igate` has
> known bugs, there is no visible upstream maintenance, and graywolf is a full substitute — the
> same RF↔APRS-IS job through the [KISS TNC](kiss.md), plus a web UI and a searchable packet log.
>
> Nothing breaks today: the stack id and every config key are unchanged, so an existing station
> keeps running and there is no rush to migrate. It will be retired once no deployment starts it.

APRS iGate. **Beacons RF on start** (433.900) — use a dummy load.

| | |
|---|---|
| Build | `gcc … -o loraham_igate` |
| Run | `./loraham_igate -c {callsign} -t 433.900 -r 433.775 …` |
| TX / RX | TX 433.900, RX 433.775 |
