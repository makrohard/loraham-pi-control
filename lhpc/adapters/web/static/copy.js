// Copy-to-clipboard for command boxes (.copybtn with data-copy=<pre id>).
// Same-origin, no inline handlers — CSP-compliant. Loaded on every page.
// init(root) re-runs on lhpc:bodyloaded so lazily-loaded stack bodies get wired too.
(function () {
  "use strict";

  function wire(btn) {
    btn.addEventListener("click", function () {
      var pre = document.getElementById(btn.getAttribute("data-copy"));
      if (!pre) return;
      var done = function () {
        btn.textContent = "✓ copied";
        setTimeout(function () { btn.textContent = "⧉ copy"; }, 1500);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(pre.textContent).then(done, function () {});
      } else {
        var r = document.createRange(); r.selectNodeContents(pre);
        var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
        try { document.execCommand("copy"); done(); } catch (e) {}
        sel.removeAllRanges();
      }
    });
  }

  function init(root) {
    (root || document).querySelectorAll(".copybtn").forEach(wire);
  }

  init();
  document.addEventListener("lhpc:bodyloaded", function (e) { init((e.detail || {}).root); });
})();
