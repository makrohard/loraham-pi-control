// In a param grid driven by a "radio" select, show only the chosen radio's per-radio cells: a cell marked
// data-radio="rN" hides unless rN is chosen; a choice that names no radio (all, a band) shows every cell.
// W2 (WEB-LANE-PLAN-v5: the band attribute became data-radio, G1-W2-RE): the filter, keyed by the
// selected radio. init(root) re-runs on lhpc:bodyloaded so lazily-loaded stack bodies get wired too.
(function () {
  "use strict";

  function wire(table) {
    var sel = table.querySelector('select[name$="radio"]');
    if (!sel) return;
    function apply() {
      var v = sel.value;
      table.querySelectorAll("[data-radio]").forEach(function (cell) {
        cell.hidden = /^r[0-9]+$/.test(v) && cell.getAttribute("data-radio") !== v;
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
