// DAEMON-MONITOR-TREE: a radio's live daemon views (_daemon_view.html) under its Monitor, read-only over the
// /api/daemon endpoints. UI-REORG s4 + F14: in the tree a view is its own closed <details>; it asks only while
// SHOWN (it and every <details> around it open, in the page, the tab visible: shown.js): STATUS / STATS one read of
// /api/daemon/<rid>
// per show and per Reload;
// SOCKET a rolling 22-line window polled from the config socket each second; "RX/TX View" the dashboard's
// RX/TX feed every 3 s; on a Monitor's own page (data-fullpage) every view repeats every 3 s. Hiding stops the
// polling; Reload starts the view afresh. A reply to an earlier run of the
// view (before a hide or a Reload) is dropped; a view has ONE request out at a time, and a show while one is out
// asks once more when it settles (so a hide/show never leaves STATUS/STATS at "loading…").
// Daemon down (the block's data-up="0", from the server and then from dash.js's per-radio poll, event
// lhpc:daemonstate): the views pause (no requests, Reload off, "Daemon not running"); they resume when the daemon is
// up. The same event greys / re-enables a Live radio settings block and fills its current values.
// textContent only, never innerHTML; no setting is changed. Delegated on the document, so a Stacks body that arrives
// later needs no wiring (sync() on lhpc:bodyloaded is the same activation path).
(function () {
  "use strict";

  var SOCKET_LINES = 22, POLL_MS = { socket: 1000, rxtx: 3000 }, FULL_MS = 3000;

  function render(obj) {
    if (!obj || !Object.keys(obj).length) return "(no data)";
    return Object.keys(obj).map(function (k) { return k + "=" + obj[k]; }).join("\n");
  }
  // RADIO view: the reply of GET RADIO (daemon 1.5.0) as it came, its own RADIO= health first among the values;
  // none: an older daemon (no RADIOERR= in its STATUS, never asked) or a 1.5.0 one whose reply was not complete.
  function radioText(d) {
    if (d.radio && Object.keys(d.radio).length) return render(d.radio);
    return d.status && "RADIOERR" in d.status ? "RADIO not readable"
      : "RADIO not reported by this daemon (needs daemon 1.5.0)";
  }
  function two(n) { return (n < 10 ? "0" : "") + n; }
  function stamp() {
    var d = new Date();
    return two(d.getHours()) + ":" + two(d.getMinutes()) + ":" + two(d.getSeconds());
  }

  // GET `path` under /api/daemon/<rid>; `done(data or null, failed)` only while this run of the view is the current
  // one. One request out per view: a show's ask while one is out is remembered (pending) and sent once it settles; a
  // timer tick while one is out is skipped (the next tick asks).
  function ask(panel, path, done) {
    if (panel.dvBusy) {
      if (!panel.dvTimer) panel.dvPending = function () { ask(panel, path, done); };
      return;
    }
    var gen = panel.dvGen;
    panel.dvBusy = true;
    fetch("/api/daemon/" + encodeURIComponent(panel.getAttribute("data-rid")) + path,
          { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { return { d: d, failed: false }; }, function () { return { d: null, failed: true }; })
      .then(function (res) {
        panel.dvBusy = false;
        if (panel.dvGen === gen && panel.dvRunning) done(res.d, res.failed);
        var again = panel.dvPending;
        panel.dvPending = null;
        if (again && panel.dvRunning) again();
      });
  }

  function up(panel) {
    var b = panel.closest("[data-up]");
    return !b || b.getAttribute("data-up") === "1";
  }

  function stop(panel) {
    if (panel.dvTimer) { clearInterval(panel.dvTimer); panel.dvTimer = 0; }
    panel.dvRunning = false;
    panel.dvPending = null;
    panel.dvGen = (panel.dvGen || 0) + 1;            // every reply still on its way is dropped
  }

  function start(panel) {
    if (panel.dvRunning) return;                     // one run per show
    panel.dvRunning = true;
    panel.dvGen = (panel.dvGen || 0) + 1;
    var rid = panel.getAttribute("data-rid"), kind = panel.getAttribute("data-kind");
    var text = panel.querySelector(".daemonview-text"), status = panel.querySelector(".daemonview-status");
    var full = !!panel.closest("[data-fullpage]");   // the Monitor's page: its one view repeats every 3 s (s4, F7)
    var poll;
    if (kind === "status" || kind === "radio" || kind === "stats") {
      text.textContent = "loading…";
      status.textContent = "";
      poll = function () {
        ask(panel, "", function (d, failed) {
          if (failed) { text.textContent = "request failed"; status.textContent = full ? "" : "Reload to ask again"; return; }
          text.textContent = !d || !d.reachable ? "daemon not reachable on " + rid
            : kind.toUpperCase() + " — " + rid + " (live)\n"
              + (kind === "radio" ? radioText(d) : render(kind === "stats" ? d.stats : d.status));
          status.textContent = "updated " + stamp();                          // F6: no "every 3 s" line
        });
      };
      if (!full) { poll(); return; }                 // in the tree: one read per show and per Reload
    } else if (kind === "socket") {
      var buf = [];
      text.textContent = "connecting to config socket (" + rid + ")…";
      poll = function () {
        ask(panel, "/socket", function (d, failed) {
          buf.push("[" + stamp() + "] " + (failed ? "(request failed)"
            : d && d.reachable && d.line ? d.line : "(no response on " + rid + ")"));
          if (buf.length > SOCKET_LINES) buf = buf.slice(-SOCKET_LINES);       // rolling window
          text.textContent = buf.join("\n");
        });
      };
    } else if (kind === "rxtx") {
      text.textContent = "loading RX/TX activity (" + rid + ")…";
      poll = function () {
        // FEED ONLY: /api/daemon/<radio> would drag a CAD scan behind a text log at every poll.
        ask(panel, "/feed", function (d, failed) {
          if (failed) return;                                                   // transient; the next tick retries
          var next = (d && d.feed && d.feed.length) ? d.feed.join("\n") : "(no recent RX/TX activity)";
          if (text.textContent !== next) text.textContent = next;              // no flicker
        });
      };
    } else {
      return;
    }
    var ms = full ? FULL_MS : POLL_MS[kind];
    if (POLL_MS[kind] && !full) status.textContent = "live — every " + ms / 1000 + " s while shown";   // F6: the tree's
    var tick = function () { if (!shown(panel)) { stop(panel); return; } poll(); };   // hidden around, replaced: stop
    tick();
    panel.dvTimer = setInterval(tick, ms);
  }

  // Shown (shown.js, s13): in the page, every <details> around it open, the tab visible.
  function shown(panel) { return window.lhpcShown(panel); }
  // The ONE activation path: start a shown view of a running daemon, else stop it.
  function sync(panel) { if (shown(panel) && up(panel)) start(panel); else stop(panel); }
  function syncAll(root) { if (root && root.querySelectorAll) root.querySelectorAll(".daemonview").forEach(sync); }

  // A daemon-state block (the Monitor, Live · rX, the Monitor page's view) takes the state `isUp`; `d` = the poll's answer.
  function apply(block, isUp, d) {
    if ((block.getAttribute("data-up") === "1") === isUp) return;
    block.setAttribute("data-up", isUp ? "1" : "0");
    block.querySelectorAll(".dstate-down").forEach(function (p) { p.hidden = isUp; });
    block.querySelectorAll(".daemonview-reload").forEach(function (b) { b.disabled = !isUp; });
    if (block.classList.contains("dlive")) {
      block.querySelectorAll(".setform").forEach(function (f) {
        var key = f.querySelector('input[name="key"]'), val = f.querySelector('[name="value"]');
        var st = (d && d.status) || {}, ch = (d && d.channel) || {};
        var cur = !key ? undefined : st[key.value] !== undefined ? st[key.value] : ch[key.value];
        if (isUp && val && cur !== undefined && cur !== null) val.value = cur;   // the current values of that answer
        f.querySelectorAll("select, input:not([type=hidden]), button").forEach(function (el) { el.disabled = !isUp; });
      });
    }
    block.querySelectorAll(".daemonview").forEach(sync);
    if (block.classList.contains("daemonview")) sync(block);
  }
  // dash.js: one answer per poll of a radio.
  document.addEventListener("lhpc:daemonstate", function (e) {
    var s = e.detail || {};
    if (!s.rid) return;
    document.querySelectorAll("[data-up][data-rid]").forEach(function (b) {
      if (b.getAttribute("data-rid") === s.rid) apply(b, !!s.up, s.data);
    });
  });
  // A block that arrives later (a lazy row body): the cached state, only when fresher than one poll (else it keeps its
  // server-rendered state and takes the next answer).
  function replay(root) {
    if (!window.lhpcDaemonState || !root.querySelectorAll) return;
    root.querySelectorAll("[data-up][data-rid]").forEach(function (b) {
      var s = window.lhpcDaemonState(b.getAttribute("data-rid"));
      if (s) apply(b, s.up, s.data);
    });
  }

  document.addEventListener("toggle", function (e) {
    var el = e.target;
    if (el && el.querySelectorAll) syncAll(el);      // a Monitor, Settings or row around views opened or closed
    if (el && el.classList && el.classList.contains("daemonview")) sync(el);   // F14: a view's own node
  }, true);                                          // toggle does not bubble: caught on the way down
  document.addEventListener("visibilitychange", function () { syncAll(document); });   // s13 P1: hidden tab -> quiet
  document.addEventListener("click", function (e) {
    var btn = e.target && e.target.closest ? e.target.closest(".daemonview-reload") : null;
    var panel = btn ? btn.closest(".daemonview") : null;
    if (panel) { stop(panel); sync(panel); }         // afresh (a hidden view stays quiet)
  });
  document.addEventListener("lhpc:bodyloaded", function (e) {
    var root = (e.detail && e.detail.root) || document;
    replay(root);
    syncAll(root);
  });
  syncAll(document);                                 // every shown view at load (a view's own page included)
})();
