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
