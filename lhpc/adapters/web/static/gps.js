/* GPS Monitor pane: /api/gps while #gps-monitor is open; skyview (inline SVG) and the NMEA pane.
   Polling is scheduled ONLY after the previous request settled (the taskbanner.js pattern), with an
   in-flight guard, so a slow gpsd never stacks requests or pins a server worker. Late responses
   carry a generation number and are discarded when the pane state moved on. */
(function () {
  "use strict";
  var mon = document.getElementById("gps-monitor");
  if (!mon) return;
  var outer = document.getElementById("gps-row");        // the whole GPS panel; closed = no polling
  var MON_MS = 3000, NMEA_MS = 2000;
  var monTimer = null, monInFlight = false, monGen = 0;
  var nmeaTimer = null, nmeaInFlight = false, nmeaGen = 0;
  var last = null;                       // the latest /api/gps result
  var $ = function (id) { return document.getElementById(id); };

  function text(id, v) { var el = $(id); if (el) el.textContent = (v === null || v === undefined || v === "") ? "—" : String(v); }
  function fmtCoord(v, digits) { return (typeof v === "number" && isFinite(v)) ? v.toFixed(digits) : null; }
  function fmtAlt(d) {
    if (typeof d.alt !== "number" || !isFinite(d.alt)) return null;
    var kind = d.alt_kind === "msl" ? "m MSL" : d.alt_kind === "hae" ? "m HAE" : "m (legacy alt)";
    return d.alt.toFixed(1) + " " + kind;
  }
  function paneOpen() { return mon.open && (!outer || outer.open) && !document.hidden; }
  function nmeaOpen() { var w = $("gps-nmea-wrap"); return paneOpen() && w && !w.hidden; }

  function render(d) {
    last = d;
    var state = d.label || d.state || "?";
    if (d.error) state += " (" + d.error + ")";
    text("gps-mon-state", state);
    text("gps-mon-summary", d.label || "");
    var note = $("gps-mon-note");
    if (note) { note.hidden = !d.note; note.textContent = d.note || ""; }
    var showPos = d.lat !== null && d.lat !== undefined && d.lon !== null && d.lon !== undefined;
    text("gps-mon-lat", showPos ? fmtCoord(d.lat, 6) : null);
    text("gps-mon-lon", showPos ? fmtCoord(d.lon, 6) : null);
    text("gps-mon-alt", showPos ? fmtAlt(d) : null);
    text("gps-mon-time", d.time ? (d.time + (d.time_has_date ? "" : " (date unavailable)")) : null);
    var sats = (d.sats_used !== null && d.sats_used !== undefined) || (d.sats_seen !== null && d.sats_seen !== undefined)
      ? ((d.sats_used === null || d.sats_used === undefined) ? "?" : d.sats_used) + " of " +
        ((d.sats_seen === null || d.sats_seen === undefined) ? "n/a" : d.sats_seen) : null;
    text("gps-mon-sats", sats);
    text("gps-mon-device", d.device || (d.devices && d.devices.length ? d.devices.join(", ") : null));
    renderSky(d);
    var nb = $("gps-nmea-btn");
    if (nb) nb.disabled = !d.nmea_ok;
    if (!d.nmea_ok) suppressNmea(d.state === "ambiguous"
                                 ? "several GPS data sources — combined NMEA not shown"
                                 : "no NMEA stream in this state");
    else if (d.source === "nmea" && nmeaOpen()) renderNmeaLines(d.nmea || []);
  }

  function renderSky(d) {
    var svg = $("gps-sky");
    if (!svg) return;
    var sats = (d.satellites || []).filter(function (s) {
      return typeof s.el === "number" && isFinite(s.el) && s.el >= 0 && typeof s.az === "number" && isFinite(s.az);
    });
    var ns = "http://www.w3.org/2000/svg", cx = 160, cy = 160, R = 140;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    function el(name, attrs, txt) {
      var e = document.createElementNS(ns, name);
      Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
      if (txt !== undefined) e.textContent = txt;
      svg.appendChild(e); return e;
    }
    [90, 60, 30, 0].forEach(function (elev) {
      el("circle", {cx: cx, cy: cy, r: R * (1 - elev / 90), fill: "none", stroke: "currentColor", "stroke-opacity": "0.25"});
    });
    el("line", {x1: cx, y1: cy - R, x2: cx, y2: cy + R, stroke: "currentColor", "stroke-opacity": "0.25"});
    el("line", {x1: cx - R, y1: cy, x2: cx + R, y2: cy, stroke: "currentColor", "stroke-opacity": "0.25"});
    el("text", {x: cx, y: cy - R - 4, "text-anchor": "middle", "font-size": "11", fill: "currentColor"}, "N");
    sats.forEach(function (s) {
      var r = R * (1 - s.el / 90), a = (s.az - 90) * Math.PI / 180;
      var x = cx + r * Math.cos(a), y = cy + r * Math.sin(a);
      var ss = (typeof s.ss === "number" && isFinite(s.ss)) ? s.ss : null;
      var radius = ss === null ? 5 : Math.max(3, Math.min(11, 3 + ss / 6));
      el("circle", {cx: x.toFixed(1), cy: y.toFixed(1), r: radius, fill: s.used ? "currentColor" : "none",
                    stroke: "currentColor", "fill-opacity": s.used ? "0.85" : "0", "stroke-opacity": s.used === null ? "0.5" : "1"});
      el("text", {x: (x + radius + 2).toFixed(1), y: (y + 4).toFixed(1), "font-size": "10", fill: "currentColor"},
         (s.talker ? s.talker + " " : "") + s.prn);
    });
    if (!sats.length) el("text", {x: cx, y: cy + 4, "text-anchor": "middle", "font-size": "12", fill: "currentColor"}, "no satellites reported");
  }

  function schedule(ms) {
    if (monTimer !== null) { clearTimeout(monTimer); monTimer = null; }
    if (!paneOpen()) return;
    monTimer = setTimeout(tick, ms);
  }
  function tick() {
    if (monTimer !== null) { clearTimeout(monTimer); monTimer = null; }
    if (!paneOpen() || monInFlight) return;
    monInFlight = true;
    var gen = ++monGen;
    fetch("/api/gps", {cache: "no-store"})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        monInFlight = false;
        // An obsolete generation means the pane was collapsed or hidden while this request was
        // out: its answer is discarded, never rendered. But the pane may have been REOPENED
        // since — that reopen found a request in flight and could not start one — so polling
        // resumes here when the pane is open, with a fresh request rather than the stale data.
        if (gen !== monGen) { schedule(0); return; }
        if (!paneOpen()) return;
        if (d) render(d); else text("gps-mon-state", "request failed");
        schedule(MON_MS);
      })
      .catch(function () { monInFlight = false; if (gen === monGen) text("gps-mon-state", "request failed"); schedule(gen === monGen ? MON_MS : 0); });
  }
  function stop() {
    if (monTimer !== null) { clearTimeout(monTimer); monTimer = null; }
    monGen++;                              // any in-flight response is now obsolete
    stopNmea();
  }

  // ---- NMEA pane ---------------------------------------------------------------------------
  function renderNmeaLines(lines) {
    var body = $("gps-nmea-body");
    if (!body) return;
    body.textContent = lines.join("\n");
    body.scrollTop = body.scrollHeight;
  }
  function suppressNmea(why) {
    stopNmea();
    var body = $("gps-nmea-body"), lbl = $("gps-nmea-label");
    if (body) body.textContent = "";        // clear what was shown for the previous verdict
    if (lbl) lbl.textContent = why;
  }
  function nmeaSchedule(ms) {
    if (nmeaTimer !== null) { clearTimeout(nmeaTimer); nmeaTimer = null; }
    if (!nmeaOpen()) return;
    nmeaTimer = setTimeout(nmeaTick, ms);
  }
  function nmeaTick() {
    if (nmeaTimer !== null) { clearTimeout(nmeaTimer); nmeaTimer = null; }
    if (!nmeaOpen() || nmeaInFlight) return;
    if (!last || !last.nmea_ok) return;
    if (last.source === "nmea") { renderNmeaLines(last.nmea || []); nmeaSchedule(NMEA_MS); return; }  // the sample's own tail; no second reader
    nmeaInFlight = true;
    var gen = ++nmeaGen;
    fetch("/api/gps/nmea", {cache: "no-store"})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        nmeaInFlight = false;
        if (gen !== nmeaGen) { nmeaSchedule(0); return; }   // discarded; resume if the pane is open again
        if (!nmeaOpen() || !last || !last.nmea_ok) return;   // verdict moved on
        renderNmeaLines((d && d.lines) || []);
        nmeaSchedule(NMEA_MS);
      })
      .catch(function () { nmeaInFlight = false; nmeaSchedule(gen === nmeaGen ? NMEA_MS : 0); });
  }
  function stopNmea() {
    if (nmeaTimer !== null) { clearTimeout(nmeaTimer); nmeaTimer = null; }
    nmeaGen++;
  }

  // ---- wiring ------------------------------------------------------------------------------
  mon.addEventListener("toggle", function () { if (paneOpen()) tick(); else stop(); });
  if (outer) outer.addEventListener("toggle", function () { if (paneOpen()) tick(); else stop(); });
  document.addEventListener("visibilitychange", function () { if (document.hidden) stop(); else if (mon.open) tick(); });
  var skyBtn = $("gps-sky-btn"), skyWrap = $("gps-sky-wrap"), skyClose = $("gps-sky-close");
  if (skyBtn && skyWrap) skyBtn.addEventListener("click", function () { skyWrap.hidden = false; if (last) renderSky(last); });
  if (skyClose && skyWrap) skyClose.addEventListener("click", function () { skyWrap.hidden = true; });
  var nmeaBtn = $("gps-nmea-btn"), nmeaWrap = $("gps-nmea-wrap"), nmeaClose = $("gps-nmea-close");
  if (nmeaBtn && nmeaWrap) nmeaBtn.addEventListener("click", function () {
    var lbl = $("gps-nmea-label");
    if (lbl) lbl.textContent = (last && last.source === "nmea") ? "receiver NMEA (the last sample)" : "gpsd NMEA output";
    nmeaWrap.hidden = false; nmeaTick();
  });
  if (nmeaClose && nmeaWrap) nmeaClose.addEventListener("click", function () { nmeaWrap.hidden = true; stopNmea(); });
  if (mon.open) tick();
})();
