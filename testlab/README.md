# lhpc-testlab

A **separate, downloadable** package that runs the real LHPC console, CLI and stack processes
against deterministic **fake hardware**: no Raspberry Pi, no radio, no root. It is not part of
the shipped product: it depends on `lhpc` and drives it through one generic extension point
(`LHPC_SYSTEM_PROVIDER`), so the `lhpc` wheel and the Pi image carry zero test-lab bytes.
Install with `pip install -e . && pip install -e ./testlab`; everything else (launch, scenarios,
the verification lanes, Codespaces) is in [docs/testlab.md](../docs/testlab.md).

## The three lanes

| lane | proves | run it |
|---|---|---|
| `tests/unit` | the simulator itself — provider, runner, scenarios, fake host services | `pytest testlab/tests/unit -q` |
| `tests/acceptance` | the real `lhpc` executable and server over the simulated host | `LHPC_ACCEPTANCE=1 pytest testlab/tests/acceptance -q` |
| `tests/browser` | the console in real headless Chromium (`headless=True`, no virtual display) | `LHPC_BROWSER=1 pytest testlab/tests/browser -q` |

The acceptance lane needs a virtual display only because `voice` and `sideband` are GTK/Kivy
programs; the browser lane needs none. Both skip with a reason when their opt-in is absent.
