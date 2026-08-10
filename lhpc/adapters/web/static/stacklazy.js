// Lazy-load a stack's settings body on first expand. Progressive enhancement only: a forced-open
// row (?open=… / a flow) is already rendered server-side, and no-JS / a fetch failure falls back to
// the ?open= link in the placeholder — so the settings are ALWAYS reachable. Same-origin fetch, no
// inline handlers (CSP-compliant).
(function () {
  "use strict";

  function fallbackLink(details, ph) {
    var sid = (details.id || "").replace(/^stackrow-/, "");
    var a = ph.querySelector("noscript a");
    var href = a ? a.getAttribute("href")
                 : "/stacks?open=" + encodeURIComponent(sid) + "#stackrow-" + encodeURIComponent(sid);
    ph.removeAttribute("data-loading");
    ph.textContent = "";
    var p = document.createElement("p");
    p.className = "muted";
    p.appendChild(document.createTextNode("Could not load the settings. "));
    var link = document.createElement("a");
    link.setAttribute("href", href);
    link.textContent = "Open the full page";
    p.appendChild(link);
    ph.appendChild(p);
  }

  // Keep `el`'s summary at the same viewport Y across `mutate()` — a body arriving after the
  // click grows the row and could shift the header the user is looking at. Local twin of the
  // helper in stacks_state.js (the two files are deliberately independent; this one must work
  // even when stacks_state.js did not run). Measured AFTER the synchronous relayout, so only
  // the residual the browser's native scroll anchoring left is corrected.
  function pinDuring(el, mutate) {
    var s = el.querySelector(":scope > summary") || el;
    var before = s.getBoundingClientRect().top;
    mutate();
    var delta = s.getBoundingClientRect().top - before;
    if (delta) { window.scrollBy(0, delta); }
  }

  function loadBody(details) {
    var ph = details.querySelector(":scope > .lazy-body");
    if (!ph || ph.getAttribute("data-loading") || ph.getAttribute("data-loaded")) return;
    var url = ph.getAttribute("data-body-url");
    if (!url) return;
    ph.setAttribute("data-loading", "1");
    fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })
      .then(function (html) {
        var tmp = document.createElement("div");
        tmp.innerHTML = html;
        var parent = ph.parentNode;
        pinDuring(details, function () {
          while (tmp.firstChild) parent.insertBefore(tmp.firstChild, ph);
          parent.removeChild(ph);
        });
        // Tell the per-body enhancers (copy/bandfilter/dparams/daemoncfg/stackparams) to wire the
        // freshly-injected subtree. `details` is the root that now holds the new body.
        document.dispatchEvent(new CustomEvent("lhpc:bodyloaded", { detail: { root: details } }));
      })
      .catch(function () { fallbackLink(details, ph); });
  }

  function wire(details) {
    details.addEventListener("toggle", function () {
      if (details.open) loadBody(details);
    });
    if (details.open) loadBody(details);   // already open on load (state-restore/forced) — defensive
  }

  function init() {
    document.querySelectorAll("details.stackrow").forEach(wire);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
