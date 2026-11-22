// "‹ Back" (a[data-back]) goes one step back in the browser history, as the browser's own Back does.
// A page opened with no history (a new tab, a bookmark) follows the link's href instead: Home. No inline handler (CSP).
(function () {
  "use strict";
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("a[data-back]") : null;
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (window.history.length > 1) {
      e.preventDefault();
      window.history.back();
    }
  });
})();
