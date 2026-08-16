"use strict";
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.2/full/";
const $ = (id) => document.getElementById(id);
const setStatus = (t) => { const s = $("status"); if (s) s.textContent = t; };
let bridge = null;

async function main() {
  try {
    setStatus("starting Python…");
    const py = await loadPyodide({ indexURL: PYODIDE });
    setStatus("loading packages…");
    await py.loadPackage("micropip");
    const micropip = py.pyimport("micropip");
    setStatus("installing LHPC…");
    const wheels = await (await fetch("./wheels.json", { cache: "no-cache" })).json();
    await micropip.install(wheels, { keep_going: true });
    bridge = await py.runPythonAsync("import lhpc_demo.bridge as _b; _b");
    bridge.boot(localStorage.getItem("lhpc_demo_state") || "");
    await go("GET", "/");
    $("loader").style.display = "none";
    $("app").hidden = false;
    liveTick();
    setInterval(liveTick, 1500);   // live RX-TX monitor + System box, updated in place
  } catch (e) {
    setStatus("failed to start: " + (e && e.message ? e.message : e));
    console.error(e);
  }
}

function persist() {
  try { localStorage.setItem("lhpc_demo_state", bridge.dump_state()); } catch (_) {}
}

// The app defers each stack's heavy body to a fetch on first expand (stacklazy.js). That
// client JS can't run here (injected scripts don't execute; its fetch would miss the
// in-browser app), so we replicate JUST that behaviour: when a stack row opens, fetch its
// body through the bridge and inject it. Forms inside then work via the submit delegate.
function wireLazyBodies(container) {
  container.querySelectorAll("details.stackrow").forEach((det) => {
    const lazy = det.querySelector(":scope > .lazy-body[data-body-url]");
    if (!lazy) return;
    det.addEventListener("toggle", () => {
      if (!det.open || lazy.dataset.loaded) return;
      lazy.dataset.loaded = "1";
      try {
        const res = JSON.parse(bridge.handle("GET", lazy.getAttribute("data-body-url"), ""));
        const doc = new DOMParser().parseFromString(res.body, "text/html");
        lazy.innerHTML = doc.body.innerHTML;
      } catch (e) { lazy.textContent = "failed to load: " + e; }
    });
  });
}

