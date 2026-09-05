/* Running-task indicator: 2 s poll of the read-only /api/tasks (auto-install + HMAC + build/test/install
 * + detached start/restart jobs). The SERVER owns visibility (done expires after 60 s; failed/unsafe
 * STAY) — the client renders whatever the server returns, keyed by kind+run_id. A `failed` item gets a
 * ✕ (dismiss); an `unsafe` JOB gets a Recover button — both POST (CSRF) to /api/tasks/{dismiss,recover}.
 * A running start/restart job also marks its stack ("starting…" badge on the Apps row / Dashboard card,
 * from the item's `stack`), and when such a job turns terminal the page reloads ONCE so the
 * server-rendered state becomes current. CSP-safe: createElement / textContent / setAttribute only. */
(function () {
  "use strict";
  var banner = document.getElementById("task-banner");
  if (!banner) return;
  var CSRF = banner.getAttribute("data-csrf") || "";
  var LABELS = {running: "running…", done: "finished", failed: "failed",
                unsafe: "UNSAFE — needs attention"};

  function keyOf(t) { return t.kind + "-" + t.run_id; }
  var reloading = false;
  // Attempts this TAB already reloaded for. Kept in sessionStorage so the reload itself does not
  // forget them (a `done` item stays in the feed for a minute, a `failed` one until dismissed) —
  // exactly one reload per attempt (without storage the guard falls back to this page load).
  var RELOADED_KEY = "lhpc_reloaded_starts";
  function reloadedSet() {
    try { return JSON.parse(sessionStorage.getItem(RELOADED_KEY) || "{}"); } catch (e) { return {}; }
  }
  function markReloaded(set) {                  // false when the tab cannot remember (no storage)
    try { sessionStorage.setItem(RELOADED_KEY, JSON.stringify(set)); return true; } catch (e) { return false; }
  }

  function isStartJob(t) { return t.kind === "job" && (t.op === "start" || t.op === "restart"); }

  // Mark every element that represents stack `sid`: the Apps row (#stackrow-<sid>) and any element
  // tagged data-stack="<sid>" (Dashboard cards). The badge sits beside the status badge.
  function markStarting(tasks) {
    var want = {};
    tasks.forEach(function (t) {
      if (isStartJob(t) && t.state === "running" && t.stack) want[t.stack] = t.op;
    });
    document.querySelectorAll(".badge-starting").forEach(function (b) {
      if (!want[b.getAttribute("data-starting-for")]) b.parentNode.removeChild(b);
    });
    Object.keys(want).forEach(function (sid) {
      var hosts = [];
      var row = document.getElementById("stackrow-" + sid);
      if (row) { var st = row.querySelector(":scope > summary .col-status"); if (st) hosts.push(st); }
      document.querySelectorAll("[data-stack=\"" + sid + "\"]").forEach(function (h) { hosts.push(h); });
      hosts.forEach(function (h) {
        if (h.querySelector(".badge-starting")) return;
        // The badge's own marker is a DIFFERENT attribute than the hosts' `data-stack`, so the
        // badge is never selected as a host on the next poll (it would nest a badge in a badge).
        var b = el("span", "badge badge-starting", want[sid] === "restart" ? "restarting…" : "starting…");
        b.setAttribute("data-starting-for", sid);
        h.appendChild(b);
      });
    });
  }

  // ONE reload per TERMINAL start/restart attempt this tab has not reloaded for yet — whether the
  // job turned terminal while we polled or had already finished before the first poll (the page
  // may have rendered mid-restart, with the stack stopped). The server-rendered rows/cards/links
  // become current; the attempt is remembered so the reloaded page does not reload again. The
  // remembered set is pruned to the attempts the feed still lists (the server expires them), so
  // it never grows without bound. On the DASHBOARD the signature poll (dash.js) already reloads
  // on the structural change a finished start causes; racing it here made two renders of a
  // finished start, so there this only records the attempt and leaves the reload to dash.js.
  // A terminal attempt changed what the page shows when it is `done`, or when it FAILED after it
  // was ADMITTED (the child passed its pre-mutation boundary: an owner or a dependent may have been
  // stopped, a restart's stop leg done — `admitted` is the marker's own flag). A failed attempt
  // that was never admitted changed nothing, and its red banner stays until dismissed — reloading
  // for it would fire on every fresh tab. A tab that cannot remember (no sessionStorage) never
  // reloads: a reload it could not record would repeat on every tick. And, like dash.js, a reload
  // waits while the operator is typing.
  var dashOwnsReload = !!document.querySelector(".radiogrid");
  function changedThePage(t) { return t.state === "done" || (t.state === "failed" && !!t.admitted); }
  function reloadOnFinish(tasks) {
    if (reloading) return;
    var old = reloadedSet(), seen = {}, fresh = false;
    tasks.forEach(function (t) {
      if (!isStartJob(t) || !t.attempt_id) return;
      if (old[t.attempt_id]) seen[t.attempt_id] = true;          // still listed: keep remembering
      else if (changedThePage(t)) { seen[t.attempt_id] = true; fresh = true; }
    });
    if (!fresh) { if (Object.keys(old).length !== Object.keys(seen).length) markReloaded(seen); return; }
    var a = document.activeElement;
    if (a && /^(SELECT|INPUT|TEXTAREA)$/.test(a.tagName)) return;   // typing: try again next tick
    if (!markReloaded(seen) || dashOwnsReload) return;
    reloading = true;
    window.location.reload();
  }

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
    markStarting(tasks);
    reloadOnFinish(tasks);
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
      if (hint) {                                   // defensive: a server row may omit it
        hint.textContent = t.hint || "";
        // Toggle the SAME class the server renders, not an inline style: one mechanism, and
        // nothing here re-introduces the style attribute the CSP blocks.
        hint.classList.toggle("is-empty", !t.hint);
      }
      row.querySelector(".ti-view").setAttribute("href", t.href || "#");

      // ✕ (dismiss) for failed; Recover for an unsafe JOB. Rebuild the trailing control on state change.
      var oldBtn = row.querySelector(".ti-close, .ti-recover");
      if (oldBtn) row.removeChild(oldBtn);
      if (t.state === "failed" && !t.no_dismiss) {
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
