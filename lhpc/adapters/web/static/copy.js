// Copy-to-clipboard for command boxes (.copybtn with data-copy=<pre id>).
// Same-origin, no inline handlers — CSP-compliant. Loaded on every page.
(function () {
  "use strict";
  document.querySelectorAll(".copybtn").forEach(function (btn) {
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
  });
})();
