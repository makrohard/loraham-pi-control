// Transient flash notes (class "transient", e.g. a just-started GUI's connect hint):
// show them, then fade and remove after a few seconds — "show then hide". Non-transient
// flashes (warnings/errors) stay until the next navigation.
(function () {
  var SHOW_MS = 6000;        // visible time before fading
  var LONG_SHOW_MS = 30000;  // "transient-long": boot warnings etc.
  var FADE_MS = 600;         // must match the CSS transition
  function arm(el, ms) {
    setTimeout(function () {
      el.classList.add("flash-hide");
      setTimeout(function () { el.remove(); }, FADE_MS);
    }, ms);
  }
  document.querySelectorAll(".flash.transient").forEach(function (el) {
    arm(el, SHOW_MS);
  });
  document.querySelectorAll(".flash.transient-long").forEach(function (el) {
    arm(el, LONG_SHOW_MS);
  });
})();
