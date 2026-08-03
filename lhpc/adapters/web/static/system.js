// System box: live host metrics, polled ONLY while the box is expanded AND the tab is visible —
// a collapsed box costs the host nothing (the requirement, not an optimization). The server
// returns RAW counters + a monotonic ts; rates are computed HERE between our own polls, so the
// server stays stateless. Display-only; same-origin; DOM writes via textContent / classList /
// CSSOM / SVG attributes (never innerHTML). The bar widths use element.style.width — a
// deliberate exception to the class-preference convention: CSP-legal (no style= attribute in
// server HTML), and a stepped class ladder would visibly jump.
(function () {
  "use strict";
  var box = document.getElementById("sysbox");
  if (!box) return;
  var POLL_MS = 2000, HIST = 60;
  var timer = null, clockTimer = null;
  var inflightCtl = null;   // AbortController of the ACTIVE request — the ownership token:
                            // only the request holding it may clear the slot, so a late/aborted
                            // response can never unlock polling for a newer generation.
  var prev = null;                                  // last sample for rate deltas
  var hist = { cpu: [], rx: [], tx: [], temp: [], mem: [], disk: [], disk2: [], swap: [] }; // client-only sparkline history

  // --- formatting (numeric zero is VALID data — guard with checks, never truthiness) ----------
  function num(v) { return typeof v === "number" && isFinite(v); }
  function fmtBytes(b) {
    if (!num(b)) return "?";
    var units = ["B", "kB", "MB", "GB", "TB"], i = 0;
    while (b >= 1000 && i < units.length - 1) { b /= 1000; i++; }
    // Stable widths: B as integer (a rate can be a long float), one decimal below 100,
    // none above — "999.5 kB" never grows a digit storm.
    var v = i === 0 ? String(Math.round(b)) : (b >= 100 ? b.toFixed(0) : b.toFixed(1));
    return v + " " + units[i];
  }
  function fmtRate(b) { return fmtBytes(b) + "/s"; }
  function fmtUptime(s) {
    if (!num(s)) return "?";
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600),
        m = Math.floor((s % 3600) / 60);
    return (d ? d + "d " : "") + h + "h " + m + "m";
  }
  // --- local clock tick --------------------------------------------------------------------
  // The server's offset from THIS browser's clock, plus the zone offset the server reported, so
  // the row can advance both lines itself between polls.
  var clock = { skew: null, offset_s: 0, tz: "" };

  function tzOffsetSeconds(local, utc) {
    // Both strings describe the SAME instant; their difference is the zone offset, so the
    // browser never has to know anything about the zone itself.
    var l = Date.parse(local.replace(" ", "T") + "Z"), u = Date.parse(utc.replace(" ", "T") + "Z");
    return (isFinite(l) && isFinite(u)) ? Math.round((l - u) / 1000) : 0;
  }

  function stamp(ms) {
    // Formats in UTC deliberately: the caller pre-applies the zone offset, so the browser's own
    // timezone never leaks into a reading the server is responsible for.
    var d = new Date(ms);
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return d.getUTCFullYear() + "-" + p(d.getUTCMonth() + 1) + "-" + p(d.getUTCDate()) + " " +
           p(d.getUTCHours()) + ":" + p(d.getUTCMinutes()) + ":" + p(d.getUTCSeconds());
  }

  function tickClock() {
    if (clock.skew === null) return;                  // nothing anchored yet
    var utcMs = Date.now() + clock.skew;
    set("sys-time-val", stamp(utcMs + clock.offset_s * 1000) + (clock.tz ? " " + clock.tz : ""));
    set("sys-time-utc", stamp(utcMs) + " UTC");
  }

  function set(id, text) {
    var el = document.getElementById(id);
    if (el && el.textContent !== String(text)) el.textContent = text;
  }
  function setBar(id, pct) {
    var el = document.getElementById(id);
    if (el && num(pct)) el.style.width = Math.max(0, Math.min(100, pct)) + "%";
  }
  function setLevel(rowEl, level) {                 // level: "", "sys-warn", "sys-crit"
    if (!rowEl) return;
    rowEl.classList.remove("sys-warn", "sys-crit");
    if (level) rowEl.classList.add(level);
  }
  function pushHist(arr, v) { arr.push(v); if (arr.length > HIST) arr.shift(); }
  function drawSpark(svgId, series, lo, hi) {
    // Fixed scales (CPU/Mem/Disk 0..100 %, Temp 0..90 °C); only Net passes a rolling max as `hi`.
    var svg = document.getElementById(svgId);
    if (!svg) return;
    var lines = svg.querySelectorAll("polyline");
    for (var li = 0; li < series.length && li < lines.length; li++) {
      var arr = series[li], pts = [], step = 60 / (HIST - 1);
      for (var i = 0; i < arr.length; i++) {
        var y = 20 - 20 * ((arr[i] - lo) / ((hi - lo) || 1));
        // NEWEST AT THE LEFT: fresh samples enter at the left edge and age rightward
        // (operator's preferred running direction; oldest drops off the right).
        pts.push(((arr.length - 1 - i) * step).toFixed(1) + "," + Math.max(0, Math.min(20, y)).toFixed(1));
      }
      lines[li].setAttribute("points", pts.join(" "));
    }
  }
  function rowOf(id) { var el = document.getElementById(id); return el ? el.closest("tr") : null; }
  function hideOptionalRows() {
    // The source-dependent rows are hidden UNLESS the current sample proves them:
    // called before every apply() and on every fresh baseline, so an omitted source (or a
    // reopen) can never leave last session's values on screen.
    ["sys-swap-row", "sys-disk2-row", "sys-power-row", "sys-time-row"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.hidden = true;
    });
  }
  function coreLoads(percore) {
    if (!percore) return [];
    return percore.map(function (row) {
      var s = 0;
      for (var i = 0; i < row.length; i++) s += row[i];
      return [s, row[3] + row[4]];
    });
  }
  function drawCoreBars(cores, prevCores) {
    var grid = document.getElementById("sys-cpu-cores");
    if (!grid || !cores.length) return;
    if (grid.childElementCount !== cores.length) {  // (re)build once per core count
      while (grid.firstChild) grid.removeChild(grid.firstChild);
      for (var b = 0; b < cores.length; b++) {
        var bar = document.createElement("span");
        bar.className = "sysbar";
        var fill = document.createElement("span");
        fill.className = "sysbar-fill";
        bar.appendChild(fill);
        grid.appendChild(bar);
      }
    }
    if (!prevCores || prevCores.length !== cores.length) return;
    for (var ci = 0; ci < cores.length; ci++) {
      var ds = cores[ci][0] - prevCores[ci][0], di = cores[ci][1] - prevCores[ci][1];
      if (ds > 0 && di >= 0) {
        var cp = Math.max(0, Math.min(100, 100 * (1 - di / ds)));
        var barEl = grid.children[ci];
        barEl.firstChild.style.width = cp + "%";
        barEl.classList.remove("sys-warn", "sys-crit");     // normal <70, high ≥70, full ≥90
        if (cp >= 90) barEl.classList.add("sys-crit");
        else if (cp >= 70) barEl.classList.add("sys-warn");
      }
    }
  }
  function setNetVal(rxText, txText) {
    var el = document.getElementById("sys-net-val");
    if (!el) return;
    var key = rxText + "|" + txText;
    if (el.dataset.txt === key) return;
    el.dataset.txt = key;
    while (el.firstChild) el.removeChild(el.firstChild);
    [["↓", rxText], ["↑", txText]].forEach(function (pair) {
      var line = document.createElement("span");
      line.className = "sys-netline";
      var arrow = document.createElement("span");
      arrow.textContent = pair[0];
      var rate = document.createElement("span");
      rate.textContent = pair[1];
      line.appendChild(arrow); line.appendChild(rate);
      el.appendChild(line);
    });
  }

  // --- apply one sample -----------------------------------------------------------------------
  function apply(d) {
    hideOptionalRows();                             // reveal below ONLY from valid current data
    // CPU: % busy from jiffy deltas; needs two samples (shows … until the second tick).
    if (d.cpu && d.cpu.total) {
      var t = d.cpu.total, sum = 0, i;
      for (i = 0; i < t.length; i++) sum += t[i];
      var idle = t[3] + t[4];
      var cores = coreLoads(d.cpu.percore);        // [[sum, idle] per core] for the delta
      if (prev && prev.cpu) {
        var dSum = sum - prev.cpu.sum, dIdle = idle - prev.cpu.idle;
        if (dSum > 0 && dIdle >= 0) {               // negative delta (reboot/wrap) -> skip sample
          var pct = 100 * (1 - dIdle / dSum);       // number + graph stay TOTAL-%
          set("sys-cpu-val", pct.toFixed(0) + " %");
          pushHist(hist.cpu, pct);
          drawSpark("sys-cpu-spark", [hist.cpu], 0, 100);
        }
        drawCoreBars(cores, prev.cpu.cores);        // per-core mini bars (2x2)
      }
      prev = prev || {};
      prev.cpu = { sum: sum, idle: idle, cores: cores };
    }
    // Memory (+ swap folded into the text as used/total, only when swap exists).
    if (d.mem && num(d.mem.total_kb) && d.mem.total_kb > 0) {
      var usedKb = d.mem.total_kb - d.mem.available_kb;
      var mpct = 100 * usedKb / d.mem.total_kb;
      setBar("sys-mem-bar", mpct);
      set("sys-mem-val", fmtBytes(usedKb * 1024) + " / " + fmtBytes(d.mem.total_kb * 1024));
      setLevel(rowOf("sys-mem-bar"), mpct >= 92 ? "sys-crit" : (mpct >= 80 ? "sys-warn" : ""));
      pushHist(hist.mem, mpct);
      drawSpark("sys-mem-spark", [hist.mem], 0, 100);
      // Swap: own row — shown ONLY while the current sample has swap; anything else hides it
      // again (unknown stays unknown, never a stale row).
      var srow = document.getElementById("sys-swap-row");
      if (srow && num(d.mem.swap_total_kb) && d.mem.swap_total_kb > 0) {
        srow.hidden = false;
        var sUsedKb = d.mem.swap_total_kb - d.mem.swap_free_kb;
        var spct = 100 * sUsedKb / d.mem.swap_total_kb;
        setBar("sys-swap-bar", spct);
        set("sys-swap-val", fmtBytes(sUsedKb * 1024) + " / " + fmtBytes(d.mem.swap_total_kb * 1024));
        setLevel(srow, spct >= 95 ? "sys-crit" : (spct >= 80 ? "sys-warn" : ""));
        pushHist(hist.swap, spct);
        drawSpark("sys-swap-spark", [hist.swap], 0, 100);
      }
    }
    // Net: B/s from byte deltas; rolling-max scale, RX solid / TX lighter (opacity).
    if (d.net && num(d.net.rx_bytes)) {
      if (prev && prev.net) {
        // dt comes from the NET baseline's own timestamp: prev.ts advances on every response,
        // but the counters only when a net sample is present — an omitted-net response in
        // between would otherwise divide a two-interval byte delta by one interval (2x rate).
        var dt = d.ts - prev.net.ts;
        var dRx = d.net.rx_bytes - prev.net.rx, dTx = d.net.tx_bytes - prev.net.tx;
        if (dt > 0 && dRx >= 0 && dTx >= 0) {
          var rx = dRx / dt, tx = dTx / dt;
          // Two fixed flex lines (arrow left, rate right): constant height, right-aligned
          // numbers, arrows that never wander with the digit width.
          setNetVal(fmtRate(rx), fmtRate(tx));
          pushHist(hist.rx, rx); pushHist(hist.tx, tx);
          // Two stacked graphs (RX above, TX below) on ONE shared rolling-max scale — with
          // independent scales, tiny idle noise drew as tall as a real transfer and the
          // graphs contradicted the numbers. The 1 kB/s floor keeps idle noise low instead
          // of full-height.
          var peak = 1000, k;
          for (k = 0; k < hist.rx.length; k++) if (hist.rx[k] > peak) peak = hist.rx[k];
          for (k = 0; k < hist.tx.length; k++) if (hist.tx[k] > peak) peak = hist.tx[k];
          drawSpark("sys-netrx-spark", [hist.rx], 0, peak);
          drawSpark("sys-nettx-spark", [hist.tx], 0, peak);
        }
      }
      prev = prev || {};
      prev.net = { rx: d.net.rx_bytes, tx: d.net.tx_bytes, ts: d.ts };
    }
    // Disk(s): used% bars; the optional runtime row appears only when the server sends it.
    if (d.disk && d.disk.root && num(d.disk.root.total_b) && d.disk.root.total_b > 0) {
      var r = d.disk.root, rused = 100 * (1 - r.free_b / r.total_b);
      setBar("sys-disk-bar", rused);
      set("sys-disk-val", fmtBytes(r.total_b - r.free_b) + " / " + fmtBytes(r.total_b));
      setLevel(rowOf("sys-disk-bar"), rused >= 90 ? "sys-crit" : (rused >= 80 ? "sys-warn" : ""));
      pushHist(hist.disk, rused);
      drawSpark("sys-disk-spark", [hist.disk], 0, 100);
    }
    var row2 = document.getElementById("sys-disk2-row");
    if (row2 && d.disk && d.disk.runtime && num(d.disk.runtime.total_b)) {
      row2.hidden = false;
      var q = d.disk.runtime, qused = 100 * (1 - q.free_b / q.total_b);
      setBar("sys-disk2-bar", qused);
      set("sys-disk2-val", fmtBytes(q.total_b - q.free_b) + " / " + fmtBytes(q.total_b));
      setLevel(row2, qused >= 90 ? "sys-crit" : (qused >= 80 ? "sys-warn" : ""));
      pushHist(hist.disk2, qused);
      drawSpark("sys-disk2-spark", [hist.disk2], 0, 100);
    }
    // Temp: fixed 0–90 °C scale.
    if (num(d.temp_mc)) {
      var c = d.temp_mc / 1000;
      setBar("sys-temp-bar", 100 * c / 90);
      set("sys-temp-val", c.toFixed(1) + " °C");
      setLevel(rowOf("sys-temp-bar"), c >= 80 ? "sys-crit" : (c >= 70 ? "sys-warn" : ""));
      pushHist(hist.temp, c);
      drawSpark("sys-temp-spark", [hist.temp], 0, 90);
    }
    // Power: TRUTHFUL per source. hwmon-alarm sees ONLY the sticky under-voltage alarm —
    // it can never say "OK" (it is blind to throttling), and clear reads as the neutral
    // "no under-voltage alarm". A future comprehensive source declares a different `source`.
    var prow = document.getElementById("sys-power-row");
    var pill = document.getElementById("sys-power-pill");
    if (prow && pill && d.power && d.power.source === "hwmon-alarm") {
      prow.hidden = false;
      var bad = d.power.undervolt_alarm === true;
      pill.textContent = bad ? "UNDER-VOLTAGE alarm" : "no under-voltage alarm";
      pill.className = "pill " + (bad ? "pill-bad" : "");
      set("sys-power-val", num(d.power.core_mv)
          ? "core " + (d.power.core_mv / 1000).toFixed(3) + " V" : "");
    }
    // Time: LHPC only REPORTS the clock — it never sets, steps or disciplines it, and the hint
    // below is text the operator runs themselves. The pin is the SYNC STATE; no string claims the
    // time is CORRECT, because nothing here can prove that without an external reference.
    // `unknown` is its own state, never folded into bad. Yellow is a legitimate steady state —
    // a daemon that has not synced yet, or a clock restored from an RTC — so it is worded as
    // unverified rather than as a fault; no source at all is red, not yellow.
    var trow = document.getElementById("sys-time-row");
    var tpill = document.getElementById("sys-time-pill");
    if (trow && tpill && d.time && d.time.local) {
      trow.hidden = false;
      var tst = d.time.state;
      // Label comes from the backend, which distinguishes the red cases (no source vs a clock
      // that reads earlier than files this box wrote); the fallback never invents a reason.
      tpill.textContent = d.time.label || (tst === "green" ? "synced"
        : tst === "yellow" ? "unverified"
        : tst === "red" ? "no time source" : "unknown");
      tpill.className = "pill " + (tst === "green" ? "pill-ok"
        : tst === "yellow" ? "pill-warn"
        : tst === "red" ? "pill-bad" : "");
      // TWO deliberate lines: local (with the zone that reading is actually in) above UTC.
      // The zone qualifies the LOCAL time, not the UTC one.
      //
      // Each poll re-anchors the offset between the server's clock and this browser's; a 1 Hz
      // local timer moves the seconds in between, so the display cannot drift and costs nothing.
      if (num(d.time.epoch) && d.time.utc) {
        clock.skew = d.time.epoch * 1000 - Date.now();
        clock.offset_s = tzOffsetSeconds(d.time.local, d.time.utc);
        clock.tz = d.time.tz || "";
        tickClock();
      } else {
        clock.skew = null;                            // no anchor -> show exactly what was sent
        set("sys-time-val", d.time.local + (d.time.tz ? " " + d.time.tz : ""));
        set("sys-time-utc", d.time.utc ? d.time.utc + " UTC" : "");
      }
      // Guidance and command are REAL text in the row when the state is not green — a title
      // tooltip is neither copyable in the normal way nor reachable on a touch device. They are
      // SEPARATE elements so select-all on the command copies exactly a runnable line: the old
      // single string ended "(or install chrony)" and pasted into a shell as a syntax error.
      // The conflict case deliberately offers guidance and NO command — which daemon to disable
      // is the operator's call, and "enable NTP" would be wrong advice there.
      var thint = document.getElementById("sys-time-hint");
      if (thint) {
        thint.textContent = d.time.hint || "";
        thint.hidden = !d.time.hint;
      }
      var tcmd = document.getElementById("sys-time-cmd");
      if (tcmd) {
        tcmd.textContent = d.time.hint_cmd || "";
        tcmd.hidden = !d.time.hint_cmd;
      }
      // The conflict detail must be visible, not tooltip-only: a touch device has no hover.
      if (thint && tst !== "green" && d.time.detail && d.time.label === "conflict") {
        thint.textContent = d.time.detail + " — " + (d.time.hint || "");
        thint.hidden = false;
      }
      // Tooltip keeps the supporting detail: source, age, estimated error, RTC.
      var tbits = [];
      if (d.time.source) tbits.push("source: " + d.time.source);
      if (d.time.detail) tbits.push(d.time.detail);
      if (num(d.time.synced_age_s)) tbits.push("synced " + fmtUptime(d.time.synced_age_s) + " ago");
      if (num(d.time.maxerror_us)) {
        tbits.push("est. error \u00b1" + (d.time.maxerror_us / 1e6).toFixed(1) + " s");
      }
      // Only claim to know about the RTC when the backend actually reported it.
      if (typeof d.time.rtc_present === "boolean") {
        tbits.push("RTC: " + (d.time.rtc_present ? "yes" : "no"));
      }
      trow.title = tbits.join("\n");
    }
    // Info footer (static-ish; cheap to refresh each tick for load/uptime).
    if (d.info) {
      // ONE flowing line that only ever wraps BETWEEN values: each value is its own nowrap
      // <span> (built via createElement/textContent — never innerHTML), separators are plain
      // text nodes the browser may break after.
      var parts = [];
      if (d.info.hostname) parts.push(d.info.hostname);
      if (d.info.model) parts.push(d.info.model);
      if (d.info.os) parts.push(d.info.os);
      if (d.info.kernel) parts.push(d.info.kernel + " " + (d.info.arch || ""));
      if (d.cpu && num(d.cpu.cores) && d.cpu.cores > 0) parts.push(d.cpu.cores + " cores");
      if (num(d.uptime_s)) parts.push("up " + fmtUptime(d.uptime_s));
      var el = document.getElementById("sys-info");
      var txt = parts.join(" · ");
      if (el && el.dataset.txt !== txt) {        // rebuild only on change (no flicker)
        el.dataset.txt = txt;
        while (el.firstChild) el.removeChild(el.firstChild);
        for (var pi = 0; pi < parts.length; pi++) {
          if (pi) el.appendChild(document.createTextNode(" · "));
          var span = document.createElement("span");
          span.textContent = parts[pi];
          el.appendChild(span);
        }
      }
    }
  }

  // --- polling lifecycle ----------------------------------------------------------------------
  // One request at a time: `inflightCtl` is both the overlap guard and the ownership token.
  // stop() ABORTS the active request (never just forgets it); a completing request touches the
  // slot only while it still owns it, so overlapping generations are impossible by construction.
  function poll() {
    if (document.hidden || inflightCtl) return;
    var ctl = new AbortController();
    inflightCtl = ctl;
    fetch("/api/system", { cache: "no-store", signal: ctl.signal })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (inflightCtl !== ctl) return;            // no longer the owner: a stop() intervened
        inflightCtl = null;
        if (!d) return;
        apply(d);                                   // reads prev (rates), records new baselines
        prev = prev || {};
        prev.ts = d.ts;
      })
      .catch(function () { if (inflightCtl === ctl) inflightCtl = null; });
  }
  function resetDynamic() {
    hideOptionalRows();
    // Fresh baseline must LOOK fresh: placeholders + empty graphs + neutral core bars — a
    // reopened box must never keep showing the last session's values.
    set("sys-cpu-val", "…");
    var nv = document.getElementById("sys-net-val");
    if (nv) { while (nv.firstChild) nv.removeChild(nv.firstChild); nv.textContent = "…"; delete nv.dataset.txt; }
    var svgs = document.querySelectorAll("#sysbox polyline");
    for (var i = 0; i < svgs.length; i++) svgs[i].setAttribute("points", "");
    var grid = document.getElementById("sys-cpu-cores");
    if (grid) {
      for (var b = 0; b < grid.children.length; b++) {
        grid.children[b].classList.remove("sys-warn", "sys-crit");
        grid.children[b].firstChild.style.width = "0%";
      }
    }
  }
  // --- history persistence (STAGE 1: client-side only, zero cost for the Pi) -----------------
  // Graph SHAPES survive reload/collapse via the browser's localStorage; the rate BASELINES
  // (prev counters) deliberately do not — across a gap the counters may have reset, so values
  // still start at … and only the drawn history carries over. Saved state older than the age
  // cap is discarded: yesterday's curve on a fresh box would be a lie.
  var HKEY = "lhpc:sysbox:hist", HIST_MAX_AGE_MS = 10 * 60 * 1000;
  function saveHist() {
    try {
      localStorage.setItem(HKEY, JSON.stringify({ v: 1, at: Date.now(), hist: hist }));
    } catch (e) { /* private mode / quota */ }
  }
  function restoreHist() {
    try {
      var raw = localStorage.getItem(HKEY);
      if (!raw) return;
      var d = JSON.parse(raw);
      if (!d || d.v !== 1 || !d.hist || typeof d.at !== "number"
          || Date.now() - d.at > HIST_MAX_AGE_MS) return;
      Object.keys(hist).forEach(function (k) {
        if (Array.isArray(d.hist[k])) {
          hist[k] = d.hist[k].filter(function (x) {
            return typeof x === "number" && isFinite(x);
          }).slice(-HIST);
        }
      });
      redrawFromHist();
    } catch (e) { /* corrupt state: ignore, next save overwrites */ }
  }
  function redrawFromHist() {
    drawSpark("sys-cpu-spark", [hist.cpu], 0, 100);
    drawSpark("sys-mem-spark", [hist.mem], 0, 100);
    drawSpark("sys-swap-spark", [hist.swap], 0, 100);
    drawSpark("sys-disk-spark", [hist.disk], 0, 100);
    drawSpark("sys-disk2-spark", [hist.disk2], 0, 100);
    drawSpark("sys-temp-spark", [hist.temp], 0, 90);
    var peak = 1000, k;
    for (k = 0; k < hist.rx.length; k++) if (hist.rx[k] > peak) peak = hist.rx[k];
    for (k = 0; k < hist.tx.length; k++) if (hist.tx[k] > peak) peak = hist.tx[k];
    drawSpark("sys-netrx-spark", [hist.rx], 0, peak);
    drawSpark("sys-nettx-spark", [hist.tx], 0, peak);
  }
  function start() {
    if (timer !== null) return;
    prev = null;                                    // fresh baseline: rates show … until tick 2
    hist = { cpu: [], rx: [], tx: [], temp: [], mem: [], disk: [], disk2: [], swap: [] };
    resetDynamic();
    restoreHist();                                  // graph shapes only; values stay …
    poll();
    timer = setInterval(poll, POLL_MS);
    // 1 Hz DISPLAY tick — no request and no server work. Polling every second just to move a
    // digit would have doubled the endpoint's cost (2.3% of a core on a Zero 2W, measured) for
    // something cosmetic, and a slow response would still make it stutter.
    clockTimer = setInterval(tickClock, 1000);
  }
  function stop() {
    if (timer === null) return;
    clearInterval(timer);
    timer = null;
    if (clockTimer !== null) { clearInterval(clockTimer); clockTimer = null; }
    clock.skew = null;                              // a reopen re-anchors on a fresh sample
    if (inflightCtl) { inflightCtl.abort(); inflightCtl = null; }
    saveHist();                                     // BEFORE the wipe: collapse must not lose it
    prev = null;
    hist = { cpu: [], rx: [], tx: [], temp: [], mem: [], disk: [], disk2: [], swap: [] };
  }
  if (typeof window !== "undefined") {
    window.addEventListener("pagehide", function () {   // reload/navigate (incl. dash.js's
      if (timer !== null) saveHist();                   // signature reload) keeps the curves
    });
  }
  var PERSIST = "lhpc:sysbox";
  try {   // remembered open state (manual reload/navigation): server renders collapsed, we reopen
    if (localStorage.getItem(PERSIST) === "1" && !box.open) box.open = true;
  } catch (e) { /* private mode */ }
  box.addEventListener("toggle", function () {
    try { localStorage.setItem(PERSIST, box.open ? "1" : "0"); } catch (e) { /* private mode */ }
    box.open ? start() : stop();
  });
  document.addEventListener("visibilitychange", function () {
    if (!box.open) return;
    if (document.hidden) stop(); else start();      // visible again: fresh baseline
  });
  if (box.open) start();   // belt-and-braces: the id-keyed restore may have opened it already
  // Test hook (inert in production): lets the node-driven regression harness call the state
  // machine directly — apply/reset with controlled samples, no fetch/timers involved.
  box.lhpcTest = { apply: apply, resetDynamic: resetDynamic, tickClock: tickClock };
})();
