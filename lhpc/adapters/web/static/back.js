// "‹ Back" (a[data-back]) goes one step back in the browser history, as the browser's own Back does.
// A page with no entry before it (a new tab, a bookmark, or back at a tab's first entry with forward entries after
// it) follows the link's href instead: Home. navigation.canGoBack knows a backward entry; history.length counts the
// forward ones too, so it is only the fallback where the Navigation API is missing. No inline handler (CSP).
(function () {
  "use strict";
  function canGoBack() {
    var nav = window.navigation;
    return nav && typeof nav.canGoBack === "boolean" ? nav.canGoBack : window.history.length > 1;
  }
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("a[data-back]") : null;
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (canGoBack()) {
      e.preventDefault();
      window.history.back();
    }
  });
})();
