// Live log tail — polls the read-only log API and keeps the box scrolled to the
// bottom (unless the user has scrolled up to read history). Same-origin only.
(function () {
  "use strict";
  var card = document.getElementById("log-card");
  if (!card) return;
  var target = card.getAttribute("data-target");
  var job = card.getAttribute("data-job");
  var url = "/api/logs/" + encodeURIComponent(target) + (job ? "?job=" + encodeURIComponent(job) : "");
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
        mark(d.running);
      })
      .catch(function () { if (status) status.textContent = "retrying…"; });
  }

  box.scrollTop = box.scrollHeight;     // start at the newest line
  setInterval(poll, 2000);
  poll();
})();
