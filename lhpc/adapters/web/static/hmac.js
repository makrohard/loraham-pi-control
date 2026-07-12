/* HMAC apply run view: 2 s poll of /api/hmac-apply.
 * - step pills updated via textContent ONLY (never innerHTML)
 * - append-only log via cursor/offset chunks (byte-capped + secret-redacted server-side)
 * - the cursor RESETS when run_id changes; terminal states slow polling down */
(function () {
  "use strict";
  var runCard = document.getElementById("hmac-run");
  if (!runCard) return;
  var logbox = document.getElementById("hmac-logbox");
  var badge = document.getElementById("hmac-status");
  var runId = runCard.getAttribute("data-run") || "";
  var offset = 0;
  var timer = null;
  var interval = 2000;

  function atBottom() {
    return logbox.scrollHeight - logbox.scrollTop - logbox.clientHeight < 40;
  }

  function setBadge(text, cls) {
    if (!badge) return;
    badge.textContent = text;
    badge.className = "badge " + cls;
  }

  function stepBadge(cell, state) {
    var span = cell.firstElementChild;
    if (!span) return;
    span.textContent = state + (state === "running" ? "…" : "");
    span.className = "badge badge-" + (state === "running" ? "running" :
      (state === "done" ? "ok" : (state === "failed" ? "failed" : "stopped")));
  }

  function render(d) {
    var st = d.state || {};
    if (st.absent || st.unsafe) return;
    if (d.run_id && d.run_id !== runId) {          // a NEW run took over
      runId = d.run_id;
      offset = 0;
      logbox.textContent = "";
      window.location.reload();                     // step set / headers changed
      return;
    }
    (st.steps || []).forEach(function (s) {
      var row = runCard.querySelector('tr[data-step="' + s.key + '"]');
      if (!row || row.children.length < 2) return;
      stepBadge(row.children[1], s.state);
    });
    if (st.phase === "running") setBadge("in progress", "badge-running");
    else if (st.phase === "done") setBadge("done", "badge-ok");
    else if (st.phase === "interrupted")
      setBadge("ended unexpectedly — incomplete", "badge-failed");
    else setBadge("failed", "badge-failed");
    var log = d.log || {};
    if (typeof log.offset === "number") {
      if (log.offset < offset) { offset = 0; logbox.textContent = ""; }
      if (log.data) {
        var stick = atBottom();
        logbox.textContent += log.data;
        offset = log.offset;
        if (stick) logbox.scrollTop = logbox.scrollHeight;
      } else {
        offset = log.offset;
      }
    }
    if (!d.running && interval < 10000) {
      interval = 10000;                            // terminal: slow down
      clearInterval(timer);
      timer = setInterval(tick, interval);
    }
  }

  function tick() {
    fetch("/api/hmac-apply?offset=" + offset, {cache: "no-store"})
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function () { /* transient */ });
  }

  timer = setInterval(tick, interval);
  tick();
})();
