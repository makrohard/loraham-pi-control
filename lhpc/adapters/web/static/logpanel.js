// LOGS-IN-TREE (v4 N2/N3): a log entry's closed panel on the Apps page. Opened the first time it fetches the LAST 30
// lines of its log (an RF log: the last 30 records, each its raw line, never decoded), and again on Reload or when
// its source changes; nothing while closed, no timer (the live tail is the full page's). The text is set with
// textContent; the polite status line outside it says what happened. Delegated on the document, so a Stacks body
// that arrives later (stacklazy.js) needs no wiring.
(function () {
  "use strict";
  var LINES = 30;

  function load(panel) {
    var pick = panel.querySelector(".logpanel-src");
    var url = pick ? pick.value : panel.getAttribute("data-src");
    var text = panel.querySelector(".logpanel-text");
    var status = panel.querySelector(".logpanel-status");
    panel.setAttribute("data-loaded", "1");
    fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) {
        var rows = panel.getAttribute("data-kind") === "records"
          ? (d.records || []).map(function (rec) { return String(rec.raw || ""); })
          : (d.lines || []).map(String);
        rows = rows.slice(-LINES);
        text.textContent = rows.join("\n");
        status.textContent = rows.length ? "updated" : "no entries yet";
      })
      .catch(function () {
        text.textContent = "";
        status.textContent = "could not load the log (reload or open the page)";
      });
  }

  document.addEventListener("toggle", function (e) {
    var panel = e.target;
    if (panel && panel.classList && panel.classList.contains("logpanel") && panel.open
        && !panel.hasAttribute("data-loaded")) load(panel);
  }, true);                                        // toggle does not bubble: caught on the way down
  document.addEventListener("click", function (e) {
    var btn = e.target && e.target.closest ? e.target.closest(".logpanel-reload") : null;
    if (btn) load(btn.closest(".logpanel"));       // a real button: focus stays on it while the text updates
  });
  document.addEventListener("change", function (e) {
    if (e.target && e.target.classList && e.target.classList.contains("logpanel-src")) load(e.target.closest(".logpanel"));
  });
})();
