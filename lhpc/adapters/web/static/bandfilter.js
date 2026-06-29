// In a param grid driven by a "radio" select (the daemon), show only the chosen
// band's per-band columns: radio=433 hides 868, radio=868 hides 433, both shows all.
(function () {
  "use strict";
  document.querySelectorAll("table.paramgrid").forEach(function (table) {
    var sel = table.querySelector('select[name$="radio"]');
    if (!sel) return;

    function apply() {
      var v = sel.value;
      table.querySelectorAll("[data-band]").forEach(function (cell) {
        var band = cell.getAttribute("data-band");
        var hide = (v === "433" || v === "868") && band !== v;
        cell.hidden = hide;
      });
    }
    sel.addEventListener("change", apply);
    apply();
  });
})();
