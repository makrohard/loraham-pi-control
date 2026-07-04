/* Bulk install-all run view: 2 s poll of /api/install-all.
 * - task rows + run badge updated via textContent ONLY (never innerHTML)
 * - append-only log via cursor/offset chunks (byte-capped server-side)
 * - the cursor RESETS when run_id changes (a new run never appends onto the old one)
 * - terminal states slow polling down */
(function () {
  "use strict";
  var runCard = document.getElementById("bulk-run");
  if (!runCard) return;
  var logbox = document.getElementById("bulk-logbox");
  var complog = document.getElementById("bulk-complog");
  var badge = document.getElementById("bulk-status");
  var ci = 0, co = 0;                              // component-log stream cursor
  var COMPLOG_MAX = 1500000;                       // big scrollback, front-trimmed
  var runId = runCard.getAttribute("data-run") || "";
  // Starting card: reload ONLY when the EXPECTED new run's marker appears — the old
  // terminal marker may still answer the API meanwhile and must not trigger reloads.
  var expect = runCard.getAttribute("data-run-expect") || "";
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

  function statusBadge(cell, status) {
    var span = cell.firstElementChild;
    if (!span) return;
    var active = status === "downloading" || status === "building" || status === "testing";
    span.textContent = active ? status + "…" : status;
    span.className = "badge badge-" + (active ? "running" :
      (status === "success" ? "ok" : (status === "fail" ? "failed" : "stopped")));
  }

  function render(d) {
    var st = d.state || {};
    if (expect) {
      if (d.run_id === expect) { window.location.reload(); return; }  // marker landed
      if (!d.spawn_live) {
        // The spawn ended WITHOUT our marker: a pre-claim refusal — show its output.
        window.location.href = "/install-all?spawn=" + encodeURIComponent(expect);
      }
      return;
    }
    if (st.absent || st.unsafe) return;
    if (d.run_id && d.run_id !== runId) {          // a NEW run took over
      runId = d.run_id;
      offset = 0;
      logbox.textContent = "";
      window.location.reload();                     // row set / headers changed
      return;
    }
    (st.stacks || []).forEach(function (r) {
      var row = runCard.querySelector('tr[data-stack="' + r.id + '"]');
      if (!row || row.children.length < 4) return;
      statusBadge(row.children[1], r.status);
      var t = "";
      if (r.tests && r.tests.ran) t = r.tests.ok ? "passed" : "FAILED";
      else if (r.tests) t = r.tests.detail || "—";
      if (r.tx && r.tx.ran) t += " / TX " + (r.tx.ok ? "passed" : "FAILED");
      row.children[2].textContent = t;
      row.children[3].textContent = r.detail || "";
    });
    var txRow = runCard.querySelector('tr[data-stack="__tx__"]');
    if (txRow && st.tx_phase) {
      txRow.children[1].firstElementChild.textContent = st.tx_phase.status;
      txRow.children[3].textContent = st.tx_phase.detail || "";
    }
    if (d.running) setBadge("run in progress", "badge-running");
    else if (st.state === "completed") setBadge("run completed", "badge-ok");
    else if (st.state === "completed-with-failures")
      setBadge("completed with failures", "badge-failed");
    else setBadge("ended unexpectedly — incomplete", "badge-failed");
    var log = d.log || {};
    if (typeof log.offset === "number") {
      if (log.offset < offset) { offset = 0; logbox.textContent = ""; }
      if (log.data) {
        var stick = atBottom();
        if (log.offset - (log.data ? log.data.length : 0) === 0 && offset !== 0) {
          logbox.textContent = "";                 // server restarted the cursor
        }
        logbox.textContent += log.data;
        offset = log.offset;
        if (stick) logbox.scrollTop = logbox.scrollHeight;
      } else {
        offset = log.offset;
      }
    }
    var cl = d.complog || null;
    if (complog && cl && (cl.data || typeof cl.index === "number")) {
      if (cl.index < ci || (cl.index === ci && cl.offset < co)) {
        complog.textContent = "";                  // server restarted the stream
      }
      if (cl.data) {
        var stick2 = complog.scrollHeight - complog.scrollTop
                     - complog.clientHeight < 40;
        complog.textContent += cl.data;
        if (complog.textContent.length > COMPLOG_MAX) {
          var cut = complog.textContent.length - (COMPLOG_MAX - 200000);
          var nl = complog.textContent.indexOf("\n", cut);
          complog.textContent = "[… older output trimmed …]\n"
            + complog.textContent.slice(nl >= 0 ? nl + 1 : cut);
        }
        if (stick2) complog.scrollTop = complog.scrollHeight;
      }
      ci = cl.index; co = cl.offset;
    }
    if (!d.running && interval < 10000) {
      interval = 10000;                            // terminal: slow down
      clearInterval(timer);
      timer = setInterval(tick, interval);
    }
  }

  function tick() {
    fetch("/api/install-all?offset=" + offset + "&ci=" + ci + "&co=" + co,
          {cache: "no-store"})
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function () { /* transient */ });
  }

  timer = setInterval(tick, interval);
  tick();
})();
