# lhpc-testlab

A **separate, downloadable** package that runs the real [LHPC](../README.md) console,
CLI, and stack processes against deterministic **fake hardware** — no Raspberry Pi, no
radio, no root. Its home is a private GitHub Codespace, but any Linux container works.

**It is not part of the shipped product.** It depends on `lhpc` and drives it through one
generic extension point (`LHPC_SYSTEM_PROVIDER`), so the `lhpc` wheel and the Pi image
carry **zero** test-lab bytes. Install it only where you want the lab.

## Install

```sh
pip install -e .            # lhpc (the product)
pip install -e ./testlab    # lhpc-testlab (this package)
```

## Use

```sh
export LHPC_SYSTEM_PROVIDER=lhpc_testlab.provider:build
export LHPC_TESTLAB=1
export LHPC_RUNTIME_ROOT="$HOME/lhpc-lab"        # empty dir
export LHPC_BOOT_ID_FILE="$LHPC_RUNTIME_ROOT/state/testlab/host/boot_id"
export LHPC_FW_PATH_PREFIX="$LHPC_RUNTIME_ROOT/state/testlab/host"

lhpc-testlab init        # create the lab root (refuses a non-empty non-lab root)
lhpc-testlab reset       # bootstrap + hardware + callsign + fakes + fake-daemon install
lhpc-testlab web         # real lhpc console + the Test Lab panel, on :8770

lhpc install kiss --yes && lhpc build kiss --yes && lhpc stack start kiss --yes
lhpc-testlab scenario degraded     # healthy | disconnected | wrong-password |
                                   # hardware-missing | degraded | recovery
lhpc-testlab inject 433 aprs-position     # RX a frame into the fake daemon → the chain
lhpc-testlab status | lhpc-testlab check
```

## What runs against fake backends

A fake `loraham_daemon` (full v112 wire protocol) → real **kiss** TNC → real **graywolf**
(+ web UI); real **meshcore**; **reticulum** (fake spidev/gpiod shims); **meshtastic**
(upstream `sim` radio); **meshcom** (QEMU ESP32); **voice**/**sideband** (headless under Xvfb).
Graywolf's iGate is forced to a local APRS-IS sink — never the live ham network.
`chat`/`nomadnet` are interactive TUIs LHPC never auto-spawns.

## How it plugs into lhpc

`ControllerService`, when nothing is injected, reads `LHPC_SYSTEM_PROVIDER` and calls
`lhpc_testlab.provider:build(paths)`, which returns the LabSystem (simulated
nmcli/systemd/logind behind the real runner), the generated manifest overlay, and the
spawn guard — only when the two-key latch (env + marker) holds. lhpc contains no
reference to "testlab".

Full docs: [`docs/testlab.md`](../docs/testlab.md). Tests: `testlab/tests/`
(`LHPC_ACCEPTANCE=1` / `LHPC_BROWSER=1` lanes; the coverage-matrix gate runs by default).
