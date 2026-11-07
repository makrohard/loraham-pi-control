/* W203: the busy state of a confirm form. A confirmed stop (or clean, uninstall, …) runs inside the
 * POST, so without this the page sits unchanged until the action ends and looks frozen. On submit the
 * button is disabled (no second click) and shows its data-busy-label; the response replaces the page.
 * The button has no name, so disabling it drops nothing from the POST. */
(function () {
  "use strict";
  document.querySelectorAll("form[data-busy-form]").forEach(function (form) {
    form.addEventListener("submit", function (ev) {
      if (ev.defaultPrevented) return;
      var btn = form.querySelector("button[type=submit][data-busy-label]");
      if (!btn) return;
      btn.textContent = btn.getAttribute("data-busy-label");
      btn.disabled = true;
      form.setAttribute("aria-busy", "true");
    });
  });
})();
