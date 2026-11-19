// UI-REORG s13 (L85): the ONE in-tree poll rule. An in-tree poller asks only while lhpcShown(el): el is in the page,
// el (when it is a <details>) and every <details> around it are open, and the tab is visible (a pill in a closed
// block's <summary> is not shown: dash.js hides it, F11). Used by daemoncfg.js, gps.js and dash.js; loaded by
// base.html before them.
window.lhpcShown = function (el) {
  if (!el || !el.isConnected || document.hidden) return false;
  for (var d = el.closest("details"); d; d = d.parentElement ? d.parentElement.closest("details") : null) {
    if (!d.open) return false;
  }
  return true;
};
