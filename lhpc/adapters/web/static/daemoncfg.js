// DAEMON-MONITOR-TREE: a radio's live daemon views (_daemon_view.html), each a closed panel under its Monitor, read-only
// over the /api/daemon endpoints. A view asks only while it is open, driven by the panel's open state (a click, the
// accordion, a restore or a page that opens it at load): "Get STATUS"/"Get STATS" one read of /api/daemon/<rid> per
// open and per Reload; "View Socket" a rolling 22-line window polled from the config socket each second; "RX/TX View"
// the dashboard's RX/TX feed every 3 s. Closing stops the polling; Reload starts the view afresh. A reply to an earlier
// run of the view (before a close or a Reload) is dropped. textContent only, never innerHTML; no setting is changed.
// Delegated on the document, so a Stacks body that arrives later needs no wiring.
(function () {
  "use strict";

  var SOCKET_LINES = 22, POLL_MS = { socket: 1000, rxtx: 3000 };

  function render(obj) {
    if (!obj || !Object.keys(obj).length) return "(no data)";
    return Object.keys(obj).map(function (k) { return k + "=" + obj[k]; }).join("\n");
  }
  function two(n) { return (n < 10 ? "0" : "") + n; }
  function stamp() {
    var d = new Date();
    return two(d.getHours()) + ":" + two(d.getMinutes()) + ":" + two(d.getSeconds());
  }

  // GET `path` under /api/daemon/<rid>; `done(data or null, failed)` only while this run of the view is the current one.
  function ask(panel, path, done) {
    var gen = panel.dvGen;
    fetch("/api/daemon/" + encodeURIComponent(panel.getAttribute("data-rid")) + path,
          { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { return { d: d, failed: false }; }, function () { return { d: null, failed: true }; })
      .then(function (res) { if (panel.dvGen === gen && panel.dvRunning) done(res.d, res.failed); });
  }

  function stop(panel) {
    if (panel.dvTimer) { clearInterval(panel.dvTimer); panel.dvTimer = 0; }
    panel.dvRunning = false;
    panel.dvGen = (panel.dvGen || 0) + 1;            // every reply still on its way is dropped
  }

  function start(panel) {
    if (panel.dvRunning) return;                     // one run per open
    panel.dvRunning = true;
    panel.dvGen = (panel.dvGen || 0) + 1;
    var rid = panel.getAttribute("data-rid"), kind = panel.getAttribute("data-kind");
    var text = panel.querySelector(".daemonview-text"), status = panel.querySelector(".daemonview-status");
    if (kind === "status" || kind === "stats") {
      text.textContent = "loading…";
      status.textContent = "";
      ask(panel, "", function (d, failed) {
        if (failed) { text.textContent = "request failed"; status.textContent = "Reload to ask again"; return; }
        text.textContent = !d || !d.reachable ? "daemon not reachable on " + rid
          : kind.toUpperCase() + " — " + rid + " (live)\n" + render(kind === "stats" ? d.stats : d.status);
        status.textContent = "updated " + stamp();
      });
      return;
    }
    var poll;
    if (kind === "socket") {
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
    status.textContent = "live — every " + POLL_MS[kind] / 1000 + " s while open";
    var tick = function () { if (!panel.isConnected) { stop(panel); return; } poll(); };   // a replaced body: stop
    tick();
    panel.dvTimer = setInterval(tick, POLL_MS[kind]);
  }

  function sync(panel) { if (panel.open) start(panel); else stop(panel); }

  document.addEventListener("toggle", function (e) {
    var panel = e.target;
    if (panel && panel.classList && panel.classList.contains("daemonview")) sync(panel);
  }, true);                                          // toggle does not bubble: caught on the way down
  document.addEventListener("click", function (e) {
    var btn = e.target && e.target.closest ? e.target.closest(".daemonview-reload") : null;
    var panel = btn ? btn.closest(".daemonview") : null;
    if (panel) { stop(panel); sync(panel); }         // afresh (a closed panel stays quiet)
  });
  // a view's own page opens its panel at load
  document.querySelectorAll(".daemonview[open]").forEach(sync);
})();
