// Stacks page open/close + scroll behaviour.
//
// Model: DEFAULT EVERYTHING CLOSED, at most ONE relevant section open at a time.
//  - The server renders every <details> closed except the ones a ?param targets (data-force-open,
//    plus data-force-scroll for ?inst) and active-job rows. This script NEVER closes anything — it
//    only opens the single relevant target, so "close all others" is free.
//  - A LINK to a section (its #anchor, or a ?cfg/?dp/?inst param) → open only that section (+ the
//    parents needed to reach it) and JUMP to it.
//  - An ACTION button (a form POST that redirects back) → reopen exactly the section the button sat
//    in (innermost <details> + ancestors) and STAY there — or jump to the top if a flash appeared.
//    A server-directed focus (data-force-scroll, a nested data-force-open, or a nested hash target)
//    WINS over that action memory, so a stale POST memory can never steal focus.
//
// Keys are per-element PATHS (id when present, else index among sibling <details>) so a remembered
// section resolves after the server re-renders the same structure.
(function () {
  if (!document.querySelector(".stacklist")) { return; }   // only the stacks page (incl. its re-renders)

  var AKEY = "lhpc:stacks:act";        // one-shot: { k: pathKey, y: scrollY } of the acted section
  var OKEY = "lhpc:stacks:open";       // durable per-tab: keyFor() of every open <details>, for reload restore

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

  function openWithAncestors(el) {
    for (var n = el; n && n !== document.body; n = n.parentElement) {
      if (n.tagName === "DETAILS") { n.open = true; }
    }
  }

  // Durable open/close memory so a reload restores exactly what was open. keyFor() paths survive the
  // server's re-render of the same structure.
  function allDetails() {
    return Array.prototype.slice.call(document.querySelectorAll(".stacklist details"));
  }
  function saveOpenSet() {
    try {
      var keys = [];
      allDetails().forEach(function (d) { if (d.open) { keys.push(keyFor(d)); } });
      sessionStorage.setItem(OKEY, JSON.stringify(keys));
    } catch (e) { /* private mode */ }
  }
  function restoreOpenSet() {
    var keys = null;
    try { keys = JSON.parse(sessionStorage.getItem(OKEY) || "null"); } catch (e) { keys = null; }
    if (!keys || !keys.length) { return false; }
    var set = {};
    keys.forEach(function (k) { set[k] = true; });
    var opened = false;
    allDetails().forEach(function (d) { if (set[keyFor(d)]) { d.open = true; opened = true; } });
    return opened;
  }

  // A sub-panel (has a <details> ancestor) vs a top-level row (#stackrow-*, #controller-row).
  function nested(el) {
    return !!(el && el.parentElement && el.parentElement.closest("details"));
  }

  // The <details> named by location.hash (the element itself, or its nearest <details>), or null.
  function detailsForHash() {
    if (location.hash.length <= 1) { return null; }
    var el = null;
    try { el = document.getElementById(decodeURIComponent(location.hash.slice(1))); }
    catch (e) { el = document.getElementById(location.hash.slice(1)); }
    if (!el) { return null; }
    return (el.tagName === "DETAILS") ? el : el.closest("details");
  }

  // Programmatic opens (load restore, hashchange) also fire the native <details> `toggle` event, which
  // would trip the accordion below and undo them. `openTarget` marks such opens so the accordion ignores
  // them; the load path is additionally protected by deferred binding (see setTimeout(attachAccordion)).
  var programmaticOpen = false;
  function openTarget(el) {
    programmaticOpen = true;
    try { openWithAncestors(el); }
    finally { setTimeout(function () { programmaticOpen = false; }, 0); }
  }

  // ACCORDION: opening one MAIN header (a direct .stacklist child row, incl. the controller row)
  // auto-closes the OTHER rows AND their sub-panels, and resets the freshly opened row's OWN sub-panels
  // to closed (a native <details> keeps children's open state across a close/reopen, so we clear it
  // ourselves). The FIRST submenu level is an accordion too (see attachSubAccordion); deeper levels stay
  // independently openable. Only the top-level `.stackrow` rows are bound here; each now sits inside a
  // `.stackrow-wrap`, so this is a descendant selector, not a direct child.
  function attachAccordion() {
    document.querySelectorAll(".stacklist .stackrow").forEach(function (row) {
      row.addEventListener("toggle", function () {
        if (!row.open) { return; }                 // react to USER OPEN only; close-toggles ignored
        if (programmaticOpen) { return; }           // a scripted open (restore/hashchange) — leave it be
        document.querySelectorAll(".stacklist .stackrow").forEach(function (o) {
          if (o === row) { return; }
          if (o.open) { o.open = false; }
          o.querySelectorAll("details").forEach(function (s) { s.open = false; });   // + their sub-panels
        });
        row.querySelectorAll("details").forEach(function (s) { s.open = false; });   // own subs start closed
      });
    });
  }

  // SUB-PANEL ACCORDION (EVERY nested level): opening any `.advcfg` sub-panel closes its SIBLING `.advcfg`
  // panels (those sharing its parent). So each level is single-open — the first submenu (Install / Info /
  // Settings / Webserver, and the controller's Update / System deps / Webserver), the level below it (e.g.
  // the Webserver panel's Settings / Monitor / Certificates), and any deeper. Only sibling `.advcfg` are
  // closed, so opening a child never collapses its own ancestors.
  function attachSubAccordion() {
    document.querySelectorAll(".stackrow-body .advcfg").forEach(function (sub) {
      sub.addEventListener("toggle", function () {
        if (!sub.open) { return; }                 // react to USER OPEN only
        if (programmaticOpen) { return; }           // scripted open (restore/hashchange) — leave it be
        var sibs = sub.parentElement ? sub.parentElement.children : [];
        for (var i = 0; i < sibs.length; i++) {
          var o = sibs[i];
          if (o !== sub && o.tagName === "DETAILS" && o.classList.contains("advcfg") && o.open) {
            o.open = false;
          }
        }
      });
    });
  }

  // --- capture the acted section on ANY form submit inside a <details> (broad root: whole page) ---
  document.addEventListener("submit", function (e) {
    var btn = e.submitter || document.activeElement;
    var d = btn && btn.closest ? btn.closest("details") : null;
    if (!d && e.target && e.target.closest) { d = e.target.closest("details"); }
    if (!d) { return; }                                    // not inside a section -> nothing to remember
    try {
      sessionStorage.setItem(AKEY, JSON.stringify({ k: keyFor(d),
        y: window.scrollY || window.pageYOffset || 0 }));
    } catch (e2) { /* private mode */ }
  }, true);

  // Persist the open set on EVERY <details> toggle (user or programmatic — incl. this load's restore and
  // the accordion's programmatic closes). `toggle` doesn't bubble, so listen in the capture phase. The
  // full-set write is idempotent, so the last coalesced toggle always leaves the correct set stored.
  document.addEventListener("toggle", saveOpenSet, true);

  // --- restore -----------------------------------------------------------------------------------
  if ("scrollRestoration" in history) { history.scrollRestoration = "manual"; }

  var act = null;
  try { act = JSON.parse(sessionStorage.getItem(AKEY) || "null"); sessionStorage.removeItem(AKEY); }
  catch (e) { act = null; }

  var hashDetails = detailsForHash();

  // A server/link-directed focus (?inst/?cfg/?dp/?comp or a nested hash). Resolved first; the acted
  // section (below) takes precedence on a real action-return, this is the fallback for plain links.
  var forcedTarget = document.querySelector("[data-force-scroll]");
  if (!forcedTarget) {
    var fdo = document.querySelectorAll("details[data-force-open]");
    for (var i = 0; i < fdo.length; i++) { if (nested(fdo[i])) { forcedTarget = fdo[i]; break; } }
  }
  if (!forcedTarget && hashDetails && nested(hashDetails)) { forcedTarget = hashDetails; }

  var actEl = null;
  if (act && act.k) {
    var all = document.querySelectorAll("details");
    for (var j = 0; j < all.length; j++) { if (keyFor(all[j]) === act.k) { actEl = all[j]; break; } }
  }

  // A new flash on top means "a message appeared" -> stay at the top instead of scrolling to the spot.
  var flash = document.querySelector(".wrap > p.flash");

  // Resolve ONE section to open. An ACTION return (a form POST that redirected back, evidenced by a flash
  // or a server anchor) reopens exactly the section the button sat in (actEl) — more precise than the
  // server's redirect hash (e.g. per-stack Webserver Apply redirects to the controller #webserver-row,
  // but the button lived in #stack-webserver-<id>). A plain nav carrying a STALE post memory (no flash,
  // no anchor — e.g. after install/build/test diverted to the log page) is ignored. Otherwise a link/
  // server-directed focus wins. Failing everything, the saved open set is restored (plain reload).
  var actionReturn = !!(act && (flash || forcedTarget || hashDetails));
  var target = (actEl && actionReturn) ? actEl : (forcedTarget || hashDetails);

  // Precedence that preserves the single-main-row model:
  //  - An EXPLICIT target (action-return / link / hash) WINS and replaces the saved set — open only its
  //    chain, so a link to another row can never leave two main rows open. The toggle-save above then
  //    re-persists exactly that chain.
  //  - Only a plain reload (no explicit target) restores the saved open set.
  if (!target) { restoreOpenSet(); }
  if (target) { openWithAncestors(target); }

  // --- scroll (after the open relayout changes page height) --------------------------------------
  requestAnimationFrame(function () {
    if (flash) { window.scrollTo(0, 0); }             // message on top -> stay on top (section already open)
    else if (target) { target.scrollIntoView(); }     // no message -> jump to the opened section

    // Bind the accordions ONLY now, deferred one task past this frame. The <details> `toggle` event is
    // async, so the load-path's programmatic opens above queue toggles that would otherwise hit the
    // accordion and collapse a server-forced row. Binding after they have drained makes that
    // impossible — the listeners do not exist while those toggles fire.
    setTimeout(function () { attachAccordion(); attachSubAccordion(); }, 0);
  });

  // --- same-page hash navigation (e.g. the global footer "Update →" clicked while on /stacks) ------
  // The load logic runs once; a pure #-only link changes the hash WITHOUT a reload, so re-apply the
  // one-section model here: close everything, open only the target + ancestors, then scroll after the
  // relayout (rAF), exactly like the load path.
  window.addEventListener("hashchange", function () {
    var d = detailsForHash();
    if (!d) { return; }
    document.querySelectorAll("details").forEach(function (x) { x.open = false; });
    openTarget(d);     // guarded: the accordion is already bound here, so mark this as a scripted open
    requestAnimationFrame(function () { d.scrollIntoView(); });
  });
})();
