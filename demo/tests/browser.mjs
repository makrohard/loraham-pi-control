// Headless-browser smoke test for the assembled demo bundle: the real Pyodide app renders a
// pre-provisioned box, STARTS a stack via the real confirm->Apply flow, the simulated daemon
// goes LIVE (r1's card READY), and it PERSISTS across a reload. Running is verified by the
// dashboard daemon card, not the always-present stop button.
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
  // 0.2.9: Start means start — a routine Start runs the saved configuration at once (no confirm
  // page; only a consequential choice asks), so clicking the row's Start IS the start. The stack
  // body ALWAYS shows start+stop, so neither is a running signal; the authoritative running check
  // is the DASHBOARD DAEMON CARD of r1 going READY (a stack owns its radio's daemon).
  // 0.14: the stack's Start names its radio (a target select kiss@r1 / kiss@r2); the row also holds
  // the optional kiss-serial's own start forms, so the form is the one inside #stackrow-kiss that offers kiss@r1.
  await openKiss(page);
  await hasOp(page, "start");
  await page.waitForFunction(() => [...document.querySelectorAll('#stackrow-kiss form')].some((f) => {
    const op = f.querySelector('input[name="op"]'), t = f.querySelector('[name="target"]'), b = f.querySelector('button');
    return op && op.value === "start" && t && [...(t.options || [])].some((o) => o.value === "kiss@r1") && b && !b.disabled;
  }), { timeout: 30000 });
  await page.evaluate(() => {
    const f = [...document.querySelectorAll('#stackrow-kiss form')].find((f) => {
      const op = f.querySelector('input[name="op"]'), t = f.querySelector('[name="target"]');
      return op && op.value === "start" && t && [...(t.options || [])].some((o) => o.value === "kiss@r1");
    });
    f.querySelector('[name="target"]').value = "kiss@r1";
    f.querySelector('button').click();
  });

  const daemonReady = () => page.waitForFunction(() => {
    const a = [...document.querySelectorAll('a[href]')].find(
      (x) => x.getAttribute('href') === '/' || x.textContent.trim() === 'Home');
    if (a) a.click();
    const b = document.getElementById("rd-badge-r1");
    return !!b && /READY|usable/.test(b.textContent);
  }, { timeout: 30000 });

  // start worked <=> r1's daemon card is now READY (kiss owns r1)
  await daemonReady();
  console.log("OK: start kiss@r1 -> r1's daemon live (READY) on the dashboard");

  // RX-TX Monitor is LIVE: the demo's boot.js poller reads /api/daemon/r1 from the bridge
  // every tick and fills the rd-*-r1 cells (the real dash.js can't run under the Pyodide bridge).
  await page.waitForFunction(() => {
    const val = (id, re) => { const el = document.getElementById(id);
      return el && re.test((el.textContent || "").trim()); };
    return val("rd-rssiv-r1", /-?\d+/) && val("rd-uptime-r1", /\d/) &&
           val("rd-rx-r1", /\d/) && val("rd-txok-r1", /\d/);
  }, { timeout: 30000 });
  console.log("OK: r1 RX-TX monitor shows live values (RSSI/uptime/RX/TX)");

  // ONE DAEMON PER RADIO: kiss started only r1 (433), so the demo box's second radio r2 (868)
  // must show its card and stay offline: an r1 start must never bring r2 up.
  const perRadio = await page.evaluate(() => {
    const c2 = document.getElementById("radio-r2");
    if (!c2) return "missing";
    return /READY|usable/.test(c2.innerText) ? "BOTH_LIVE" : "isolated";
  });
  if (perRadio !== "isolated")
    throw new Error("r2's card: " + perRadio + " — the dual-radio demo box must show r2 offline after an r1 start");
  console.log("OK: one daemon per radio (r2 stays offline)");

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
  // Time pill: the demo box is synced (timesyncd), so the product logic shows green "NTP".
  await page.waitForFunction(() => {
    const p = document.getElementById("sys-time-pill");
    return p && p.textContent === "NTP" && p.classList.contains("pill-ok");
  }, { timeout: 30000 });
  console.log("OK: Time pill shows green NTP");
  // GPS row in the System box: the simulated receiver's fix, linked to the GPS Monitor.
  await page.waitForFunction(() => {
    const v = ((document.getElementById("sys-gps-val") || {}).textContent || "").trim();
    const p = document.getElementById("sys-gps-state");
    return p && p.textContent === "3D fix" && p.classList.contains("pill-ok")
      && /^-?\d+\.\d{6} -?\d+\.\d{6}$/.test(v);              // the pill + coordinates only
  }, { timeout: 30000 });
  const gpsHref = await page.evaluate(() => {
    const a = document.querySelector("#sys-gps a"); return a && a.getAttribute("href"); });
  if (gpsHref !== "/stacks?open=gps#gps-row") throw new Error("GPS row link: " + gpsHref);
  console.log("OK: System box GNSS row shows the simulated 3D fix as a green pill and links to the GPS Monitor");
  // Wi-Fi pill on the network line: the simulated signal, coloured by the approved bands.
  await page.waitForFunction(() => {
    const p = document.getElementById("sys-wifi-pill");
    return p && /^-\d+ dBm$/.test(p.textContent) && /pill-(ok|warn|bad)/.test(p.className);
  }, { timeout: 30000 });
  const netHref = await page.evaluate(() => {
    const a = document.querySelector("#sys-link-row th a"); return a && a.textContent + " " + a.getAttribute("href"); });
  if (netHref !== "Net /stacks?open=network#controller-network") throw new Error("Net link: " + netHref);
  console.log("OK: the Net row shows the Wi-Fi signal pill and links to the Network panel");
  // GPS MONITOR (R1): under Position, the Monitor must leave "loading…" and show the simulated
  // fix; the Skyview button must draw the satellites (the real gps.js cannot run here).
  await page.evaluate(() => {
    const a = [...document.querySelectorAll('a[href]')].find(
      (x) => x.getAttribute('href') === '/stacks' || x.textContent.trim() === 'Apps');
    if (!a) throw new Error("Apps link not found"); a.click();
  });
  await page.waitForFunction(() => !!document.getElementById("gps-monitor"), { timeout: 30000 });
  await page.evaluate(() => {
    for (const id of ["gps-row", "gps-monitor"]) {
      const d = document.getElementById(id);
      if (d && !d.open) (d.querySelector("summary") || d).click();
    }
  });
  await page.waitForFunction(() => {
    const t = (id) => ((document.getElementById(id) || {}).textContent || "").trim();
    return !/loading/i.test(t("gps-mon-state")) && /\d/.test(t("gps-mon-lat")) && / of \d/.test(t("gps-mon-sats"));
  }, { timeout: 30000 });
  await page.evaluate(() => document.getElementById("gps-sky-btn").click());
  await page.waitForFunction(() => {
    const w = document.getElementById("gps-sky-wrap"), svg = document.getElementById("gps-sky");
    return w && !w.hidden && svg && svg.querySelectorAll("circle").length > 4;   // 4 rings + satellites
  }, { timeout: 30000 });
  console.log("OK: GPS Monitor shows the simulated fix and the Skyview draws its satellites");

  // persistence across reload: kiss still running -> r1 still READY
  await page.reload({ waitUntil: "domcontentloaded" });
  await ready(page);
  await daemonReady();
  console.log("OK: running state persisted across reload (r1 still READY)");
} catch (e) {
  console.error("FAIL:", e.message, "| pageerrors:", JSON.stringify(errs.slice(0, 5)));
  rc = 1;
}
await b.close();
process.exit(rc);
