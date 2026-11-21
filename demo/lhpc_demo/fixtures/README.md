# Demo log fixtures

What the demo's log windows show: real logs of the project's test box (captured read-only on 2026-11-21, the last
300 lines a full log page shows), anonymized. `logs_fixture.install` copies them into the runtime root at every build;
`MANIFEST.json` gives each file its box mtime, so the console's own "newest log" choice is the box's.

Only windows the demo does not fill itself: no file here has a name the demo writes (its start and RF logs), for any
radio id. A window that was empty on the box stays empty.

## Anonymized (fixed rules, nothing else changed)

- the callsign -> `N0CALL` (also inside packet hex and AX.25-shifted bytes)
- the MAC -> `00:00:5E:00:53:01`, its Meshtastic node id/num -> `5e005301` / `1577079553`
- LAN IPv4 -> `192.0.2.N`; host names -> `lhpc-box`, `lhpc-zero`, `client-pc`; home paths -> `/home/user`
- keys and identity bytes (also a truncated key) -> zeros of the same length
- none present (a run with one would stop): positions, IPv6, e-mail, credentials, cookies, URL query secrets,
  serial numbers, the maintainer's name or address

## Kept (accepted by the maintainer)

Exact timestamps, browser user agents, node display names, the auto-install run id, packet ids, pids, radio ids,
frequencies, the USB port path, wheel hashes, the GitHub org in clone URLs, payloads encrypted with box-only keys.

## Changing a fixture

Every change goes through the same anonymizer and an exact-value residue scan with 0 hits before it is committed;
`demo/tests/probe.py` checks the generic classes on every run.
