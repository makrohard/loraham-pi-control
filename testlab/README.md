# lhpc-testlab

A separate package that runs the real LHPC console, CLI and stack processes against
deterministic fake hardware: no Raspberry Pi, no radio, no root. It depends on `lhpc` and drives
it through one generic extension point (`LHPC_SYSTEM_PROVIDER`); the `lhpc` wheel and the Pi
image carry none of it.

## Contents

- [Install](#install)
- [Documentation](#documentation)

## Install

```sh
pip install -e . && pip install -e ./testlab
```

## Documentation

Launch, scenarios, the verification lanes (unit and the four opt-in lanes) and Codespaces: [docs/testlab.md](../docs/testlab.md).
