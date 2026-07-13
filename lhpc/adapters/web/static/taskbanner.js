/* Running-task indicator: 2 s poll of the read-only /api/tasks (auto-install + HMAC + build/test/install
 * jobs). The SERVER owns visibility (done expires after 60 s; failed/unsafe STAY) — the client renders
 * whatever the server returns, keyed by kind+run_id. A `failed` item gets a ✕ (dismiss); an `unsafe` JOB
 * gets a Recover button — both POST (CSRF) to /api/tasks/{dismiss,recover}. CSP-safe: createElement /
 * textContent / setAttribute only, never innerHTML. */
(function () {
  "use strict";
  var banner = document.getElementById("task-banner");
  if (!banner) return;
  var CSRF = banner.getAttribute("data-csrf") || "";
  var LABELS = {running: "running…", done: "finished", failed: "failed",
                unsafe: "UNSAFE — needs attention"};

  function keyOf(t) { return t.kind + "-" + t.run_id; }

  function post(url, t) {
    var body = new URLSearchParams();
    body.set("_csrf", CSRF);
    body.set("kind", t.kind || "");
    body.set("run_id", t.run_id || "");
    body.set("attempt_id", t.attempt_id || "");
    return fetch(url, {method: "POST", cache: "no-store",
                       headers: {"Content-Type": "application/x-www-form-urlencoded"}, body: body});
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function render(tasks) {
    tasks = tasks || [];
    var wanted = {};
    tasks.forEach(function (t) { wanted[keyOf(t)] = true; });
    Array.prototype.slice.call(banner.children).forEach(function (c) {
      if (!wanted[c.getAttribute("data-key")]) banner.removeChild(c);
    });
    tasks.forEach(function (t) {
      var key = keyOf(t), row = null, i;
      for (i = 0; i < banner.children.length; i++) {
        if (banner.children[i].getAttribute("data-key") === key) { row = banner.children[i]; break; }
      }
      if (!row) {
        row = el("div", null);
        row.setAttribute("data-key", key);
        row.appendChild(el("span", "ti-label"));
        row.appendChild(el("span", "ti-state"));
        row.appendChild(el("span", "ti-hint"));
        row.appendChild(el("a", "ti-view", "view →"));
        banner.appendChild(row);
      }
      row.className = "taskitem taskitem-" + t.state;
      row.querySelector(".ti-label").textContent = t.label || "";
      row.querySelector(".ti-state").textContent = LABELS[t.state] || t.state || "";
      var hint = row.querySelector(".ti-hint");
      hint.textContent = t.hint || "";
      hint.style.display = t.hint ? "" : "none";
      row.querySelector(".ti-view").setAttribute("href", t.href || "#");

      // ✕ (dismiss) for failed; Recover for an unsafe JOB. Rebuild the trailing control on state change.
      var oldBtn = row.querySelector(".ti-close, .ti-recover");
      if (oldBtn) row.removeChild(oldBtn);
      if (t.state === "failed") {
        var x = el("button", "ti-close", "×"); x.setAttribute("type", "button"); x.title = "Dismiss";
        x.addEventListener("click", function () {
          post("/api/tasks/dismiss", t).then(function (r) { if (r.ok) row.parentNode && banner.removeChild(row); });
        });
        row.appendChild(x);
      } else if (t.state === "unsafe" && t.kind === "job") {
        var rec = el("button", "ti-recover", "Recover"); rec.setAttribute("type", "button");
        rec.addEventListener("click", function () { post("/api/tasks/recover", t).then(tick); });
        row.appendChild(rec);
      }
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