// --- live telemetry ---------------------------------------------------------------------
// The real dashboard's client pollers (dash.js/system.js) can't run here (see above), so the
// RX-TX Monitor's per-second cells and the whole System box would sit blank. This ONE timer
// replicates their DOM updates — values, meter, gauge bars, per-core CPU bars and the rolling
// sparklines — against the in-browser bridge: it reads the CURRENT DOM by id every tick (so it
// survives navigation with no re-wiring), never reloads, and can never throw into the page.
const HIST = 60;                                       // sparkline history depth (matches system.js)
const H = { cpu: [], mem: [], swap: [], disk: [], disk2: [], temp: [], rx: [], tx: [] };
let prevCpu = null, prevNet = null, prevCores = null;
function txt(id, v) { const el = $(id); if (el && v != null && v !== "") el.textContent = v; }
function fmtBytes(b) {
  if (typeof b !== "number" || !isFinite(b)) return "?";
  const u = ["B", "kB", "MB", "GB", "TB"]; let i = 0;
  while (b >= 1000 && i < u.length - 1) { b /= 1000; i++; }
  return (i === 0 ? Math.round(b) : b >= 100 ? b.toFixed(0) : b.toFixed(1)) + " " + u[i];
}
function setBar(id, pct) { const el = $(id); if (el) el.style.width = Math.max(0, Math.min(100, pct)) + "%"; }
function show(id) { const el = $(id); if (el) el.hidden = false; }
function pushHist(a, v) { a.push(v); if (a.length > HIST) a.shift(); }
// Replicates system.js drawSpark: newest sample at the LEFT edge, aging rightward, mapped into
// the SVG's 0..60 × 0..20 viewBox on a fixed [lo,hi] scale.
function drawSpark(svgId, arr, lo, hi) {
  const svg = $(svgId); if (!svg) return;
  const line = svg.querySelector("polyline"); if (!line) return;
  const step = 60 / (HIST - 1), pts = [];
  for (let i = 0; i < arr.length; i++) {
    let y = 20 - 20 * ((arr[i] - lo) / ((hi - lo) || 1));
    y = Math.max(0, Math.min(20, y));
    pts.push(((arr.length - 1 - i) * step).toFixed(1) + "," + y.toFixed(1));
  }
  line.setAttribute("points", pts.join(" "));
}
function coreLoads(percore) {
  return (percore || []).map((row) => {
    let s = 0; for (const x of row) s += x; return [s, row[3] + (row[4] || 0)];
  });
}
function drawCoreBars(cores) {
  const grid = $("sys-cpu-cores"); if (!grid || !cores.length) return;
  if (grid.childElementCount !== cores.length) {          // (re)build once per core count
    grid.textContent = "";
    for (let b = 0; b < cores.length; b++) {
      const bar = document.createElement("span"); bar.className = "sysbar";
      const fill = document.createElement("span"); fill.className = "sysbar-fill";
      bar.appendChild(fill); grid.appendChild(bar);
    }
  }
  if (prevCores && prevCores.length === cores.length) {
    for (let ci = 0; ci < cores.length; ci++) {
      const ds = cores[ci][0] - prevCores[ci][0], di = cores[ci][1] - prevCores[ci][1];
      if (ds > 0 && di >= 0) grid.children[ci].firstChild.style.width =
        Math.max(0, Math.min(100, 100 * (1 - di / ds))) + "%";
    }
  }
  prevCores = cores;
}
// bridge.handle returns the transport envelope {status, ctype, body}; body is the
// endpoint's JSON payload as a string, so parse it a second time to get the data.
function ask(path) {
  try {
    const env = JSON.parse(bridge.handle("GET", path, ""));
    return env && env.status === 200 ? JSON.parse(env.body) : null;
  } catch (_) { return null; }
}
function liveDaemon() {
  document.querySelectorAll("[data-radio-band]").forEach((col) => {
    const b = col.getAttribute("data-radio-band");
    const j = ask("/api/daemon/" + encodeURIComponent(b));
    if (!j || !j.reachable) return;                 // offline panel: leave as rendered
    const st = j.status || {}, sta = j.stats || {}, ch = j.channel || {};
    txt("rd-rssiv-" + b, ch.LIVERSSI); txt("rd-radio-" + b, st.RADIO);
    txt("rd-txmode-" + b, st.TXMODE); txt("rd-cad-" + b, ch.CADSTATE);
    txt("rd-tx-" + b, st.TX); txt("rd-pktrssi-" + b, ch.PACKETRSSI);
    txt("rd-cadrssi-" + b, st.CADRSSI); txt("rd-cadwait-" + b, st.CADWAIT);
    txt("rd-rx-" + b, sta.RX); txt("rd-txok-" + b, sta.TXOK); txt("rd-txerr-" + b, sta.TXERR);
    if (sta.UPTIME != null) txt("rd-uptime-" + b, sta.UPTIME + " s");
    const m = $("rd-rssi-" + b); if (m && ch.LIVERSSI) m.value = parseFloat(ch.LIVERSSI);
    const feed = $("rd-feed-" + b); if (feed && Array.isArray(j.feed)) feed.textContent = j.feed.join("\n");
  });
}
function liveSystem() {
  if (!$("sys-cpu-val") && !$("sys-mem-val")) return;   // System box not on this page
  const s = ask("/api/system"); if (!s) return;
  // CPU: % busy from jiffy deltas (needs two samples) + per-core bars + rolling spark.
  if (s.cpu && Array.isArray(s.cpu.total)) {
    const t = s.cpu.total, sum = t.reduce((a, b) => a + b, 0), idle = t[3] + (t[4] || 0);
    if (prevCpu && sum > prevCpu.sum) {
      const ds = sum - prevCpu.sum, di = idle - prevCpu.idle;
      if (ds > 0) {
        const cpu = Math.max(0, Math.min(100, 100 * (ds - di) / ds));
        txt("sys-cpu-val", Math.round(cpu) + "%");
        pushHist(H.cpu, cpu); drawSpark("sys-cpu-spark", H.cpu, 0, 100);
      }
    }
    prevCpu = { sum, idle };
    drawCoreBars(coreLoads(s.cpu.percore));             // per-core mini bars (uses its own prev)
  }
  // Memory + swap: used/total, gauge bar, spark.
  if (s.mem && s.mem.total_kb > 0) {
    const used = s.mem.total_kb - s.mem.available_kb, mpct = 100 * used / s.mem.total_kb;
    txt("sys-mem-val", fmtBytes(used * 1024) + " / " + fmtBytes(s.mem.total_kb * 1024));
    setBar("sys-mem-bar", mpct); pushHist(H.mem, mpct); drawSpark("sys-mem-spark", H.mem, 0, 100);
    if (s.mem.swap_total_kb > 0) {
      show("sys-swap-row");
      const su = s.mem.swap_total_kb - s.mem.swap_free_kb, spct = 100 * su / s.mem.swap_total_kb;
      txt("sys-swap-val", fmtBytes(su * 1024) + " / " + fmtBytes(s.mem.swap_total_kb * 1024));
      setBar("sys-swap-bar", spct); pushHist(H.swap, spct); drawSpark("sys-swap-spark", H.swap, 0, 100);
    }
  }
  // Net: B/s from byte deltas, two stacked graphs on a shared rolling-max scale.
  if (s.net && typeof s.net.rx_bytes === "number") {
    if (prevNet && s.ts > prevNet.ts) {
      const dt = s.ts - prevNet.ts;
      const rx = (s.net.rx_bytes - prevNet.rx) / dt, tx = (s.net.tx_bytes - prevNet.tx) / dt;
      if (rx >= 0 && tx >= 0) {
        txt("sys-net-val", fmtBytes(rx) + "/s ↓  " + fmtBytes(tx) + "/s ↑");
        pushHist(H.rx, rx); pushHist(H.tx, tx);
        let peak = 1000;
        for (const v of H.rx) if (v > peak) peak = v;
        for (const v of H.tx) if (v > peak) peak = v;
        drawSpark("sys-netrx-spark", H.rx, 0, peak); drawSpark("sys-nettx-spark", H.tx, 0, peak);
      }
    }
    prevNet = { rx: s.net.rx_bytes, tx: s.net.tx_bytes, ts: s.ts };
  }
  // Disk(s): used% bar + spark; the runtime row appears only when the server sends it.
  if (s.disk && s.disk.root && s.disk.root.total_b > 0) {
    const r = s.disk.root, rused = 100 * (1 - r.free_b / r.total_b);
    txt("sys-disk-val", fmtBytes(r.total_b - r.free_b) + " / " + fmtBytes(r.total_b));
    setBar("sys-disk-bar", rused); pushHist(H.disk, rused); drawSpark("sys-disk-spark", H.disk, 0, 100);
    if (s.disk.runtime && s.disk.runtime.total_b > 0) {
      show("sys-disk2-row");
      const q = s.disk.runtime, qused = 100 * (1 - q.free_b / q.total_b);
      txt("sys-disk2-val", fmtBytes(q.total_b - q.free_b) + " / " + fmtBytes(q.total_b));
      setBar("sys-disk2-bar", qused); pushHist(H.disk2, qused); drawSpark("sys-disk2-spark", H.disk2, 0, 100);
    }
  }
  // Temp: fixed 0–90 °C scale.
  if (typeof s.temp_mc === "number") {
    const c = s.temp_mc / 1000;
    txt("sys-temp-val", c.toFixed(1) + " °C");
    setBar("sys-temp-bar", 100 * c / 90); pushHist(H.temp, c); drawSpark("sys-temp-spark", H.temp, 0, 90);
  }
  if (s.time && s.time.local) {
    show("sys-time-row");
    txt("sys-time-val", s.time.local + (s.time.tz ? " " + s.time.tz : ""));
    txt("sys-time-utc", s.time.utc ? s.time.utc + " UTC" : "");
  }
  if (s.info) {
    const p = [s.info.hostname, s.info.model, s.info.os,
               (s.info.kernel || "") + " " + (s.info.arch || "")].filter((x) => x && x.trim());
    if (typeof s.uptime_s === "number") {
      const u = s.uptime_s, h = Math.floor(u / 3600), mm = Math.floor((u % 3600) / 60);
      p.push("up " + h + "h " + mm + "m");
    }
    txt("sys-info", p.join(" · "));
  }
}
function liveTick() { if (!bridge) return; try { liveDaemon(); liveSystem(); } catch (_) { /* never break the page */ } }

