// Headless-browser smoke test for the assembled demo bundle: the real Pyodide app renders a
// pre-provisioned box, STARTS a stack via the real confirm->Apply flow, the simulated daemon
// goes LIVE (433 panel READY), and it PERSISTS across a reload. Running is verified by the
// dashboard daemon panel, not the always-present stop button.
// Env: DEMO_URL, CHROME (default /usr/bin/google-chrome).
import puppeteer from "puppeteer-core";

const URL = process.env.DEMO_URL;
if (!URL) { console.error("set DEMO_URL"); process.exit(2); }
const exe = process.env.CHROME || "/usr/bin/google-chrome";

const ready = (p) => p.waitForFunction(
  () => { const a = document.getElementById("app"); return a && !a.hidden && a.innerText.length > 80; },
  { timeout: 180000 });
const hasOp = (p, op) => p.waitForFunction(
  (o) => [...document.querySelectorAll('input[name="op"]')].some(i => i.value === o),
  { timeout: 30000 }, op);

// Navigate to Apps and expand the kiss row (its body lazy-loads via the bridge).
async function openKiss(p) {
  await p.evaluate(() => {
    const a = [...document.querySelectorAll('a[href]')].find(
      x => x.getAttribute('href') === '/stacks' || x.textContent.trim() === 'Apps');
    if (!a) throw new Error("Apps link not found"); a.click();
  });
  await p.waitForFunction(() => !!document.getElementById("stackrow-kiss"), { timeout: 30000 });
  await p.evaluate(() => {
    const d = document.getElementById("stackrow-kiss");
    if (!d.open) (d.querySelector("summary") || d).click();
  });
}

const b = await puppeteer.launch({ executablePath: exe, headless: true,
  args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"] });
const page = await b.newPage();
const errs = [];
page.on("pageerror", (e) => errs.push(e.message.slice(0, 150)));
let rc = 0;
try {
  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 60000 });
  await ready(page);
  const dash = await page.evaluate(() => document.getElementById("app").innerText);
  if (!/LoRaHAM Pi Control/.test(dash)) throw new Error("dashboard did not render");
  // the demo is a pre-provisioned box: the dashboard must NOT show the fresh-box prompts
  if (/Nothing is installed yet/i.test(dash))
    throw new Error("dashboard shows fresh/empty box despite pre-install");
  if (/Daemon not installed/i.test(dash))
    throw new Error("dashboard shows 'Daemon not installed' despite pre-install");

  // DEFAULT state: every stack pre-installed+built, so kiss offers START straight away.
  // Start it via the real confirm->Apply flow. Note: the confirm form has BOTH a
  // "Save params" and an "Apply start" button — we must click "Apply" by text, and the
  // stack body ALWAYS shows start+stop, so neither is a running signal; the authoritative
  // running check is the DASHBOARD DAEMON PANEL going READY (a stack owns its band's daemon).
  await openKiss(page);
  await hasOp(page, "start");
  await page.evaluate(() => {
    const f = [...document.querySelectorAll('form')].find((f) => {
      const op = f.querySelector('input[name="op"]'), t = f.querySelector('input[name="target"]');
      return op && op.value === "start" && t && /kiss/.test(t.value);
    });
    if (!f) throw new Error("kiss start form not found");
    (f.querySelector('button') || { click() { f.requestSubmit(); } }).click();
  });
  await page.waitForFunction(
    () => [...document.querySelectorAll('input[name="confirmed"]')].some(i => i.value === "yes"),
    { timeout: 30000 });
  await page.evaluate(() => {
    const f = [...document.querySelectorAll('form')].find(
      (f) => f.querySelector('input[name="confirmed"][value="yes"]') &&
             (f.querySelector('input[name="op"]') || {}).value === "start");
    if (!f) throw new Error("confirm Apply form not found");
    const btn = [...f.querySelectorAll('button')].find((x) => /apply/i.test(x.textContent))
      || f.querySelector('button');
    if (!btn) throw new Error("Apply button not found");
    btn.click();
  });

  const daemonReady = () => page.waitForFunction(() => {
    const a = [...document.querySelectorAll('a[href]')].find(
      (x) => x.getAttribute('href') === '/' || x.textContent.trim() === 'Home');
    if (a) a.click();
    const t = document.getElementById("app").innerText;
    return /433 MHz[^]*?(READY|usable)/.test(t);
  }, { timeout: 30000 });

  // start worked <=> the 433 daemon panel is now READY (kiss owns 433)
  await daemonReady();
  console.log("OK: start kiss (Apply) -> 433 daemon live (READY) on the dashboard");

  // RX-TX Monitor is LIVE: the demo's boot.js poller reads /api/daemon/433 from the bridge
  // every tick and fills the rd-* cells (the real dash.js can't run under the Pyodide bridge).
  await page.waitForFunction(() => {
    const val = (id, re) => { const el = document.getElementById(id);
      return el && re.test((el.textContent || "").trim()); };
    return val("rd-rssiv-433", /-?\d+/) && val("rd-uptime-433", /\d/) &&
           val("rd-rx-433", /\d/) && val("rd-txok-433", /\d/);
  }, { timeout: 30000 });
  console.log("OK: 433 RX-TX monitor shows live values (RSSI/uptime/RX/TX)");

  // ONE DAEMON PER RADIO: kiss started only 433, so an 868 card (dual-radio mode) must
  // stay offline — a 433 start must never bring 868 up. (N/A in single-radio mode.)
  const perRadio = await page.evaluate(() => {
    const c8 = [...document.querySelectorAll('[data-radio-band]')].find(
      (c) => c.getAttribute("data-radio-band") === "868");
    if (!c8) return "single";
    return /READY|usable/.test(c8.innerText) ? "BOTH_LIVE" : "isolated";
  });
  if (perRadio === "BOTH_LIVE")
    throw new Error("868 went live from a 433-only start — one-daemon-per-radio broken");
  console.log("OK: one daemon per radio (" + perRadio + ")");

  // SYSTEM box: open it and confirm the simulated host backend fills live metrics.
  await page.evaluate(() => {
    const box = document.getElementById("sysbox");
    if (box && !box.open) (box.querySelector("summary") || box).click();
  });
  await page.waitForFunction(() => {
    const hasNum = (id) => { const el = document.getElementById(id);
      return el && /[0-9]/.test((el.textContent || "")); };   // "…"/"?" placeholders have no digit
    const barSet = (id) => { const el = document.getElementById(id);
      return el && /[0-9]/.test(el.style.width || ""); };      // gauge fill width applied
    const sparkSet = (id) => { const el = document.getElementById(id);
      const l = el && el.querySelector("polyline");
      return l && (l.getAttribute("points") || "").length > 3; };   // sparkline drawn
    const cores = document.getElementById("sys-cpu-cores");
    return hasNum("sys-cpu-val") && hasNum("sys-mem-val") &&
           barSet("sys-mem-bar") && barSet("sys-temp-bar") &&
           sparkSet("sys-cpu-spark") && sparkSet("sys-mem-spark") &&
           !!cores && cores.childElementCount > 0;             // per-core CPU bars built
  }, { timeout: 30000 });
  console.log("OK: System box live — values, gauge bars, per-core CPU bars, and sparklines");

  // persistence across reload: kiss still running -> 433 still READY
  await page.reload({ waitUntil: "domcontentloaded" });
  await ready(page);
  await daemonReady();
  console.log("OK: running state persisted across reload (433 still READY)");
} catch (e) {
  console.error("FAIL:", e.message, "| pageerrors:", JSON.stringify(errs.slice(0, 5)));
  rc = 1;
}
await b.close();
process.exit(rc);
