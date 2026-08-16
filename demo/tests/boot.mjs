// Headless demo boot test. Loads the lhpc wheel into Pyodide, mounts the independent
// lhpc_demo package, wires the demo provider, and renders the real routes.
// Env: LHPC_WHEEL=<path to loraham_pi_control wheel>, DEMO_DIR=<path to demo/>.
import { loadPyodide } from "pyodide";
import { readFileSync } from "fs";
import { basename } from "path";

const wheel = process.env.LHPC_WHEEL;
const demoDir = process.env.DEMO_DIR || new URL("..", import.meta.url).pathname;
if (!wheel) { console.error("set LHPC_WHEEL to the lhpc wheel path"); process.exit(2); }

const py = await loadPyodide();
await py.loadPackage("micropip");
const whl = basename(wheel);
py.FS.writeFile("/tmp/" + whl, readFileSync(wheel));
await py.pyimport("micropip").install("emfs:/tmp/" + whl, { keep_going: true });
py.FS.mkdirTree("/demo");
py.FS.mount(py.FS.filesystems.NODEFS, { root: demoDir }, "/demo");
const out = (await py.runPythonAsync(readFileSync(new URL("./probe.py", import.meta.url), "utf8"))) || "";
console.log(out);
// Fail on empty output, a traceback (FATAL), OR any 5xx status (unquoted integer, e.g. `: 500`).
if (!out || out.includes('"FATAL"') || /:\s*5\d\d\b/.test(out)) process.exit(1);
