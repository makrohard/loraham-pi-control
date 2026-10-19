# openHop on LHPC

How to use the openHop repeater when LHPC runs it: the MeshCore stack in mode `chat+repeater` or
`repeater` ([stack reference](stacks/meshcore.md)). openHop is upstream's software, used as
upstream ships it. LHPC keeps only what it must own: the radio, the network access, the files,
the clock and its own chat node. Everything else is set in openHop, the upstream way.

## Contents

- [Where the settings live](#where-the-settings-live)
- [Who owns what](#who-owns-what)
- [Name, behaviour, password, identity, duty cycle](#name-behaviour-password-identity-duty-cycle)
- [Position (GPS)](#position-gps)
- [MQTT](#mqtt)
- [Waev Outpost](#waev-outpost)
- [Glass](#glass)
- [Room servers and further companions](#room-servers-and-further-companions)
- [Restart](#restart)
- [The companion slot](#the-companion-slot)
- [What stays unavailable](#what-stays-unavailable)
- [Backup, restore, purge](#backup-restore-purge)

## Where the settings live

openHop keeps its configuration in its own file, `<runtime>/state/openhop/config.yaml` (0600, in
a 0700 directory). Its dashboard and Glass save there as upstream does.
At every start LHPC reads that file with upstream's own loader, puts its values over the keys
it owns, and writes the file back with a block on top:

```yaml
lhpc_managed:
  note: Set by LoRaHAM Pi Control at every start. ...
  keys: [radio_type, radio, http, storage, radios, kiss, modem_usb, modem_tcp, ...]
  lhpc_one_store: [repeater.node_name, repeater.mode, ...]
```

`keys` lists what LHPC replaces at every start; `lhpc_one_store` lists what lives only in this
file (next section). Everything else in the file is yours. Edit the file by hand only while the
repeater is stopped: a running openHop rewrites the whole file on its next save.

## Who owns what

| setting | owner | change it in |
|---|---|---|
| radio (frequency, bandwidth, SF, CR, TX power, radio type); CAD | LHPC | LHPC Settings (MeshCore) |
| HTTP bind (`127.0.0.1:8000`, reached through LHPC's proxy), storage paths | LHPC | — |
| GPS while `use_gps` is on; time sync (always off) | LHPC | LHPC GPS / Settings |
| LHPC's chat node (`chat+repeater`): its companion entry, name, advert name and location | LHPC | LHPC Settings (MeshCore) |
| the dashboard's login-signing secret, first-run setup | LHPC | — |
| repeater name, behaviour (`forward`/`monitor`/`no_tx`), dashboard password, repeater identity, duty cycle | **one store** | openHop while running, LHPC Settings while stopped |
| mesh (region, flood policy, path hash, loop detection), delays, advert rate limits | you | openHop |
| policy engine, transport keys | you | openHop |
| MQTT brokers, Glass (except its certificate folder), sensors, web frontend, API tokens | you | openHop |
| room servers, further companions | you | openHop |
| position (`repeater.latitude/longitude`), owner info, guest password, intervals | you | openHop |
| GPS while `use_gps` is off (time sync stays off) | you | openHop |

openHop's log level (`logging.level`) is kept in its file, but LHPC's host logs at INFO
whatever it says.

A change of an LHPC-owned value made in openHop (dashboard, CLI, Glass, a config import) is
refused before openHop applies it. The API answer, the CLI and the dashboard's Terminal show
"Managed by LoRaHAM Pi Control: change it in LHPC Settings (MeshCore)."; openHop's Radio and
Repeater settings pages show only their own generic "Failed to update settings" (they do not
display an error's text). Sending an unchanged value passes, so those pages save normally. The CLI refuses `set radio`, `set freq`, `set tx`, `tempradio`, `http start` and
`http stop` by name.

## Name, behaviour, password, identity, duty cycle

These five live only in openHop's file, so LHPC and openHop never show different values:

- **Repeater running:** change them in openHop. LHPC Settings shows the file's values and
  refuses a save with "the repeater is running: change it in openHop".
- **Repeater stopped:** LHPC Settings writes them into the file (exactly the changed key).
- **Before the first repeater-mode start** they stay in LHPC's settings and secret files; that
  start moves them into the file and removes the old copies only once the file is safely on
  disk. A downgrade to LHPC 0.12 after that finds no repeater name or keys of its own. A restart
  of a running chat node cannot be that first start: stop the stack, then start it.

LHPC's own rules apply to its writes: the name is required and should not be your callsign, the
password is 15-128 printable characters without spaces.
openHop's own rules are looser (its password change asks for 8 characters; a shorter password in
the file still logs in, with a warning in openHop's log). A repeater name equal to the operator
callsign, or a dashboard password outside LHPC's rule, set in openHop is kept, and the start shows it
as a `[warning]` line, e.g. "Repeater name equals the operator callsign (set in openHop)". A start is refused only when
openHop could not run with it: an empty name, an unknown behaviour, a password that is empty or
not text, a duty cycle per minute openHop's own airtime code cannot compute with, an identity
that is not a key, or a file openHop's own loader cannot read. openHop at its pinned version (85fc14a6)
cannot run with these; LHPC Settings asks for 1-100 % (its input range, not one of them).

**Duty cycle.** LHPC's `airtime` (%) is openHop's `duty_cycle.max_airtime_per_minute`
(10 % = 6000 ms per minute). openHop can turn the enforcement off; the repeater then transmits
without a duty-cycle limit, and keeping your band's legal duty cycle is up to you.

**Vanity key.** openHop's vanity key generator runs inside the repeater. Prefixes longer than 4
hex characters can stall the repeater for minutes to hours (upstream behaviour, from its code;
not yet verified here: the live proof times 1-4 characters).

## Position (GPS)

With `use_gps` on, LHPC gives openHop the box's position: openHop reads a small file of NMEA
sentences on tmpfs that LHPC rewrites every 2 s (the live fix, or the fixed coordinates; empty
while there is none). openHop may still change the position it advertises, as upstream does.
With `use_gps` off the `gps` section is yours; do not point it at the receiver LHPC's GPS
already uses (they would clash). Time sync stays off either way: the clock is LHPC's.

## MQTT

openHop's MC2MQTT observer, unchanged: Settings → MQTT in the dashboard, including the bundled
presets (Waev, LetsMesh, MeshCore.CA, MeshMapper, meshat.se, chimesh). It starts by itself once
a broker is set and publishes every packet the repeater hears, a retained status (name, radio,
statistics, position if known) and optionally its neighbours: **everything heard, other people's
packets included, goes to the chosen platform.** openHop has a position-precision setting.
Brokers that use JWT sign with the repeater identity and need a correct clock; after a boot
they connect once LHPC has set the time (NTP or GPS). Outbound connections are not blocked by
LHPC's firewall.

## Waev Outpost

A plugin from openHop's catalogue (dashboard → Plugins), served by the repeater under
`/plugins/waev.outpost/` and reachable through the same LHPC proxy page. It can be made the main
frontend (Settings → Web). It calls the same routes as the dashboard, so the same refusals and
notices apply. Its chat with LHPC's chat node uses the companion slot (below).

## Glass

Enable Glass in openHop (dashboard or the file: `glass.enabled`, `base_url`, `api_token`,
`verify_tls`), the upstream way. LHPC owns only the certificate folder
(`state/openhop/glass`). Glass may adopt the repeater, push transport keys and policies, change
the behaviour (`set_mode`), run diagnostics and renew certificates. A radio change or an update
of an LHPC-owned value is refused. **Glass's config export gives Glass the whole file, keys and
passwords included.**

## Room servers and further companions

Create them in openHop (dashboard → identities). They are yours, stored in openHop's file and
database. Give a room server an admin or guest password: openHop accepts one without, but skips it
at the next start. LHPC's chat node is the companion entry with LHPC's chat key; openHop cannot
change or delete it, and LHPC never touches the others. If another companion already uses the chat
node's name with a different key (an old entry after a chat key change, or a restored backup of
another box), the LHPC save and the start refuse: "An openHop companion already uses this name with
a different identity key; remove the old entry in openHop or choose another name."

openHop binds further companions to `0.0.0.0` by default. With LHPC's firewall in secure-default
mode they are reachable only on the box. In compatibility mode they are reachable from the LAN
(LHPC does not control who connects to them): set their `bind_address` to `127.0.0.1` unless you
need that.

## Restart

openHop's restart (dashboard, CLI `reboot`, Glass) runs LHPC's own `lhpc stack restart meshcore`,
so a "restart to apply" works and LHPC's view of the stack stays right.

## The companion slot

LHPC's chat node serves one client at a time. `meshcli`, the MeshCore Web UI and a browser chat
in the dashboard or Outpost share one lock; while `meshcli` holds it the browser chat is refused
with "Companion in use by meshcore-cli". Further companions are not part of it.

## What stays unavailable

| feature | why |
|---|---|
| setup wizard | LHPC completes the first-run setup |
| CAD settings and calibration | channel access is the daemon's job |
| openHop's own updates (check, install, channel) | LHPC installs and updates the stack |
| chat node advert name and location in the dashboard | LHPC owns its chat node (Settings, GPS) |
| radio settings | LHPC owns the radio (Settings) |

These answer "Managed by LoRaHAM Pi Control" (the API answer; the pages that call them show
their own error handling). Updates come with LHPC: a stack
update rebuilds openHop's environment, and packages a sensor installed there by itself are
expected to be gone afterwards (not yet verified).

## Backup, restore, purge

The secrets backup includes `state/openhop`: the config file with the identity and passwords,
`policy.yaml`, Glass certificates, plugins and the databases. The GPS file is on tmpfs and is not
in it. `lhpc clean meshcore --purge` removes the folder; a reinstall is then a new repeater.
