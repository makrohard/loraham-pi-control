// Daemon config panel (in the daemon stack body): live read-only views of the daemon over the
// /api/daemon endpoints — "Get STATUS/STATS" (one-shot), "View Socket" (rolling 22-line poll) and
// "TX-Viewer" (RX/TX feed). Display only (textContent, never innerHTML); no settings are changed.
// init(root) re-runs on lhpc:bodyloaded so a lazily-loaded daemon body gets wired too.
(function () {
  "use strict";

  var SOCKET_LINES = 22, SOCKET_POLL_MS = 1000, TX_POLL_MS = 3000;

  function render(obj) {
    if (!obj || !Object.keys(obj).length) return "(no data)";
    return Object.keys(obj).map(function (k) { return k + "=" + obj[k]; }).join("\n");
  }
  function two(n) { return (n < 10 ? "0" : "") + n; }
  function stamp() {
    var d = new Date();
    return two(d.getHours()) + ":" + two(d.getMinutes()) + ":" + two(d.getSeconds());
  }

  // "Get STATUS" / "Get STATS": one-shot read of the daemon's config socket.
  function wireLive(b) {
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
  }
  function wireLiveClose(x) {
    x.addEventListener("click", function () {
      var wrap = document.getElementById("liveout-" + x.getAttribute("data-band"));
      if (wrap) wrap.hidden = true;
    });
  }

  // "View Socket": a live, rolling 22-line window streamed from the daemon CONF socket. The X closes
  // the window AND stops the polling. textContent only; one poller per band; cleared on close.
  function wireSocket(b) {
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
        if (buf.length > SOCKET_LINES) buf = buf.slice(-SOCKET_LINES);   // rolling window
        body.textContent = buf.join("\n");                              // textContent -> no HTML injection
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
      wrap.dataset.timer = String(setInterval(poll, SOCKET_POLL_MS));
    });
  }
  function wireSocketClose(x) {
    x.addEventListener("click", function () {
      var wrap = document.getElementById("socketout-" + x.getAttribute("data-band"));
      if (!wrap) return;
      if (wrap.dataset.timer) {           // stop polling = disconnect the "socket"
        clearInterval(Number(wrap.dataset.timer));
        delete wrap.dataset.timer;
      }
      wrap.hidden = true;
    });
  }

  // "TX-Viewer": the SAME read-only RX/TX feed as the dashboard, in a closable 22-line window.
  function wireTx(b) {
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
      wrap.dataset.timer = String(setInterval(poll, TX_POLL_MS));
    });
  }
  function wireTxClose(x) {
    x.addEventListener("click", function () {
      var wrap = document.getElementById("txout-" + x.getAttribute("data-band"));
      if (!wrap) return;
      if (wrap.dataset.timer) { clearInterval(Number(wrap.dataset.timer)); delete wrap.dataset.timer; }
      wrap.hidden = true;
    });
  }

  function init(root) {
    var scope = root || document;
    scope.querySelectorAll(".livebtn").forEach(wireLive);
    scope.querySelectorAll(".liveclose").forEach(wireLiveClose);
    scope.querySelectorAll(".socketbtn").forEach(wireSocket);
    scope.querySelectorAll(".socketclose").forEach(wireSocketClose);
    scope.querySelectorAll(".txbtn").forEach(wireTx);
    scope.querySelectorAll(".txclose").forEach(wireTxClose);
  }

  init();
  document.addEventListener("lhpc:bodyloaded", function (e) { init((e.detail || {}).root); });
})();
