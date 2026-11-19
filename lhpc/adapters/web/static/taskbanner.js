/* Running-task indicator: polls the read-only /api/tasks — 2 s while a job is RUNNING, 15 s otherwise
 * (auto-install + HMAC + build/test/install
 * + detached start/restart jobs). The SERVER owns visibility (done expires after 60 s; failed/unsafe
 * STAY) — the client renders whatever the server returns, keyed by kind+run_id. A `failed` item gets a
 * ✕ (dismiss); an `unsafe` JOB gets a Recover button — both POST (CSRF) to /api/tasks/{dismiss,recover}.
 * Every task of a stack marks the stack's main node (UI-REORG s14, marks() below), and when a
 * start/restart job turns terminal the page reloads ONCE so the server-rendered state becomes current. CSP-safe: template clones / textContent / setAttribute only. */
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

  // UI-REORG s14: every task of a stack (an item with `stack` + `op`) marks that stack's main node -- the Apps row's
  // summary status (#stackrow-<sid>), every element tagged data-stack="<sid>" or "<sid>@<rid>" (Home cards) --
  // "⏳ <op>" while running, "<op> failed|unsafe" until dismissed, gone when done; a running job also has its body
  // line in the stack's .jobruns. One reconcile per task, keyed by keyOf(t), from this one poll; an item without a
  // stack stays in the banner only. A mark carries data-task, never data-stack (it is never taken for a host).
  var lastTasks = [];
  function reconcile(host, want, make) {
    var keep = {};
    want.forEach(function (t) { keep[keyOf(t)] = true; });
    host.querySelectorAll(":scope > [data-task]").forEach(function (m) {
      if (!keep[m.getAttribute("data-task")]) m.remove();
    });
    want.forEach(function (t) {
      var m = make(t), old = host.querySelector(':scope > [data-task="' + keyOf(t) + '"]');
      m.setAttribute("data-task", keyOf(t));
      if (!old) host.appendChild(m);
      else if (old.outerHTML !== m.outerHTML) host.replaceChild(m, old);   // a state change: new text
    });
  }
  function mark(t) {
    return el("span", "badge " + (t.state === "running" ? "badge-starting" : "badge-failed"),
              t.state === "running" ? "⏳ " + t.op : t.op + " " + t.state);
  }
  function jobLine(t) {
    var p = el("p", "jobrun", "⏳ " + t.op + " running (" + t.target + ") — ");
    var a = el("a", "", "open log");
    a.setAttribute("href", "/logs/" + encodeURIComponent(t.target) + "?job=" + encodeURIComponent(t.log));
    p.appendChild(a);
    return p;
  }
  function marks(tasks) {
    lastTasks = tasks;
    var by = {};
    tasks.forEach(function (t) {
      if (t.stack && t.op && t.state !== "done") (by[t.stack] = by[t.stack] || []).push(t);
    });
    var of = function (sid) { return by[sid] || []; };
    document.querySelectorAll("[id^='stackrow-'] > summary .col-status").forEach(function (h) {
      reconcile(h, of(h.closest("[id^='stackrow-']").id.slice("stackrow-".length)), mark);
    });
    document.querySelectorAll("[data-stack]").forEach(function (h) {
      reconcile(h, of(h.getAttribute("data-stack").split("@")[0]), mark);
    });
    document.querySelectorAll(".jobruns[data-jobs-for]").forEach(function (h) {
      reconcile(h, of(h.getAttribute("data-jobs-for")).filter(function (t) {
        return t.kind === "job" && t.state === "running";
      }), jobLine);
    });
  }
  document.addEventListener("lhpc:bodyloaded", function () { marks(lastTasks); });   // a lazy body gets its lines

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
  var dashOwnsReload = !!document.querySelector("[data-dash-sig]");
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

  // Every row is a clone of the server's inert <template> row (_task_banner.html): this script builds no row
  // markup of its own; it fills the fields and toggles the ✕ (failed, or unsafe of every kind: UX, X5) and
  // Recover (an unsafe JOB) with `hidden`. The ✕ only hides the item (X8).
  var template = document.getElementById("task-row-template");

  function render(tasks) {
    tasks = tasks || [];
    marks(tasks);
    reloadOnFinish(tasks);
    var wanted = {};
    tasks.forEach(function (t) { wanted[keyOf(t)] = true; });
    Array.prototype.slice.call(banner.children).forEach(function (c) {
      if (!wanted[c.getAttribute("data-key")]) c.remove();
    });
    tasks.forEach(function (t) {
      var key = keyOf(t), row = null, i;
      for (i = 0; i < banner.children.length; i++) {
        if (banner.children[i].getAttribute("data-key") === key) { row = banner.children[i]; break; }
      }
      if (!row) {
        if (!template) return;                      // no template on this page: nothing to clone
        row = template.content.firstElementChild.cloneNode(true);
        banner.appendChild(row);
      }
      row.setAttribute("data-key", key);
      row.setAttribute("data-kind", t.kind || "");
      row.setAttribute("data-run", t.run_id || "");
      row.setAttribute("data-attempt", t.attempt_id || "");
      row.className = "depnote taskitem taskitem-" + t.state;
      row.querySelector(".ti-label").textContent = t.label || "";
      row.querySelector(".ti-state").textContent = LABELS[t.state] || t.state || "";
      var hint = row.querySelector(".ti-hint");
      hint.textContent = t.hint || "";
      // Toggle the SAME class the server renders, not an inline style: one mechanism, and
      // nothing here re-introduces the style attribute the CSP blocks.
      hint.classList.toggle("is-empty", !t.hint);
      row.querySelector(".ti-view").setAttribute("href", t.href || "#");
      row.querySelector(".dismiss").hidden = !(t.state === "unsafe" || (t.state === "failed" && !t.no_dismiss));
      row.querySelector(".ti-recover").hidden = !(t.state === "unsafe" && t.kind === "job");
    });
  }

  // One listener for every row's ✕ and Recover, server-rendered or cloned: the row's data-* name its task.
  banner.addEventListener("click", function (e) {
    var b = e.target.closest(".ti-close, .ti-recover");
    if (!b) return;
    var row = b.closest("[data-key]");
    var t = {kind: row.getAttribute("data-kind"), run_id: row.getAttribute("data-run"),
             attempt_id: row.getAttribute("data-attempt")};
    if (b.classList.contains("ti-recover")) post("/api/tasks/recover", t).then(tick);
    else post("/api/tasks/dismiss", t).then(function (r) { if (r.ok) { row.remove(); tick(); } });   // its mark: at once
  });

  // Two speeds, because the feed is rarely interesting. FAST only while something is RUNNING;
  // everything else — an empty feed, or one holding only terminal items — polls slowly. That
  // distinction matters: the server keeps `failed`/`unsafe` items until they are dismissed or
  // recovered, so "poll fast whenever the feed is non-empty" would poll every 2 s forever after a
  // single failure. A fixed slow interval, not escalating backoff: deterministic, and it caps how
  // late a job started from another device can appear.
  var FAST_MS = 2000, SLOW_MS = 15000;
  var timer = null, polling = false;

  function clearTimer() {
    if (timer !== null) { clearTimeout(timer); timer = null; }
  }

  function schedule(ms) {
    clearTimer();
    // Hidden page: no timer at all. MDN's own example for the Page Visibility API is a dashboard
    // that should stop polling when it is not visible; browsers also throttle background timers,
    // so this mostly removes work the browser would already have slowed.
    if (reloading || document.hidden) return;
    timer = setTimeout(tick, ms);
  }

  function tick() {
    clearTimer();
    // A single in-flight guard. "Arm the next timer only after this fetch settles" is NOT enough on
    // its own: tick() is also reachable from the Recover button and from becoming visible, either
    // of which can fire while a request is already outstanding. A call that arrives mid-flight does
    // nothing — the in-flight request finishes and schedules normally.
    if (polling) return;
    polling = true;
    fetch("/api/tasks", {cache: "no-store"})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var tasks = (d && d.tasks) || [];
        render(tasks);
        var running = tasks.some(function (t) { return t.state === "running"; });
        polling = false;
        schedule(running ? FAST_MS : SLOW_MS);
      })
      .catch(function () {            // transient: keep the slow cadence, never spin on failure
        polling = false;
        schedule(SLOW_MS);
      });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { clearTimer(); return; }
    tick();                           // one immediate refresh on return, then normal scheduling
  });

  tick();
})();