async function go(method, path, formData) {
  const formJson = formData ? JSON.stringify(Object.fromEntries(formData.entries())) : "";
  const res = JSON.parse(bridge.handle(method, path, formJson));
  if ((res.ctype || "").includes("html")) {
    const doc = new DOMParser().parseFromString(res.body, "text/html");
    $("app").innerHTML = doc.body.innerHTML;
    wireLazyBodies($("app"));
  } else {
    $("app").textContent = res.body;
  }
  persist();
  window.scrollTo(0, 0);
  liveTick();                    // fill live cells immediately after a navigation/render
}

// Internal = a same-document app path. Reject ANY scheme (http:, javascript:, tel:, data:,
// mailto:…), protocol-relative //host, and in-page #anchors; let the browser handle those.
const isInternal = (href) =>
  href && !/^[a-z][a-z0-9+.\-]*:/i.test(href) && !href.startsWith("//") && !href.startsWith("#");

document.addEventListener("click", (e) => {
  if (e.target.closest && e.target.closest("#resetbtn")) {
    e.preventDefault();
    if (!bridge) return;
    bridge.reset_state();
    localStorage.removeItem("lhpc_demo_state");
    go("GET", "/");
    return;
  }
  const a = e.target.closest && e.target.closest("a[href]");
  if (a) {
    const href = a.getAttribute("href");
    if (isInternal(href)) { e.preventDefault(); go("GET", href); }
  }
});

document.addEventListener("submit", (e) => {
  const f = e.target;
  if (!(f instanceof HTMLFormElement)) return;
  e.preventDefault();
  const fd = new FormData(f);
  if (e.submitter && e.submitter.name) fd.append(e.submitter.name, e.submitter.value);
  const method = (f.getAttribute("method") || "GET").toUpperCase();
  const action = f.getAttribute("action") || location.pathname;
  go(method, action, fd);
});

main();
