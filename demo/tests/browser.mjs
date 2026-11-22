// Headless-browser smoke test for the assembled demo bundle: the real Pyodide app renders a
// pre-provisioned box, STARTS a stack via the real confirm->Apply flow, the simulated daemon
// goes LIVE (r1's card READY), and it PERSISTS across a reload. Running is verified by the
// dashboard daemon card, not the always-present stop button.
// The demo renders each page into the iframe #app and the product's own scripts run there (one document per page, as
// on a box); every check reads that page through doc() (boot.js).
// Env: DEMO_URL, CHROME (default /usr/bin/google-chrome).
import puppeteer from "puppeteer-core";

const URL = process.env.DEMO_URL;
if (!URL) { console.error("set DEMO_URL"); process.exit(2); }
const exe = process.env.CHROME || "/usr/bin/google-chrome";

// doc() in the page: installed after every top-level load.
const installDoc = (p) => p.evaluate(() => {
  window.doc = () => { const a = document.getElementById("app"); return (a && a.contentDocument) || document; };
});
const ready = async (p) => {
  await installDoc(p);
  await p.waitForFunction(() => { const a = document.getElementById("app");
    const t = a && (a.contentDocument ? (a.contentDocument.body || {}).innerText : a.innerText);
    return a && !a.hidden && (t || "").length > 80; }, { timeout: 180000 });
};
const hasOp = (p, op) => p.waitForFunction(
  (o) => [...doc().querySelectorAll('input[name="op"]')].some(i => i.value === o),
  { timeout: 30000 }, op);
