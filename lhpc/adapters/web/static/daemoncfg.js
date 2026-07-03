// Daemon config page: "Get STATUS" / "Get STATS" buttons issue a live read of the
// daemon's config socket (via the read-only /api/daemon endpoint) and show the raw
// key=value lines. Display only; no settings are changed here. Each output box has an
// X to close it again.
(function () {
  "use strict";
  var btns = document.querySelectorAll(".livebtn");
  if (!btns.length) return;

  function render(obj) {
    if (!obj || !Object.keys(obj).length) return "(no data)";
    return Object.keys(obj).map(function (k) { return k + "=" + obj[k]; }).join("\n");
  }

  btns.forEach(function (b) {
    b.addEventListener("click", function () {
      var band = b.getAttribute("data-band");
      var kind = b.getAttribute("data-kind");
      var wrap = document.getElementById("liveout-" + band);
      var body = document.getElementById("liveout-body-" + band);
      if (!wrap || !body) return;
      wrap.hidden = false;
      body.textContent = "loading…";
      fetch("/api/daemon/" + encodeURIComponent(band))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d || !d.reachable) { body.textContent = "daemon not reachable on " + band + " MHz"; return; }
          var data = kind === "stats" ? d.stats : d.status;
          body.textContent = kind.toUpperCase() + " — " + band + " MHz (live)\n" + render(data);
        })
        .catch(function () { body.textContent = "request failed"; });
    });
  });

  document.querySelectorAll(".liveclose").forEach(function (x) {
    x.addEventListener("click", function () {
      var wrap = document.getElementById("liveout-" + x.getAttribute("data-band"));
      if (wrap) wrap.hidden = true;
    });
  });
})();

// "View Socket": a live, rolling 22-line window streamed from the daemon CONF socket. Clicking
// starts polling the READ-ONLY /api/daemon/<band>/socket endpoint (one bounded, server-sanitised
// status line per poll); the X closes the window AND stops the polling (disconnects). Display only:
// textContent (never innerHTML), a fixed 22-line ring buffer, one poller per band, cleared on close.
(function () {
  "use strict";
  var LINES = 22, POLL_MS = 1000;
  function two(n) { return (n < 10 ? "0" : "") + n; }
  function stamp() {
    var d = new Date();
    return two(d.getHours()) + ":" + two(d.getMinutes()) + ":" + two(d.getSeconds());
  }

  document.querySelectorAll(".socketbtn").forEach(function (b) {
    b.addEventListener("click", function () {
      var band = b.getAttribute("data-band");
      var wrap = document.getElementById("socketout-" + band);
      var body = document.getElementById("socketout-body-" + band);
      if (!wrap || !body || wrap.dataset.timer) return;   // no double-start
      wrap.hidden = false;
      var buf = [];
      body.textContent = "connecting to config socket (" + band + " MHz)…";
      function push(text) {
        buf.push("[" + stamp() + "] " + text);
        if (buf.length > LINES) buf = buf.slice(-LINES);  // rolling 22-line window
        body.textContent = buf.join("\n");                // textContent -> no HTML injection
      }
      function poll() {
        fetch("/api/daemon/" + encodeURIComponent(band) + "/socket", { cache: "no-store" })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) {
            push(d && d.reachable && d.line ? d.line : "(no response on " + band + " MHz)");
          })
          .catch(function () { push("(request failed)"); });
      }
      poll();
      wrap.dataset.timer = String(setInterval(poll, POLL_MS));
    });
  });

  document.querySelectorAll(".socketclose").forEach(function (x) {
    x.addEventListener("click", function () {
      var wrap = document.getElementById("socketout-" + x.getAttribute("data-band"));
      if (!wrap) return;
      if (wrap.dataset.timer) {           // stop polling = disconnect the "socket"
        clearInterval(Number(wrap.dataset.timer));
        delete wrap.dataset.timer;
      }
      wrap.hidden = true;
    });
  });
})();

// "TX-Viewer": the SAME read-only RX/TX activity feed as the dashboard (/api/daemon/<band> feed),
// shown here in a closable, fixed 22-line-tall window. Display only (textContent, no mutation);
// one poller per band, cleared on the X close.
(function () {
  "use strict";
  var POLL_MS = 3000;
  document.querySelectorAll(".txbtn").forEach(function (b) {
    b.addEventListener("click", function () {
      var band = b.getAttribute("data-band");
      var wrap = document.getElementById("txout-" + band);
      var body = document.getElementById("txout-body-" + band);
      if (!wrap || !body || wrap.dataset.timer) return;   // no double-start
      wrap.hidden = false;
      body.textContent = "loading RX/TX activity (" + band + " MHz)…";
      function poll() {
        fetch("/api/daemon/" + encodeURIComponent(band), { cache: "no-store" })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) {
            var next = (d && d.feed && d.feed.length) ? d.feed.join("\n")
                                                      : "(no recent RX/TX activity)";
            if (body.textContent !== next) body.textContent = next;   // avoid flicker
          })
          .catch(function () { /* transient; retry next tick */ });
      }
      poll();
      wrap.dataset.timer = String(setInterval(poll, POLL_MS));
    });
  });

  document.querySelectorAll(".txclose").forEach(function (x) {
    x.addEventListener("click", function () {
      var wrap = document.getElementById("txout-" + x.getAttribute("data-band"));
      if (!wrap) return;
      if (wrap.dataset.timer) { clearInterval(Number(wrap.dataset.timer)); delete wrap.dataset.timer; }
      wrap.hidden = true;
    });
  });
})();
