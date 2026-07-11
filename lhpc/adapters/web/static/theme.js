// Dark/light theme. Default to the OS scheme; remember the operator's explicit choice (localStorage).
// This file is loaded in <head> WITHOUT defer, so the `data-theme` attribute is set synchronously before
// the first paint — no flash of the wrong theme. CSP forbids inline scripts, so this must be a static file.
(function () {
  var KEY = "lhpc:theme";                 // "dark" | "light" | (absent -> follow OS)
  var root = document.documentElement;

  function stored() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }   // private mode
  }
  function systemDark() {
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }
  function resolve() {
    var s = stored();
    return (s === "dark" || s === "light") ? s : (systemDark() ? "dark" : "light");
  }
  function apply(theme) { root.setAttribute("data-theme", theme); }

  apply(resolve());                        // BEFORE paint

  // Follow live OS changes only while the operator has made no explicit choice.
  try {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    var onChange = function () { if (!stored()) { apply(systemDark() ? "dark" : "light"); } };
    if (mq.addEventListener) { mq.addEventListener("change", onChange); }
    else if (mq.addListener) { mq.addListener(onChange); }                  // older browsers
  } catch (e) { /* matchMedia unavailable */ }

  // Wire the corner toggle once the DOM (and the button) exist. Icons are pure CSS (keyed on data-theme).
  function wire() {
    var btn = document.querySelector(".theme-toggle");
    if (!btn) { return; }
    btn.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      apply(next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* private mode: session-only */ }
    });
  }
  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", wire); }
  else { wire(); }
})();
