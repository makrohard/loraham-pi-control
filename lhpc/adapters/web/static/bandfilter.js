// In a param grid driven by a "radio" select (the daemon), show only the chosen
// band's per-band columns: radio=433 hides 868, radio=868 hides 433, both shows all.
// init(root) re-runs on lhpc:bodyloaded so lazily-loaded stack bodies get wired too.
(function () {
  "use strict";

  function wire(table) {
    var sel = table.querySelector('select[name$="radio"]');
    if (!sel) return;
    function apply() {
      var v = sel.value;
      table.querySelectorAll("[data-band]").forEach(function (cell) {
        var band = cell.getAttribute("data-band");
        cell.hidden = (v === "433" || v === "868") && band !== v;
      });
    }
    sel.addEventListener("change", apply);
    apply();
  }

  function init(root) {
    (root || document).querySelectorAll("table.paramgrid").forEach(wire);
  }

  init();
  document.addEventListener("lhpc:bodyloaded", function (e) { init((e.detail || {}).root); });
})();
