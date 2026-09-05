// Daemon-parameter panel: an optional client-side guard (the server always validates; there is
// NO server-side FSK confirmation or rejection). Warn once, with OK/Cancel, before submitting when
// ANY MODE selector is FSK — that switches the radio off LoRa and breaks every LoRa stack on the
// band. Covers the Settings Save/Apply form (either band on a dual-band hardware setup). Reset
// buttons never warn; non-FSK values never warn.
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
    return /\/daemon-params\/reset$/.test(by.formAction || "");          // saved-profile Reset
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

  function init(root) {
    var scope = root || document;
    scope.querySelectorAll("form").forEach(wireForm);
  }

  init();
  document.addEventListener("lhpc:bodyloaded", function (e) { init((e.detail || {}).root); });
})();
