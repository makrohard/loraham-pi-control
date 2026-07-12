/* Running-task indicator: 2 s poll of the read-only /api/tasks (install-all + HMAC apply).
 * The SERVER owns visibility (60 s expiry after finish; `unsafe` never expires) — the client simply
 * renders whatever the server returns, keyed by kind+run_id. No client-side removal timers (so a
 * completed task can never reappear after reload, and there are no duplicate timers). CSP-safe:
 * createElement / textContent / setAttribute only, never innerHTML. */
(function () {
  "use strict";
  var banner = document.getElementById("task-banner");
  if (!banner) return;
  var LABELS = {running: "running…", done: "finished", failed: "failed",
                unsafe: "UNSAFE — needs attention"};

  function keyOf(t) { return t.kind + "-" + t.run_id; }

  function render(tasks) {
    tasks = tasks || [];
    var wanted = {};
    tasks.forEach(function (t) { wanted[keyOf(t)] = true; });
    Array.prototype.slice.call(banner.children).forEach(function (el) {
      if (!wanted[el.getAttribute("data-key")]) banner.removeChild(el);
    });
    tasks.forEach(function (t) {
      var key = keyOf(t);
      var el = null, i;
      for (i = 0; i < banner.children.length; i++) {
        if (banner.children[i].getAttribute("data-key") === key) { el = banner.children[i]; break; }
      }
      if (!el) {
        el = document.createElement("div");
        el.setAttribute("data-key", key);
        var label = document.createElement("span"); label.className = "ti-label";
        var state = document.createElement("span"); state.className = "ti-state";
        var view = document.createElement("a"); view.className = "ti-view"; view.textContent = "view →";
        el.appendChild(label);
        el.appendChild(state);
        el.appendChild(view);
        banner.appendChild(el);
      }
      el.className = "taskitem taskitem-" + t.state;
      el.querySelector(".ti-label").textContent = t.label || "";
      el.querySelector(".ti-state").textContent = LABELS[t.state] || t.state || "";
      el.querySelector(".ti-view").setAttribute("href", t.href || "#");
    });
  }

  function tick() {
    fetch("/api/tasks", {cache: "no-store"})
      .then(function (r) { return r.json(); })
      .then(function (d) { render(d.tasks); })
      .catch(function () { /* transient */ });
  }

  setInterval(tick, 2000);
  tick();
})();
