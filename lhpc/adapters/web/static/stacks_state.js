// Stacks page: keep the collapsible tree's open/close state AND the scroll position across a button
// action. Every action is a real <form> POST that 302-redirects back and reloads the page fresh, so
// without this the whole tree collapses to the server default and the scroll jumps. We persist the
// open/close of every <details> and the scroll position in sessionStorage, restore them on load, and
// — if the top flash message would be off-screen — scroll to the top so it is seen.
//
// Keys are per-element PATHS (id when present, else index among sibling <details>), because the DOM
// reshapes as panels expand; index-only keying (see dash.js) would drift. State is captured from the
// LIVE DOM at navigation time, so any panel the user acted in is necessarily open — exact-restore
// never hides a confirm/job UI.
(function () {
  // Only run on the stacks page (its inline /self-update/apply re-render renders the same template).
  if (!document.querySelector(".stacklist")) { return; }

  var DKEY = "lhpc:stacks:details";   // { pathKey: 0|1 }
  var SKEY = "lhpc:stacks:scroll";    // window.scrollY

  function keyFor(el) {
    var parts = [];
    for (var node = el; node && node !== document.body; node = node.parentElement) {
      if (node.tagName !== "DETAILS") { continue; }
      if (node.id) {
        parts.unshift("#" + node.id);
      } else {
        var i = 0;
        for (var s = node.previousElementSibling; s; s = s.previousElementSibling) {
          if (s.tagName === "DETAILS") { i++; }
        }
        parts.unshift("d" + i);
      }
    }
    return parts.join("/");
  }

  function readMap() {
    try {
      var m = JSON.parse(sessionStorage.getItem(DKEY) || "null");
      return (m && typeof m === "object") ? m : {};
    } catch (e) { return {}; }
  }

  function saveDetails() {
    var map = {};
    document.querySelectorAll("details").forEach(function (el) {
      map[keyFor(el)] = el.open ? 1 : 0;
    });
    try { sessionStorage.setItem(DKEY, JSON.stringify(map)); } catch (e) { /* private mode */ }
  }

  function saveScroll() {
    try { sessionStorage.setItem(SKEY, String(window.scrollY || window.pageYOffset || 0)); }
    catch (e) { /* private mode */ }
  }

  // --- restore ---------------------------------------------------------------------------------
  if ("scrollRestoration" in history) { history.scrollRestoration = "manual"; }
  var map = readMap();
  document.querySelectorAll("details").forEach(function (el) {
    var k = keyFor(el);
    if (Object.prototype.hasOwnProperty.call(map, k)) { el.open = map[k] === 1; }
  });

  // SERVER-FORCED navigation WINS over the restored (possibly stale) map: the server opens rows
  // for ?open=<id>/?cfg=<id>/?dp=<id>/?inst=<id> and for active jobs and marks them data-force-open.
  // Force those elements — and every ancestor <details> — open, so a stale stored open=0 can't close
  // a bookmarked / redirected / job row. Runs AFTER the map restore so it always overrides it.
  document.querySelectorAll("details[data-force-open]").forEach(function (el) {
    for (var n = el; n && n !== document.body; n = n.parentElement) {
      if (n.tagName === "DETAILS") { n.open = true; }
    }
  });

  // After a layout pass (opened panels change page height), place the scroll. The decision MUST live
  // in here, after the force-open walk above has expanded the ancestors — scrollIntoView() run before
  // that relayout would measure the collapsed geometry and land short of the target.
  requestAnimationFrame(function () {
    // A server-forced SCROLL target (data-force-scroll, e.g. ?inst=<id> after a refused start) beats
    // both the saved scroll and the flash rule: the operator was sent to that panel on purpose, and
    // the panel repeats the reason in its own banner. Deliberately NOT keyed on data-force-open —
    // every action redirect sets open=<sid>, and those must still "stay where the page was".
    var forced = document.querySelector("[data-force-scroll]");
    if (forced) { forced.scrollIntoView(); return; }

    var saved = null;
    try { saved = sessionStorage.getItem(SKEY); } catch (e) { /* ignore */ }
    if (saved !== null) { window.scrollTo(0, parseInt(saved, 10) || 0); }
    // If an action produced a top flash and it is off-screen, scroll to the top so it is seen.
    var flash = document.querySelector(".wrap > p.flash");
    if (flash) {
      var r = flash.getBoundingClientRect();
      var visible = r.bottom > 0 && r.top < (window.innerHeight || document.documentElement.clientHeight);
      if (!visible) { window.scrollTo(0, 0); }
    }
  });

  // --- save triggers ---------------------------------------------------------------------------
  // Every toggle keeps the map current; pagehide snapshots the scroll right before we navigate away.
  document.addEventListener("toggle", function (e) {
    if (e.target && e.target.tagName === "DETAILS") { saveDetails(); }
  }, true);
  window.addEventListener("pagehide", function () { saveDetails(); saveScroll(); });
})();
