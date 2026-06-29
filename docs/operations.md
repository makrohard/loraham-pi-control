# Operations & safety

Operational rules for `lhpc`. See `architecture.md` for internals.

## Not a supervisor

`lhpc` does not stay running. Closing the CLI or web server never stops a stack.
On each run it reconstructs real state from systemd, process identity, endpoint
probes, source/pin state and resource ownership — never from a stale PID file.

## Fast vs explicit

- Fast & bounded (no network, no build, no mutation, no RF): `status`, `explain`,
  `doctor`, `logs`, `web` page loads.
- Explicit & gated (print a plan, need `--yes` or a confirmation): `install`,
  `build`, `update`, `stack start/stop`, `test`, `rollback`, `repair`, `uninstall`.

## TX safety

- TX is never auto-enabled; a freshly installed/configured stack is RX-only.
- TX happens only through an explicit `test --tx` or a stack you start that
  transmits (e.g. iGate beacons).
- A `test --tx` shows band, parameters and expected RF effect, warns to use a
  **dummy load**, and confirms unless `--yes`. It sends one frame per band and
  verifies `TXOK` incremented.
- Read-only status/doctor/page loads never transmit and never initialise a radio.

## Resource ownership

One active stack owns a LoRa band at a time. The daemon's 433/868 instances
cooperate on the SPI bus (internal serialisation); a direct radio user
(meshtastic) claims a band exclusively. Daemon sockets are provider (daemon) /
consumer (kiss, bridge, meshcore). Starting a stack is blocked, with the holder
named, if a running stack already holds a band it needs.

## Secrets

Callsign, passwords, HMAC keys and private keys live only in git-ignored local
config (`~/loraham-pi-control/config/local.toml`, `config/secrets.toml` mode
`0600`) — never in tracked files, status output or web actions. Uninstall keeps
local config by default.

## Web console

Loopback only (`127.0.0.1`/`::1`, default `:8770`). GET routes are read-only.
Mutating routes follow one pattern — **POST + CSRF token + explicit confirm**,
dispatched through the same service layer as the CLI: stack/component actions show
a dry-run plan first (TX-capable ones add an RF/dummy-load warning); daemon live
settings apply only a whitelisted non-RF tuning (TX mode, CAD/LBT). Security
headers (incl. `Content-Security-Policy: default-src 'self'`) on every response.
