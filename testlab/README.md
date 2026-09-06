# lhpc-testlab

A **separate, downloadable** package that runs the real LHPC console, CLI and stack processes
against deterministic **fake hardware**: no Raspberry Pi, no radio, no root. It is not part of
the shipped product: it depends on `lhpc` and drives it through one generic extension point
(`LHPC_SYSTEM_PROVIDER`), so the `lhpc` wheel and the Pi image carry zero test-lab bytes.
Install with `pip install -e . && pip install -e ./testlab`; everything else (launch, scenarios,
the verification lanes, Codespaces) is in [docs/testlab.md](../docs/testlab.md).
