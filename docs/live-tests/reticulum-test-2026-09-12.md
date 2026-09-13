# Reticulum stack — full test matrix, 2026-09-12

The whole stack exercised on the reference box: the node, both new capabilities (the MeshChat
browser client, and the Internet interface with transport), and the properties that were only
ever argued on paper before. Measured values only; a row that could not be proven says so and
says why, rather than being dropped.

**Bench.** Box `lhpc-e293`, Pi Zero 2 W (415 MB), Debian 13 (trixie) Lite, aarch64,
`lhpc hardware` = `uputronics` (433 + 868). Controller `lhpc 0.4.5` on the two-feature branch.
Node Reticulum **1.5.2**; MeshChat's own client venv **1.5.4** — the deliberate skew the
constraints file pins.

Rows **A** and **D** were measured at `7240ee8`, the branch's first commit, where the config
was still rendered from the pre-Internet base; every other row, and a re-check of
D2/D3/D4/D7, at `cece0c9`. The re-check agreed line for line.

**The internet peer is a controlled one**, not a public hub: a second Reticulum 1.5.2 instance on
the developer machine with a `TCPServerInterface` on the LAN and one announced destination that
exists **only** there. A third instance, attached to the box's client-access port through an SSH
tunnel, plays the local client. Every "reaches the internet side" row below is that destination,
so a pass means the packet really crossed the box.