// Each passed check is printed and remembered: a FAIL names the check after the last one that passed.
let last = "(none yet)";
const ok = (msg) => { console.log("OK: " + msg); last = msg; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const boots = (p) => p.evaluate(() => sessionStorage.getItem("demoBoots"));
// A page rendered by the demo: go() is boot.js's render of an app path; the wait ends when a NEW element matches `sel`
// (the old page's one carries a marker).
async function render(p, path, sel) {
  await p.evaluate((a) => { const e = doc().querySelector(a.sel); if (e) e.dataset.old = "1"; go("GET", a.path); }, { path, sel });
  await p.waitForFunction((sel) => { const d = doc(), e = d.querySelector(sel);
    return d.readyState === "complete" && !!e && !e.dataset.old; }, { timeout: 30000 }, sel);
}
// A user click on a <summary> (the product's accordions act on clicks), returns the <details> open state after it.
const clickSummary = (p, id) => p.evaluate((id) => {
  const d = doc().getElementById(id); if (!d) throw new Error("no #" + id);
  d.querySelector(":scope > summary").click(); return d.open; }, id);
const isOpen = (p, id) => p.evaluate((id) => { const d = doc().getElementById(id); return !!d && d.open; }, id);

// Navigate to Apps and expand the kiss row (its body lazy-loads through stacklazy.js and the bridge).
async function openKiss(p) {
  await p.evaluate(() => {
    const a = [...doc().querySelectorAll('a[href]')].find(
      x => x.getAttribute('href') === '/stacks' || x.textContent.trim() === 'Apps');
    if (!a) throw new Error("Apps link not found"); a.click();
  });
  await p.waitForFunction(() => { const d = doc(); return d && d.readyState === "complete" && !!d.getElementById("stackrow-kiss"); },
    { timeout: 30000 });
  await p.evaluate(() => {
    const d = doc().getElementById("stackrow-kiss");
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
  const dash = await page.evaluate(() => doc().body.innerText);
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
  await page.waitForFunction(() => [...doc().querySelectorAll('#stackrow-kiss form')].some((f) => {
    const op = f.querySelector('input[name="op"]'), t = f.querySelector('[name="target"]'), b = f.querySelector('button');
    return op && op.value === "start" && t && [...(t.options || [])].some((o) => o.value === "kiss@r1") && b && !b.disabled;
  }), { timeout: 30000 });
  await page.evaluate(() => {
    const f = [...doc().querySelectorAll('#stackrow-kiss form')].find((f) => {
      const op = f.querySelector('input[name="op"]'), t = f.querySelector('[name="target"]');
      return op && op.value === "start" && t && [...(t.options || [])].some((o) => o.value === "kiss@r1");
    });
    f.querySelector('[name="target"]').value = "kiss@r1";
    f.querySelector('button').click();
  });

  // r1's daemon card on the dashboard (one render of /, then dash.js's live answer)
  const daemonReady = async () => {
    await render(page, "/", "#rd-badge-r1");
    await page.waitForFunction(() => /READY|usable/.test(doc().getElementById("rd-badge-r1").textContent), { timeout: 30000 });
  };

  // start worked <=> r1's daemon card is now READY (kiss owns r1)
  await daemonReady();
  ok("start kiss@r1 -> r1's daemon live (READY) on the dashboard");

  // RX-TX Monitor is LIVE: the product's dash.js polls /api/daemon/r1 (through demoFetch) and fills the rd-*-r1 cells.
  await page.waitForFunction(() => {
    const val = (id, re) => { const el = doc().getElementById(id);
      return el && re.test((el.textContent || "").trim()); };
    return val("rd-rssiv-r1", /-?\d+/) && val("rd-uptime-r1", /\d/) &&
           val("rd-rx-r1", /\d/) && val("rd-txok-r1", /\d/);
  }, { timeout: 30000 });
  ok("r1 RX-TX monitor shows live values (RSSI/uptime/RX/TX)");

  // ONE DAEMON PER RADIO: kiss started only r1 (433), so the demo box's second radio r2 (868)
  // must show its card and stay offline: an r1 start must never bring r2 up.
  const perRadio = await page.evaluate(() => {
    const c2 = doc().getElementById("radio-r2");
    if (!c2) return "missing";
    return /READY|usable/.test(c2.innerText) ? "BOTH_LIVE" : "isolated";
  });
  if (perRadio !== "isolated")
    throw new Error("r2's card: " + perRadio + " — the dual-radio demo box must show r2 offline after an r1 start");
  ok("one daemon per radio (r2 stays offline)");

  // SYSTEM box: open it and confirm the simulated host backend fills live metrics (the product's system.js).
  const sysOpen = () => page.evaluate(() => {
    const box = doc().getElementById("sysbox");
    if (box && !box.open) (box.querySelector("summary") || box).click();
  });
  const sysLive = () => page.waitForFunction(() => {
    const d = doc();
    const hasNum = (id) => { const el = d.getElementById(id);
      return el && /[0-9]/.test((el.textContent || "")); };   // "…"/"?" placeholders have no digit
    const barSet = (id) => { const el = d.getElementById(id);
      return el && /[0-9]/.test(el.style.width || ""); };      // gauge fill width applied
    const sparkSet = (id) => { const el = d.getElementById(id);
      const l = el && el.querySelector("polyline");
      return l && (l.getAttribute("points") || "").length > 3; };   // sparkline drawn
    const cores = d.getElementById("sys-cpu-cores");
    return hasNum("sys-cpu-val") && hasNum("sys-mem-val") &&
           barSet("sys-mem-bar") && barSet("sys-temp-bar") &&
           sparkSet("sys-cpu-spark") && sparkSet("sys-mem-spark") &&
           !!cores && cores.childElementCount > 0;             // per-core CPU bars built
  }, { timeout: 30000 });
  await sysOpen();
  await sysLive();
  ok("System box live — values, gauge bars, per-core CPU bars, and sparklines");
  // Time pill: the demo box is synced (timesyncd), so the product logic shows green "NTP".
  await page.waitForFunction(() => {
    const p = doc().getElementById("sys-time-pill");
    return p && p.textContent === "NTP" && p.classList.contains("pill-ok");
  }, { timeout: 30000 });
  ok("Time pill shows green NTP");
  // GPS row in the System box: the simulated receiver's fix, linked to the GPS Monitor.
  await page.waitForFunction(() => {
    const v = ((doc().getElementById("sys-gps-val") || {}).textContent || "").trim();
    const p = doc().getElementById("sys-gps-state");
    return p && p.textContent === "3D fix" && p.classList.contains("pill-ok")
      && /^-?\d+\.\d{6} -?\d+\.\d{6}$/.test(v);              // the pill + coordinates only
  }, { timeout: 30000 });
  const gpsHref = await page.evaluate(() => {
    const a = doc().querySelector("#sys-gps a"); return a && a.getAttribute("href"); });
  if (gpsHref !== "/stacks?open=gps#gps-row") throw new Error("GPS row link: " + gpsHref);
  ok("System box GNSS row shows the simulated 3D fix as a green pill and links to the GPS Monitor");
  // Wi-Fi pill on the network line: the simulated signal, coloured by the approved bands.
  await page.waitForFunction(() => {
    const p = doc().getElementById("sys-wifi-pill");
    return p && /^-\d+ dBm$/.test(p.textContent) && /pill-(ok|warn|bad)/.test(p.className);
  }, { timeout: 30000 });
  const netHref = await page.evaluate(() => {
    const a = doc().querySelector("#sys-link-row th a"); return a && a.textContent + " " + a.getAttribute("href"); });
  if (netHref !== "Net /stacks?open=network#controller-network") throw new Error("Net link: " + netHref);
  ok("the Net row shows the Wi-Fi signal pill and links to the Network panel");
  // GPS MONITOR (R1): under Position, the Monitor must leave "loading…" and show the simulated
  // fix; the Skyview button must draw the satellites (the product's gps.js).
  await page.evaluate(() => {
    const a = [...doc().querySelectorAll('a[href]')].find(
      (x) => x.getAttribute('href') === '/stacks' || x.textContent.trim() === 'Apps');
    if (!a) throw new Error("Apps link not found"); a.click();
  });
  await page.waitForFunction(() => { const d = doc(); return d && d.readyState === "complete" && !!d.getElementById("gps-monitor"); },
    { timeout: 30000 });
  await page.evaluate(() => {
    for (const id of ["controller-row", "gps-row", "gps-monitor"]) {   // outside in: gps.js polls a SHOWN Monitor
      const d = doc().getElementById(id);
      if (d && !d.open) (d.querySelector("summary") || d).click();
    }
  });
  await page.waitForFunction(() => {
    const t = (id) => ((doc().getElementById(id) || {}).textContent || "").trim();
    return !/loading/i.test(t("gps-mon-state")) && /\d/.test(t("gps-mon-lat")) && / of \d/.test(t("gps-mon-sats"));
  }, { timeout: 30000 });
  await page.evaluate(() => doc().getElementById("gps-sky-btn").click());
  await page.waitForFunction(() => {
    const w = doc().getElementById("gps-sky-wrap"), svg = doc().getElementById("gps-sky");
    return w && !w.hidden && svg && svg.querySelectorAll("circle").length > 4;   // 4 rings + satellites
  }, { timeout: 30000 });
  ok("GPS Monitor shows the simulated fix and the Skyview draws its satellites");

  await page.evaluate(() => doc().getElementById("gps-nmea-btn").click());
  await page.waitForFunction(() => {
    const w = doc().getElementById("gps-nmea-wrap"), body = doc().getElementById("gps-nmea-body");
    return w && !w.hidden && body && /\$G/.test(body.textContent);
  }, { timeout: 30000 });
  ok("the GPS Monitor's NMEA pane shows sentences");

  // The Monitor page's one daemon pin (in its node title, F15; rendered hidden until a live answer) shows r1's state.
  const MON = "/stacks/daemon/menu/stack-settings-daemon--monitor?radio=r1";
  await render(page, MON, "#stack-settings-daemon--monitor");
  await page.waitForFunction(() => {
    const b = doc().querySelector("#stack-settings-daemon--monitor > summary .dbadge");
    return !!b && !b.hidden && b.offsetWidth > 0 && b.textContent === "daemon READY";
  }, { timeout: 30000 });
  ok("the Monitor page shows r1's daemon pin (daemon READY)");
  // ... and a closed block's pin stays hidden: in the Settings tree the Monitor node is closed; after dash.js's live
  // ticks its pin is still hidden (the console's rule: no state shown for a closed node).
  await render(page, "/stacks/daemon/menu/stack-settings-daemon?radio=r1", "#stack-settings-daemon--monitor");
  if (await isOpen(page, "stack-settings-daemon--monitor")) throw new Error("the Settings tree's Monitor node is open");
  await sleep(3500);
  const closedPin = await page.evaluate(() =>
    doc().querySelector("#stack-settings-daemon--monitor > summary .dbadge").hidden);
  if (!closedPin) throw new Error("the closed Monitor node's pin is shown");
  ok("a closed Monitor node keeps its pin hidden");

  // ---- the parity features (each fails on an older bundle on its widget): #7, #13, CAD, #23, #18 ----
  await render(page, "/", "#sysbox");
  await sysOpen(); await sysLive();
  // (b) #7: the POWER row and the host line's core count, both system.js's own branches.
  const sysExtra = () => page.waitForFunction(() => {
    const d = doc();
    const pill = d.getElementById("sys-power-pill"), info = d.getElementById("sys-info");
    return !!pill && pill.textContent === "no under-voltage alarm" && !!info && /\d+ cores/.test(info.textContent);
  }, { timeout: 30000 });
  await sysExtra();
  ok("System box POWER row 'no under-voltage alarm' and the host line's cores");
  // (a) #13: the Monitor's live views, daemoncfg.js through demoFetch: STATUS (with the radio's ID) and STATS.
  const monitorLive = async () => {          // the Monitor page shows one view: STATUS, or STATS with view=stats
    await render(page, MON, ".daemonview-text");
    await page.waitForFunction(() => [...doc().querySelectorAll(".daemonview-text")].some((x) =>
      x.textContent.startsWith("STATUS — r1 (live)") && /\bID=r1\b/.test(x.textContent)), { timeout: 3000 });
    await render(page, MON + "&view=stats", ".daemonview-text");
    await page.waitForFunction(() => [...doc().querySelectorAll(".daemonview-text")].some((x) =>
      x.textContent.startsWith("STATS — r1 (live)")), { timeout: 3000 });
  };
  await monitorLive();
  ok("the Monitor's STATUS (ID=r1) and STATS views are live within 3 s");
  // (a) #13, the logs: the RF log page's decoded view (rflog.js) and the daemon's log tail (logpanel.js) have lines.
  // A log check holds a line the demo writes into the daemon's start log (service.py _write_logs), not just any text:
  // "(no output yet)" is what logpanel.js/logs.js show for an empty or missing log.
  const START = "[demo] loraham-daemon started for daemon@r1";
  const rfLines = async () => {
    await render(page, "/logs/loraham-daemon?job=r1/rf-daemon.log&radio=r1", "#rfview");
    await page.waitForFunction(() => doc().querySelectorAll("#rfview tbody tr").length > 0, { timeout: 3000 });
  };
  await rfLines();
  ok("the RF log's decoded view of r1 has lines");
  const DLOG = "#log-loraham-daemon";
  const daemonTail = async () => {
    await render(page, "/stacks?open=daemon", "#stackrow-daemon");
    await page.waitForFunction((sel) => !!doc().querySelector(sel), { timeout: 30000 }, DLOG);
    await page.evaluate((sel) => { const d = doc().querySelector(sel); if (!d.open) d.querySelector(":scope > summary").click(); }, DLOG);
    await page.waitForFunction(({ sel, start }) => {
      const t = ((doc().querySelector(sel + " .logpanel-text") || {}).textContent || "").trim();
      return t.includes(start) && !/could not load/.test(t);
    }, { timeout: 3000 }, { sel: DLOG, start: START });
  };
  await daemonTail();
  ok("the daemon's log tail holds its start-log line");

  await render(page, "/", "#sysbox");
  // (g) CAD "Scan now": dash.js's POST (form-urlencoded, with its CSRF token) answers through the bridge.
  await page.evaluate(() => doc().querySelector('[data-scan-rid="r1"]').click());
  await page.waitForFunction(() => {
    const t = ((doc().getElementById("rd-cad-r1") || {}).textContent || "").trim();
    return t !== "" && t !== "—" && t !== "scanning…" && t !== "?";
  }, { timeout: 30000 });
  ok("CAD Scan now answers through the bridge");

  // (c) #23: the Apps accordions are the product's (stacks_state.js): one open main row, one open sibling inside a
  // stack, and the open row is remembered by the server (the lhpc_open cookie through the bridge).
  const accordion = async () => {
    await render(page, "/stacks", "#stackrow-kiss");
    if (await isOpen(page, "stackrow-kiss")) await clickSummary(page, "stackrow-kiss");
    await clickSummary(page, "stackrow-chat");
    await clickSummary(page, "stackrow-kiss");
    if (await isOpen(page, "stackrow-chat") || !await isOpen(page, "stackrow-kiss"))
      throw new Error("Apps: opening kiss left chat open (or kiss closed)");
    await page.waitForFunction(() => !!doc().getElementById("stack-info-kiss"), { timeout: 30000 });
    await clickSummary(page, "stack-install-kiss");
    await clickSummary(page, "stack-info-kiss");
    if (await isOpen(page, "stack-install-kiss") || !await isOpen(page, "stack-info-kiss"))
      throw new Error("kiss: opening its Info left Install open (nested)");
    await render(page, "/", "#sysbox");
    await render(page, "/stacks", "#stackrow-kiss");
    if (!await isOpen(page, "stackrow-kiss")) throw new Error("Apps: the remembered open row (kiss) is closed after away/back");
  };
  await accordion();
  ok("Apps accordions (main + nested) and the remembered open row across away/back");
  // ... and the app's CSRF token survives the cookie transport: a CSRF-protected form after it is accepted (Restart kiss@r1).
  await page.evaluate(() => {
    const f = [...doc().querySelectorAll('#stackrow-kiss form')].find((f) => {
      const op = f.querySelector('input[name="op"]'), t = f.querySelector('[name="target"]');
      return op && op.value === "restart" && t && [...(t.options || [])].some((o) => o.value === "kiss@r1");
    });
    if (!f) throw new Error("no Restart kiss@r1 form");
    if (!f.querySelector('input[name="_csrf"]')) throw new Error("the Restart form carries no CSRF token");
    f.querySelector('[name="target"]').value = "kiss@r1";
    doc().documentElement.dataset.old = "1";
    f.querySelector('button').click();
  });
  await page.waitForFunction(() => { const d = doc(), e = d && d.documentElement;
    return !!e && !e.dataset.old && d.readyState === "complete"; }, { timeout: 30000 });
  const posted = await page.evaluate(() => doc().title + " | " + doc().body.innerText);
  if (/Bad Request|CSRF/i.test(posted)) throw new Error("the CSRF form after the remembered row was refused: " + posted.slice(0, 300));
  // Accepted means the restart's success flash (the demo's simulated restart, service.py), not merely "no CSRF error":
  // an error page has neither.
  if (!posted.includes("Restarted kiss (simulated).")) throw new Error("the Restart kiss@r1 after the remembered row did not flash Restarted: " + posted.slice(0, 300));
  ok("a CSRF-protected form after the remembered row is accepted (Restarted kiss)");


  // G2 #18: a program's log page streams: the product's logs.js polls /api/logs/<target> through the bridge (the page's
  // first lines are the server's; the poll is the stream) and the box holds the daemon's start-log lines. Only a poll
  // that ANSWERED ok counts (logs.js rewrites the box only then), and the box then holds the start-log line.
  await page.evaluate(() => { const real = window.demoFetch; window.__polls = 0;
    window.demoFetch = (u, o) => { const r = real(u, o);
      return String(u).startsWith("/api/logs/loraham-daemon") ? r.then((x) => { if (x.ok) window.__polls++; return x; }) : r; };
    window.__realFetch = real; });
  await render(page, "/logs/loraham-daemon?radio=r1", "#logbox");
  await page.waitForFunction((start) => {
    const t = ((doc().getElementById("logbox") || {}).textContent || "").trim();
    return window.__polls > 0 && t.includes(start);
  }, { timeout: 3500 }, START);
  await page.evaluate(() => { window.demoFetch = window.__realFetch; });
  ok("the daemon's log page streams its start-log lines");
  // ---- frame-only checks (the new mechanism; not red-before evidence) ----
  // (i) every product script of the page is loaded from the index directory by a RELATIVE src (on Pages that
  // directory is the repo's path: a root-relative /static/ would 404 there even where a server root makes it pass).
  const scripts = await page.evaluate(() => {
    const dir = new URL(".", location.href).href, d = doc();
    const got = new Map(d.defaultView.performance.getEntriesByType("resource").map((e) => [e.name, e.responseStatus]));
    return [...d.scripts].filter((s) => s.src).map((s) => [s.getAttribute("src"),
      s.src.startsWith(dir) && !s.getAttribute("src").startsWith("/"), got.get(s.src)]);   // relative: also on a subpath
  });
  const badScript = scripts.find(([, under, st]) => !under || st !== 200);
  if (!scripts.length || badScript) throw new Error("frame script not loaded from the index directory: " + JSON.stringify(badScript || scripts));
  ok("the page's " + scripts.length + " product scripts load from the index directory");

  // (d) away and back 3x: each widget still works on each new page; ONE log panel open = exactly ONE request.
  for (let i = 1; i <= 3; i++) {
    await render(page, "/", "#sysbox");
    await sysOpen(); await sysLive(); await sysExtra();
    await monitorLive();
    await rfLines(); await daemonTail();
    await accordion();
  }
  await render(page, "/stacks", "#controller-logs");
  const calls = await page.evaluate(async () => {
    const real = window.demoFetch, seen = [];
    window.demoFetch = (u, o) => { seen.push(String(u)); return real(u, o); };
    const d = doc(), lp = d.getElementById("controller-logs");
    const row = lp.parentElement.closest("details");
    for (let r = row; r; r = r.parentElement && r.parentElement.closest("details")) if (!r.open) r.open = true;
    const src = lp.getAttribute("data-src");
    seen.length = 0;
    lp.querySelector(":scope > summary").click();
    await new Promise((r) => setTimeout(r, 1500));
    window.demoFetch = real;
    return seen.filter((u) => u.startsWith(src)).length;
  });
  if (calls !== 1) throw new Error("one log panel open made " + calls + " requests (expected 1)");
  ok("3x away/back, every widget still live; one log panel open = one request");

  // (e) B2: 25 s on the dashboard, no new Pyodide boot (the page's own reload rules stay inside the frame).
  await render(page, "/", "#sysbox");
  await sleep(25000);
  if (await boots(page) !== "1") throw new Error("Pyodide booted again on the dashboard: " + await boots(page));
  ok("25 s on the dashboard without a new boot");
  // (f) a script's location.reload() in the frame renders the same page again through the bridge.
  await page.evaluate(() => { doc().documentElement.dataset.old = "1"; doc().defaultView.location.reload(); });
  await page.waitForFunction(() => { const d = doc(), e = d && d.documentElement;
    return !!e && !e.dataset.old && d.readyState === "complete" && !!d.getElementById("sysbox"); }, { timeout: 30000 });
  if (await boots(page) !== "1") throw new Error("a frame reload booted Pyodide again");
  // Its links route through the bridge again: the click is made once the page holds the link (a click on the reloaded
  // document before the replay replaced it is a navigation, which the replay rule renders too; no replay -> no Apps page).
  await page.waitForFunction(() => { const d = doc(), a = d.readyState === "complete" &&
      [...d.querySelectorAll('a[href]')].find((x) => x.getAttribute("href") === "/stacks");
    if (a) a.click(); return !!a; }, { timeout: 30000 });
  await page.waitForFunction(() => { const d = doc(); return d.readyState === "complete" && !!d.getElementById("stackrow-kiss"); },
    { timeout: 30000 });
  ok("location.reload() in the page renders it again through the bridge (its links route), no new boot");
  // (f2) a script's location.href = an app path (auto_install.js) renders that path through the bridge.
  await page.evaluate(() => { doc().documentElement.dataset.old = "1"; doc().defaultView.location.href = "/auto-install?x=1"; });
  await page.waitForFunction(() => { const d = doc(), e = d && d.documentElement;
    return !!e && !e.dataset.old && d.readyState === "complete" && d.title.startsWith("Auto-install"); },   // that page itself
  { timeout: 30000 });
  if (await boots(page) !== "1") throw new Error("a frame navigation booted Pyodide again");
  ok("location.href = /auto-install in the page renders it through the bridge, no new boot");
  // (j) a fragment: /#sysbox scrolls the System box into view, as a box's page load does (Chrome's fragment navigation
  // opens the <details> AROUND a target, never a target that is itself a <details>: on a box it stays closed, "(open it)").
  await page.setViewport({ width: 1280, height: 400 });
  await render(page, "/", "#sysbox");
  await render(page, "/#sysbox", "#sysbox");
  await page.waitForFunction(() => { const w = doc().defaultView, b = doc().getElementById("sysbox"), r = b.getBoundingClientRect();
    return w.location.hash === "#sysbox" && w.scrollY > 0 && r.top > -4 && r.top < w.innerHeight; }, { timeout: 30000 });   // -4: sub-pixel rounding
  await page.setViewport({ width: 1280, height: 900 });
  ok("/#sysbox scrolls the System box into view");
  // (k) an in-page #anchor jumps inside the page, as on a box (the meshcore body's "Settings → Repeater"): the frame
  // stays the page (about:srcdoc), the target is in view, no new boot.
  await render(page, "/stacks?open=meshcore", "#stackrow-meshcore");
  const ANCHOR = '#stackrow-meshcore a[href="#stack-settings-meshcore"]';
  await page.waitForFunction((sel) => !!doc().querySelector(sel), { timeout: 30000 }, ANCHOR);
  await page.evaluate((sel) => doc().querySelector(sel).click(), ANCHOR);
  await page.waitForFunction(() => { const w = doc().defaultView, t = doc().getElementById("stack-settings-meshcore");
    const r = t && t.getBoundingClientRect();
    return w.location.href === "about:srcdoc#stack-settings-meshcore" && !!r && r.top > -4 && r.top < w.innerHeight; },
  { timeout: 30000 });
  if (await boots(page) !== "1") throw new Error("an in-page anchor booted Pyodide again");
  ok("an in-page #anchor (Settings → Repeater) jumps inside the page, no new boot");
  // (l) a link to another site (the footer's GitHub link, base.html) opens a new tab; the frame stays the page: a frame
  // navigated to another origin would show that site in the demo, and its pages are unreadable to the demo.
  await render(page, "/", "#sysbox");
  const ext = await page.evaluate(() => {
    const seen = [], real = window.open;
    window.open = (...a) => { seen.push(a.slice(0, 2)); return null; };
    try {
      const a = [...doc().querySelectorAll('a[href^="https://"]')].find((x) => !x.target);
      if (!a) return { href: "", seen };
      doc().documentElement.dataset.old = "1";
      a.click();
      return { href: a.getAttribute("href"), seen };
    } finally { window.open = real; }
  });
  await sleep(500);
  const extStayed = await page.evaluate(() => { const d = doc(); return d !== document && !!d.documentElement.dataset.old; });
  if (!ext.href || JSON.stringify(ext.seen) !== JSON.stringify([[ext.href, "_blank"]]) || !extStayed)
    throw new Error("an external link did not open a new tab with the page kept: " + JSON.stringify({ ...ext, extStayed }));
  ok("an external link (" + ext.href + ") opens a new tab, the page stays");
  // (m) a script's navigation of the frame to another origin: the demo renders its page again (that frame's location
  // is unreadable to the demo; reading it must not throw).
  await page.evaluate(() => { doc().documentElement.dataset.old = "1"; doc().defaultView.location.href = "https://example.invalid/"; });
  await page.waitForFunction(() => { const d = doc(), e = d && d.documentElement;
    return d !== document && !!e && !e.dataset.old && d.readyState === "complete" && !!d.getElementById("sysbox"); },
  { timeout: 30000 });
  if (await boots(page) !== "1") throw new Error("a cross-origin frame navigation booted Pyodide again");
  ok("a frame navigation to another origin renders the page again, no new boot");
  // (h) 390 px: no horizontal page scroll, neither the demo page nor the page shown.
  await page.setViewport({ width: 390, height: 844 });
  await sleep(500);
  const wide = await page.evaluate(() => {
    const top = document.documentElement, d = doc().documentElement;
    return [top.scrollWidth, window.innerWidth, d.scrollWidth, doc().defaultView.innerWidth];
  });
  if (wide[0] > wide[1] || wide[2] > wide[3]) throw new Error("horizontal scroll at 390 px: " + JSON.stringify(wide));
  ok("no horizontal scroll at 390 px");
  await page.setViewport({ width: 1280, height: 900 });

  // persistence across reload: kiss still running -> r1 still READY
  await page.reload({ waitUntil: "domcontentloaded" });
  await ready(page);
  await daemonReady();
  ok("running state persisted across reload (r1 still READY)");
} catch (e) {
  console.error("FAIL (the check after: " + last + "):", e.message, "| pageerrors:", JSON.stringify(errs.slice(0, 5)));
  rc = 1;
}
await b.close();
process.exit(rc);
