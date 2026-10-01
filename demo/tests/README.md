# demo tests

The two gates the Pages workflow runs before deploying. Run them from `demo/` after assembling
the bundle ([demo README](../README.md#develop--test-locally)).

## Contents

- [Boot test](#boot-test)
- [Browser smoke test](#browser-smoke-test)

## Boot test

Headless boot + lifecycle check: renders the real lhpc routes under Pyodide with the demo
provider. Requires node and the `pyodide` npm package.

```
LHPC_WHEEL="$(ls web/wheels/loraham_pi_control-*.whl)" DEMO_DIR="$PWD" node tests/boot.mjs
```

## Browser smoke test

Renders the demo, starts a stack (the simulated 433 daemon goes READY) and checks that it
persists across a reload. Requires `puppeteer-core` and a Chrome/Chromium (`CHROME` = its path, default `/usr/bin/google-chrome`).

```
( cd web && python3 -m http.server 8099 & )
DEMO_URL=http://127.0.0.1:8099/index.html node tests/browser.mjs
```