Evidence is the controller's typed outcome plus the stack's own state — `lhpc status`, `rnstatus`,
`rnpath`, `rnprobe`, an HTTP code, a file mode. Log lines appear only where the log **is** the
artefact (a guard's refusal, a reconnect notice).

## Contents

- [A. Install and build](#a-install-and-build)
- [B. Start, readiness, resources](#b-start-readiness-resources)
- [C. Config ownership](#c-config-ownership)
- [D. MeshChat](#d-meshchat)
- [E. Internet interface and transport](#e-internet-interface-and-transport)
- [F. Clients](#f-clients)
- [Not proven here](#not-proven-here)

## A. Install and build

| # | check | result |
|---|---|---|
| A1 | `lhpc install reticulum --source pinned` adopts every source | **PASS** — sideband and meshchat adopted, the other four already `match`; both report `pinned-verified` (meshchat at `v2.4.0`). **61 s** |
| A2 | `lhpc build meshchat --yes` on the Pi | **PASS** — **98 s**, `[succeeded] build meshchat (rc 0)` |
| A3 | the venv holds exactly the pinned closure | **PASS** — `aiohttp 3.14.3`, `lxmf 1.1.1`, `peewee 3.19.0`, `rns 1.5.4`, `websockets 17.1` |
| A4 | the browser bundle on the box equals the one in the repo | **PASS** — 63 files, tree md5 `6bb5601a…` on both sides |
| A5 | the installer is a stage-and-swap, not a copy into place | **PASS** — the rerun replaced a 61-file tree with the 63-file one and left nothing behind |
| A6 | sideband is skipped, not failed, on a headless box | **PASS** — `[skip] sideband: GUI dependencies not installed — headless-safe default` |
| A7 | the build marker is written after the last step | **PASS** — `.venv/.lhpc-build-complete` present after A2 |

A4 found the one real defect of this campaign: `.gitignore`'s generic `dist/` rule matched two
vendored files inside the bundle, so a *clone* built a 61-file UI while the developer's tree and a
wheel built from it had all 63. Fixed, with a test that compares tracked files against the disk for
every shipped asset tree.

## B. Start, readiness, resources

| # | check | result |
|---|---|---|
| B1 | `lhpc stack start reticulum` brings up the node | **PASS** — `[verified] rns: started; ready endpoint(s) up` |
| B2 | a plain stack start leaves the optional clients alone | **PASS** — only `rns` started; `:8790` not listening afterwards |
| B3 | the node's three listeners are up, loopback only | **PASS** — `127.0.0.1:37428`, `:37429`, `:4242` |
| B4 | a second owner of 868 is refused, typed | **PASS** — `lhpc stack start meshtastic` → `[conflict] loraham.radio.868 is held by running stack 'reticulum' (rns)` **and** `[conflict] spi.bus.0.unlocked …` |
| B5 | starting a client starts its dependency first | **PASS** — `lhpc stack start meshchat` started `rns` first, then MeshChat, both `[verified]` |
| B6 | a running MeshChat reports RUNNING | **PASS** — `meshchat  running  [service]`; without the `process` stanza this reads STOPPED while the UI serves |
| B7 | the radio interface is up on the declared band | **PASS** — `LoRaSPIInterface[LoRa] Status: Up`, rate 3.12 kbps |

## C. Config ownership

| # | check | result |
|---|---|---|
| C1 | the generated config is `0400` | **PASS** — `mode=400` after every start below |
| C2 | LHPC rewrites its own read-only file | **PASS** — restart with the file already `0400`: inode `404990` → `406973`, mode unchanged, node verified |
| C3 | a client library cannot write it | **PASS** — `ConfigObj.write()` from MeshChat's own venv on a `0400` copy → `PermissionError: [Errno 13]` |
| C4 | a set value renders; a cleared one returns to the base | **PASS** — `target_host = hub.example.org`, then `target_host =` |
| C5 | the config is regenerated from the base on every start | **PASS** — C2's new inode, and C4's revert |
| C6 | no secret reaches the stack config, the CLI or the console | **PASS** — `internet_ifac_netkey` absent from `config/local.toml`; `lhpc config reticulum` lists the network name only; the console's own Settings body renders the five visible params and **no** occurrence of the passphrase field |
| C7 | the console renders the new settings | **PASS** — `GET /stacks/reticulum/body` **200**, carrying `enable_transport`, `internet_enabled`, `internet_host`, `internet_port`, `internet_ifac_netname` |

A note on C3: the first attempt to make MeshChat write the config failed one layer *earlier*, on a
`UnicodeEncodeError` — ConfigObj encodes as ascii and the base file carries an em dash in a
comment. That is an accident, not a defence; C3 therefore isolates the mode on an ascii-only copy.

## D. MeshChat

| # | check | result |
|---|---|---|
| D1 | the guard refuses a bare start while `rns` is down | **PASS** — exit **3** after the 10 s wait, `refusing to start python, because it would become the shared-instance OWNER and take the radio`, and `:37428` never bound (0 listeners before and after) |
| D2 | `rns` + `meshchat` + `lxmd` run together | **PASS** — all three `running`, UI **200** |
| D3 | the config is untouched while all three run | **PASS** — same md5 before and after, mode still `0400` (checked in both renderings) |
| D4 | the UI answers through the LHPC proxy | **PASS** — loopback **200**, proxy `https://127.0.0.1:8449/` **200** |
| D5 | the seven config-writing routes are refused | **PASS** — all seven POSTs → **404** |
| D6 | the two POSTs that write nothing still pass | **PASS** — `interfaces/export` **200**; `interfaces/import-preview` **500** from the backend (no body sent) — reached it, which is the point |
| D7 | restarting `rns` does not need MeshChat restarted | **PASS** — `Socket for LocalInterface[37428] was closed, attempting to reconnect...` → `Reconnected socket for LocalInterface[37428].`, same pid, UI **200** |
| D8 | the LXMF identity lives outside the checkout | **PASS** — `state/meshchat/identity` + `identities/<hash>` |
| D9 | propagation is off by upstream default | **PASS** — `lxmf_local_propagation_node_enabled: False` from its own `/api/v1/config` |
| D10 | MeshChat sees the mesh | **PASS** — 14 `lxmf.propagation` announces in its log while `lxmd` ran |
| D11 | the suggested proxy port collides on an upgraded box | **PASS, by refusal** — reticulum's suggested `8448` was already saved for the repeater dashboard, so the save was refused typed (`port 8448 is already used by another stack's web UI`); `8449` saved and applied |

## E. Internet interface and transport

| # | check | result |
|---|---|---|
| E1 | defaults render transport off and the interface inert | **PASS** — `enable_transport = No`; `[[Internet]]` present, `enabled = no`, empty endpoint |
| E2 | the rendered modes are the intended trio | **PASS** — `LoRa = gateway`, `Client access = gateway`, `Internet = boundary` |
| E3 | the node reports those modes | **PASS** — `rnstatus`: `Mode: Gateway` (LoRa and Client access), `Mode: Boundary` (Internet) |
> **Correction, after the external audit.** Rows E2 and E3 record what was measured on the day:
> the trio then shipped was `LoRa = gateway`. The audit demonstrated with real Reticulum 1.5.2
> that a `gateway` radio interface **retransmits internet-side announces onto the radio** once
> transport is on — RNS filters announces on the outgoing interface and its gateway path has no
> branch rejecting them. The shipped trio is now `LoRa = internal`, `Client access = gateway`,
> `Internet = boundary` + `recursive_prs = yes`, reproduced and re-measured in all four announce
> directions and both path-request directions. The rows below are left as measured rather than
> rewritten.

| E4 | enabling without an endpoint is refused at save | **PASS** — `config transaction failed and was rolled back: the Internet interface is enabled but its endpoint is incomplete (internet_host, internet_port not set)` |
| E5 | a half-configured IFAC blocks the start | **PASS** — `[blocked] rns: config generation failed (…: the Internet interface has an IFAC network name but no passphrase …)`, before RNS ran |
| E6 | a valid but unreachable target does not stop the node | **PASS** — target `192.0.2.1:4965`: node `[verified]`, `TCPInterface[Internet/192.0.2.1:4965] Status: Down`, radio and client door up |
| E7 | a reachable target comes up | **PASS** — `Status: Up`, peer shows 1 client |
| E8 | the node reaches a destination that exists only on the internet side | **PASS** — path table: `1 hop away … on TCPInterface[Internet/…]`; `rnprobe`: valid reply, **53.9 ms**, 0 % loss |
| E9 | transport OFF does not relay | **PASS** — the same destination from a client attached to `:4242`: path request **timed out**, client path table empty |
| E10 | transport ON relays | **PASS** — same client, same destination, after `enable_transport Yes` + restart: `2 hops away … on TCPInterface[Box client access/…]`, valid reply **63.6 ms**, 0 % loss |
| E11 | an IFAC mismatch drops everything while looking healthy | **PASS** — box with IFAC, peer without: `Status: Up`, `Network: privatenet`, probe **100 % loss** |
| E12 | the matching IFAC restores it | **PASS** — same passphrase on the peer: valid reply **58.0 ms**, 0 % loss. One variable changed, so E11's cause is proven, not inferred |
| E13 | relayed traffic is written into the airtime ledger | **PASS** — `state/reticulum/airtime.json` gained entries (`0.635392 s` each) while transport was on, and the radio counter moved (`↑366 B`) |
| E14 | the settings return to their defaults cleanly | **PASS** — all five cleared, node `[verified]`, config back to E1's rendering at `0400` |

## F. Clients

| # | check | result |
|---|---|---|
| F1 | NomadNet is presented, never started by lhpc | **PASS** — `[manual] nomadnet is interactive …` with the full command, wrapped in the same `loraham-rns-client` guard |
| F2 | NomadNet runs under the `0400` config | **PASS** — run on a pty from the presented command: `LXMF Router ready to receive on: <…>`, `Starting user interface...`, clean SIGTERM teardown, **zero** errors in its log for that run |
| F3 | `lxmd` runs alongside the node and MeshChat | **PASS** — `lxmd  running`, its propagation announces are D10 |
| F4 | the box has no resource conflict with all three up | **PASS** — `lhpc doctor`: `observed resource conflicts: 0`, `running=3` (its only findings are the Lite box's absent GUI packages) |

F2 closes an item the MeshChat plan left open ("nomadnet under 0400 — not yet tested").

## Post-audit corrections, and the measurements behind them

The external audit of `75646d6` found two runtime defects this matrix did not reach. Both are
corrected; the measurements are recorded here because they are what the correction rests on. They
were taken with the pinned Reticulum **1.5.2** processing real signed announces and real path
requests — no forwarding or mode-selection function was mocked.

**Announce propagation, transport ON** (the radio's mode varied, everything else fixed):

| direction | `gateway` | `internal` |
|---|---|---|
| internet announce → radio | **relayed (183 B, 1 hop)** | not relayed |
| radio announce → internet | relayed | relayed |
| client announce → radio | relayed | relayed |
| own announce → radio | relayed | relayed |

With transport **OFF** the two are indistinguishable, and in both the node's own announces still
go out on both interfaces. So `internal` removes the public mesh's announce broadcast from the air
and nothing else — which is why it is the shipped default, and why the operator can still choose
`gateway` with `lora_announce_relay` when they want radio-side discovery of internet nodes.

**Path requests** (radio `internal`, internet `boundary`):

| request | default | with `recursive_prs = yes` |
|---|---|---|
| internet → unknown radio destination | **none reaches the radio** | one does |
| radio → unknown internet destination | one reaches the internet | one does |

That is why `recursive_prs` ships with the mode: without it, `internal` would also hide the radio
from the internet side's path search.

Rows **E2** and **E3** above record the trio as it was on the day (`LoRa = gateway`) and are left
as measured. Row **A3** likewise: `meshchat-constraints.txt` then pinned the five direct
requirements only, which left thirteen transitive packages free; it is now the full freeze of the
proven venv, as its sibling already was.

The second defect needed no RF at all: an enabled Internet interface with an IFAC network name and
no passphrase saved cleanly, and `restart` stopped the node **before** config generation refused
it — leaving it down. The refusal now happens at the pre-mutation boundary, for stack and
component targets alike, with generation still the last word.

## Not proven here

- **Relay between the LoRa side and the internet side.** E9/E10 prove the relay switch on the
  client-access ↔ internet path, which is governed by the same single `enable_transport` flag —
  RNS has one transport switch, not one per interface. Proving the radio leg needs a **second
  Reticulum node on the air**, and there is none: the ESP32 peers on this bench run MeshCore,
  MeshCom and Meshtastic firmware, not RNS.
- **That the airtime limiter refuses at the ceiling.** E13 shows relayed traffic entering the
  ledger the limiter keeps; it does not drive the band to 5 %/1 % and watch a packet be held. The
  limiter is the driver's own property with its own tests upstream, and this matrix does not
  restate it as proven here.
- **Sideband under `0400`.** It needs a display; this box is Lite. Unchanged from the MeshChat
  plan's own list.
- **A public hub.** The internet peer was a controlled LAN instance, which is what makes E8–E12
  conclusive. Reaching a public testnet hub is the same code path with an address nobody here
  controls.
