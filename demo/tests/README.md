# demo tests

Headless boot check: renders the real lhpc routes under Pyodide + the demo provider.

```
LHPC_WHEEL=/path/to/loraham_pi_control-<ver>-py3-none-any.whl \
  node tests/boot.mjs
```

Requires node + the `pyodide` npm package. The Pages workflow builds the wheel and runs
this as a gate before deploying.

## Browser smoke test

Assemble the bundle (build both wheels into `web/wheels/`, lhpc static into `web/static/`),
serve `web/`, then:

```
DEMO_URL=http://127.0.0.1:8099/index.html node tests/browser.mjs
```

Requires `puppeteer-core` + a Chrome/Chromium (set `CHROME` to its path).
