// Transient flash notes (class "transient", e.g. a just-started GUI's connect hint):
// show them, then fade and remove after a few seconds — "show then hide". Non-transient
// flashes (warnings/errors) stay until the next navigation.
(function () {
  var SHOW_MS = 6000;   // visible time before fading
  var FADE_MS = 600;    // must match the CSS transition
  document.querySelectorAll(".flash.transient").forEach(function (el) {
    setTimeout(function () {
      el.classList.add("flash-hide");
      setTimeout(function () { el.remove(); }, FADE_MS);
    }, SHOW_MS);
  });
})();
