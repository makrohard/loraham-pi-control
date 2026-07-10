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

  // ACCORDION: opening one MAIN header (a direct .stacklist child row, incl. the controller row)
  // auto-closes the others. Sub-panels inside a row are NOT direct children, so they never trip it.
  function attachAccordion() {
    document.querySelectorAll(".stacklist > .stackrow").forEach(function (row) {
      row.addEventListener("toggle", function () {
        if (!row.open) { return; }                 // react to USER OPEN only; close-toggles ignored
        document.querySelectorAll(".stacklist > .stackrow").forEach(function (o) {
          if (o !== row && o.open) { o.open = false; }
        });
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

  // --- restore -----------------------------------------------------------------------------------
  if ("scrollRestoration" in history) { history.scrollRestoration = "manual"; }

  var act = null;
  try { act = JSON.parse(sessionStorage.getItem(AKEY) || "null"); sessionStorage.removeItem(AKEY); }
  catch (e) { act = null; }

  var hashDetails = detailsForHash();

  // A SERVER/LINK-directed focus wins over action memory. Resolve exactly ONE target so at most one
  // relevant section opens (opening act AND a different forced target would briefly show two).
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

  if (forcedTarget) { openWithAncestors(forcedTarget); }
  else if (actEl) { openWithAncestors(actEl); }

  // --- scroll (after the open relayout changes page height) --------------------------------------
  requestAnimationFrame(function () {
    var flash = document.querySelector(".wrap > p.flash");
    // A server/link-directed focus (data-force-scroll = ?inst, a nested data-force-open = ?cfg/?dp,
    // or a nested hash link) beats the acted-section memory. `forced` kept for the JS test.
    var forced = forcedTarget;
    if (forced) { forced.scrollIntoView(); }
    else if (act) { window.scrollTo(0, flash ? 0 : (act.y || 0)); }     // action: top-if-message, else stay
    else if (hashDetails) { hashDetails.scrollIntoView(); }             // a link to a bare row
    else if (flash) { window.scrollTo(0, 0); }

    // Bind the accordion ONLY now, deferred one task past this frame. The <details> `toggle` event is
    // async, so the load-path's programmatic opens above queue toggles that would otherwise hit the
    // accordion and collapse a server-forced row. Binding after they have drained makes that
    // impossible — the listeners do not exist while those toggles fire.
    setTimeout(attachAccordion, 0);
  });

  // --- same-page hash navigation (e.g. the global footer "Update →" clicked while on /stacks) ------
  // The load logic runs once; a pure #-only link changes the hash WITHOUT a reload, so re-apply the
  // one-section model here: close everything, open only the target + ancestors, then scroll after the
  // relayout (rAF), exactly like the load path.
  window.addEventListener("hashchange", function () {
    var d = detailsForHash();
    if (!d) { return; }
    document.querySelectorAll("details").forEach(function (x) { x.open = false; });
    openWithAncestors(d);
    requestAnimationFrame(function () { d.scrollIntoView(); });
  });
})();
