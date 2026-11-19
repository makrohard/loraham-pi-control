// Live log tail — polls the read-only log API every 3 s and keeps the box scrolled to the
// bottom (unless the user has scrolled up to read history). Same-origin only. UI-REORG s5: one poller for every full
// log page; the source adapter: a program's log (/api/logs/<target>, with its process state) or a static page's
// preview source (data-preview: /api/logpreview/<source>?lines=300, no process state).
(function () {
  "use strict";
  var card = document.getElementById("log-card");
  if (!card) return;
  var target = card.getAttribute("data-target");
  var job = card.getAttribute("data-job");
  var radio = card.getAttribute("data-radio");
  var preview = card.getAttribute("data-preview");
  var q = [];
  if (job) q.push("job=" + encodeURIComponent(job));
  if (radio) q.push("radio=" + encodeURIComponent(radio));
  var url = preview ? "/api/logpreview/" + encodeURIComponent(preview) + "?lines=300"
    : "/api/logs/" + encodeURIComponent(target) + (q.length ? "?" + q.join("&") : "");
  var box = document.getElementById("logbox");
  var status = document.getElementById("log-status");

  function atBottom() {
    return box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  }

  function mark(running) {
    if (!status) return;
    status.textContent = running ? "process running" : "process ended";
    status.className = "badge badge-" + (running ? "running" : "stopped");
  }

  function poll() {
    fetch(url)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) { return; }
        var stick = atBottom();
        box.textContent = (d.lines && d.lines.length) ? d.lines.join("\n") : "(no output yet)";
        if (stick) box.scrollTop = box.scrollHeight;
        if (!preview) mark(d.running);
      })
      .catch(function () { if (status) status.textContent = "retrying…"; });
  }

  // The RF log's Clear asks once (OK/Cancel) before the form goes out — CSP forbids an inline
  // handler, so the question lives here and the template only carries the text.
  var clear = document.getElementById("rflog-clear");
  if (clear) {
    clear.addEventListener("submit", function (e) {
      if (!window.confirm(clear.getAttribute("data-confirm") || "Clear the RF log?")) e.preventDefault();
    });
  }

  box.scrollTop = box.scrollHeight;     // start at the newest line
  setInterval(poll, 3000);
  poll();
})();
