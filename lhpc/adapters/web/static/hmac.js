/* HMAC apply run view: 1 s poll of /api/hmac-apply (smoother live log; live-run-only).
 * - step pills updated via textContent ONLY (never innerHTML)
 * - two append-only log windows via cursor chunks (byte-capped + secret-redacted server-side):
 *     #hmac-logbox   = LHPC narration (offset cursor)
 *     #hmac-complog  = every step's log end-to-end, header-framed (index/offset cursor)
 * - RELOAD ONCE when a live run becomes terminal (done/failed/unsafe/interrupted): the reloaded page is
 *   terminal so it does NOT load this poller (no reload loop) — the Abort button goes and Apply/Recover return. */
(function () {
  "use strict";
  var runCard = document.getElementById("hmac-run");
  if (!runCard) return;
  var logbox = document.getElementById("hmac-logbox");
  var complog = document.getElementById("hmac-complog");
  var badge = document.getElementById("hmac-status");
  var runId = runCard.getAttribute("data-run") || "";
  var offset = 0, ci = 0, co = 0;
  var timer = null;
  var reloaded = false;

  function atBottom(el) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
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

  function appendLog(el, chunk, curOffset, setOffset) {
    if (!el || !chunk || typeof chunk.offset !== "number") return curOffset;
    if (chunk.offset < curOffset) { el.textContent = ""; curOffset = 0; }
    if (chunk.data) {
      var stick = atBottom(el);
      el.textContent += chunk.data;
      if (stick) el.scrollTop = el.scrollHeight;
    }
    return chunk.offset;
  }

  function render(d) {
    var st = d.state || {};
    if (st.absent || st.unsafe) return;
    if (d.run_id && d.run_id !== runId) {          // a NEW run took over
      window.location.reload();
      return;
    }
    (st.steps || []).forEach(function (s) {
      var row = runCard.querySelector('tr[data-step="' + s.key + '"]');
      if (row && row.children.length >= 2) stepBadge(row.children[1], s.state);
    });
    if (st.phase === "running") setBadge("in progress", "badge-running");
    else if (st.phase === "done") setBadge("done", "badge-ok");
    else if (st.phase === "unsafe") setBadge("UNSAFE — build not proven stopped", "badge-failed");
    else if (st.phase === "interrupted") setBadge("ended unexpectedly — incomplete", "badge-failed");
    else setBadge("failed", "badge-failed");

    offset = appendLog(logbox, d.log || {}, offset);
    var cl = d.complog || {};
    if (typeof cl.index === "number") {
      if (cl.index < ci || (cl.index === ci && cl.offset < co)) { complog.textContent = ""; }
      if (complog && cl.data) {
        var stick = atBottom(complog);
        complog.textContent += cl.data;
        if (stick) complog.scrollTop = complog.scrollHeight;
      }
      ci = cl.index; co = cl.offset;
    }

    if (st.phase && st.phase !== "running" && !reloaded) {
      reloaded = true;                              // terminal: reload ONCE to swap the controls
      clearInterval(timer);
      window.location.reload();
    }
  }

  function tick() {
    fetch("/api/hmac-apply?offset=" + offset + "&ci=" + ci + "&co=" + co, {cache: "no-store"})
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function () { /* transient */ });
  }

  // 1 s poll (smoother than auto-install's 2 s): the poller runs ONLY while the run is live, then reloads
  // once to the collapsed historical view — so the faster cadence never outlives the run.
  timer = setInterval(tick, 1000);
  tick();
})();
