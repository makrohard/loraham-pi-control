// Daemon-parameter panel: an optional client-side guard (the server always validates; there is
// NO server-side FSK confirmation or rejection). Warn once, with OK/Cancel, before submitting when
// ANY MODE selector is FSK — that switches the radio off LoRa and breaks every LoRa stack on the
// band. Covers the saved-profile Save/Apply form AND the inline start-confirm form (either band in
// radio-mode both). Reset buttons never warn; non-FSK values never warn.
// init(root) re-runs on lhpc:bodyloaded so lazily-loaded stack bodies get wired too.
(function () {
  "use strict";

  function anyFskSelected(form) {
    var sels = form.querySelectorAll("select[data-mode-warn]");
    for (var i = 0; i < sels.length; i++) {
      if (sels[i].value === "FSK") return true;
    }
    return false;
  }

  function isResetSubmit(by) {
    if (!by) return false;
    return /\/daemon-params\/reset$/.test(by.formAction || "")           // saved-profile Reset
        || (by.className || "").indexOf("dp-reset-inline") !== -1;       // inline client Reset
  }

  function wireForm(form) {
    if (!form.querySelector("select[data-mode-warn]")) return;           // only forms with a MODE
    form.addEventListener("submit", function (e) {
      if (isResetSubmit(e.submitter)) return;
      if (anyFskSelected(form)
          && !window.confirm("MODE=FSK switches the radio off LoRa and will break every LoRa "
                             + "stack on this band. Continue?")) {
        e.preventDefault();
      }
    });
  }

  // Inline start-confirm "Reset to defaults": a CLIENT-SIDE reset of THIS launch's values back to
  // their defaults (data-dpdefault). It never touches the saved config — the reset values are just
  // what gets submitted with the start form and applied to the daemon for this launch.
  function wireReset(btn) {
    btn.addEventListener("click", function () {
      var panel = btn.closest(".dparams");
      if (!panel) return;
      panel.querySelectorAll("[data-dpdefault]").forEach(function (el) {
        el.value = el.getAttribute("data-dpdefault");
      });
    });
  }

  function init(root) {
    var scope = root || document;
    scope.querySelectorAll("form").forEach(wireForm);
    scope.querySelectorAll(".dp-reset-inline").forEach(wireReset);
  }

  init();
  document.addEventListener("lhpc:bodyloaded", function (e) { init((e.detail || {}).root); });
})();
