// F28 (his word): every banner that is the result of a user action has a ✕ (top right, the persistent notices' look).
// It is rendered hidden (no dead control without JavaScript); here it is shown, and a click removes its banner from the
// page. Nothing is stored: a result lives one page load.
(function () {
  "use strict";
  function arm(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll("button.bnrclose[hidden]"), function (b) {
      b.hidden = false;
    });
  }
  document.addEventListener("click", function (ev) {
    var b = ev.target && ev.target.closest && ev.target.closest("button.bnrclose");
    if (!b) return;
    var box = b.closest(".depnote");
    if (box) box.remove();
  });
  arm(document);
  document.addEventListener("lhpc:bodyloaded", function (ev) { arm(ev.detail && ev.detail.root); });   // lazy row bodies
})();
