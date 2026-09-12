// RF log page: records table with sort/filter/columns, a raw view, and the Decrypt toggle for
// encrypted stacks. Every field is radio-controlled text: it reaches the DOM through textContent
// only, never as HTML. State (columns, filter, direction, sort, raw, decrypt) lives in this
// browser's localStorage; nothing about it is stored on the box. Same-origin only.
(function () {
  "use strict";
  var card = document.getElementById("log-card");
  var table = document.getElementById("rfview");
  if (!card || !table) return;
  var target = card.getAttribute("data-target");
  var job = card.getAttribute("data-job");
  var canDecrypt = card.getAttribute("data-decoder") === "1";
  var base = "/api/rflog/" + encodeURIComponent(target) + "?job=" + encodeURIComponent(job);
  var tbody = table.querySelector("tbody");
  var status = document.getElementById("log-status");
  var box = document.getElementById("logbox");
  var notice = document.getElementById("rf-notice");
  var COLS = ["ts", "dir", "rssi", "snr", "len", "outcome", "summary", "hex", "ascii"];
  if (canDecrypt) COLS.push("decoded");
  var LABEL = {ts: "time", dir: "dir", rssi: "rssi", snr: "snr", len: "len", outcome: "outcome",
               summary: "summary", hex: "hex", ascii: "ascii", decoded: "decoded"};
  var narrow = window.matchMedia && window.matchMedia("(max-width: 700px)").matches;
  var state = {cols: {}, filter: "", dir: "", sort: "", desc: false, raw: false, decrypt: false};
  COLS.forEach(function (c) { state.cols[c] = !(narrow && (c === "hex" || c === "ascii")); });
  var KEY = "lhpc.rflog." + job;
  try { var saved = JSON.parse(localStorage.getItem(KEY) || "null"); if (saved) { Object.assign(state, saved); } } catch (e) { /* no storage: defaults */ }
  state.decrypt = false;                     // the plaintext reveal always starts OFF; it is never remembered
  function save() {
    var keep = Object.assign({}, state); delete keep.decrypt;
    try { localStorage.setItem(KEY, JSON.stringify(keep)); } catch (e) { /* ignore */ }
  }

  var records = [];
  var lastSig = "";
  var lastError = "";
  var gen = 0;                               // bumped on every Decrypt toggle: a response from an
                                             // older generation may never touch the table

  function cell(text) { var td = document.createElement("td"); td.textContent = text == null ? "" : String(text); return td; }
  function fmt(v, c) {
    if (v == null || v === "") return "";
    if (c === "rssi" || c === "snr") return Number(v).toFixed(2);
    return String(v);
  }
  function matches(r) {
    if (state.dir && r.dir !== state.dir) return false;
    if (!state.filter) return true;
    var f = state.filter.toLowerCase();
    return ["summary", "ascii", "hex", "decoded", "raw"].some(function (c) { return r[c] && String(r[c]).toLowerCase().indexOf(f) >= 0; });
  }
  function sorted(list) {
    if (!state.sort) return list;
    var c = state.sort, d = state.desc ? -1 : 1;
    return list.slice().sort(function (a, b) {
      var x = a[c], y = b[c];
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      if (typeof x === "number" && typeof y === "number") return (x - y) * d;
      return String(x).localeCompare(String(y)) * d;
    });
  }
  function render() {
    table.querySelectorAll("th[data-col]").forEach(function (th) {
      var c = th.getAttribute("data-col");
      th.hidden = !state.cols[c];
      th.setAttribute("aria-sort", state.sort === c ? (state.desc ? "descending" : "ascending") : "none");
    });
    document.querySelectorAll("#rf-cols input[type=checkbox]").forEach(function (cb) { cb.checked = !!state.cols[cb.value]; });
    var frag = document.createDocumentFragment();
    sorted(records.filter(matches)).forEach(function (r) {
      var tr = document.createElement("tr");
      if (r.status && r.status !== "ok") tr.className = "rf-" + r.status;
      COLS.forEach(function (c) {
        var td = cell(fmt(r[c], c));
        if (c === "decoded" && r.status && r.status !== "ok") td.textContent = "[" + (r.decoded || r.status) + "]";
        if (c === "hex" || c === "ascii" || c === "decoded") td.className = "rf-payload";
        td.hidden = !state.cols[c];
        tr.appendChild(td);
      });
      tr.addEventListener("click", function () { tr.classList.toggle("rf-open"); });
      frag.appendChild(tr);
    });
    tbody.textContent = "";
    tbody.appendChild(frag);
    table.hidden = state.raw;
    box.hidden = !state.raw;
    if (state.raw) box.textContent = records.length ? records.map(function (r) { return r.raw; }).join("\n") : "(no output yet)";
    notice.textContent = lastError;
    notice.hidden = !lastError;
    var dec = document.getElementById("rf-decrypt");
    if (dec) { dec.setAttribute("aria-pressed", state.decrypt ? "true" : "false"); dec.textContent = state.decrypt ? "Decrypt: on" : "Decrypt: off"; }
  }
  function mark(running) {
    if (!status) return;
    status.textContent = running ? "process running" : "process ended";
    status.className = "badge badge-" + (running ? "running" : "stopped");
  }
  function poll() {
    var url = state.decrypt ? base.replace("?job=", "/decoded?job=") : base;
    var mine = gen;
    fetch(url, {cache: "no-store"})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || mine !== gen) { return; }  // stale: the toggle moved while this was in flight
        var sig = (d.records || []).map(function (r) {
          return r.key + ":" + (r.status || "") + ":" + (r.kind || "") + ":" + (r.peer || "") + ":" + (r.decoded || "");
        }).join(",") + "|" + (d.error || "");
        mark(d.running);
        if (sig === lastSig) return;
        lastSig = sig;
        records = d.records || [];
        lastError = d.error || "";
        render();
      })
      .catch(function () { if (status) status.textContent = "retrying…"; });
  }

  table.querySelectorAll("th[data-col] button").forEach(function (b) {
    b.addEventListener("click", function () {
      var c = b.parentNode.getAttribute("data-col");
      if (state.sort === c) { state.desc = !state.desc; } else { state.sort = c; state.desc = false; }
      save(); render();
    });
  });
  var filter = document.getElementById("rf-filter");
  if (filter) { filter.value = state.filter; filter.addEventListener("input", function () { state.filter = filter.value; save(); render(); }); }
  var dirsel = document.getElementById("rf-dir");
  if (dirsel) { dirsel.value = state.dir; dirsel.addEventListener("change", function () { state.dir = dirsel.value; save(); render(); }); }
  document.querySelectorAll("#rf-cols input[type=checkbox]").forEach(function (cb) {
    cb.addEventListener("change", function () { state.cols[cb.value] = cb.checked; save(); render(); });
  });
  var rawbtn = document.getElementById("rf-raw");
  if (rawbtn) rawbtn.addEventListener("click", function () { state.raw = !state.raw; rawbtn.setAttribute("aria-pressed", state.raw ? "true" : "false"); save(); render(); });
  var decbtn = document.getElementById("rf-decrypt");
  if (decbtn) decbtn.addEventListener("click", function () {
    state.decrypt = !state.decrypt;
    gen += 1;
    if (state.decrypt) { state.cols.decoded = true; }  // turning it on always shows the column
    else {                                             // off: nothing decoded stays on screen
      records = records.map(function (r) { var c = Object.assign({}, r); delete c.status; delete c.kind; delete c.peer; delete c.decoded; return c; });
      lastError = "";
    }
    lastSig = ""; save(); render(); poll();
  });
  var clear = document.getElementById("rflog-clear");
  if (clear) clear.addEventListener("submit", function (e) { if (!window.confirm(clear.getAttribute("data-confirm") || "Clear the RF log?")) e.preventDefault(); });

  render();
  setInterval(poll, 2000);
  poll();
})();
