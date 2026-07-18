// Start-confirm "Stack parameters" panel: client-side Reset-to-defaults + a "save before apply?"
// prompt. The server always re-validates and enforces CALL/node; this is UX only.
(function () {
  "use strict";

  // "Reset to defaults": set every stack param back to its manifest default (data-default). Never
  // touches saved config — it just changes what this start submits. Present in every stack body, so
  // init(root) re-runs on lhpc:bodyloaded to wire lazily-loaded bodies too.
  function wireReset(btn) {
    btn.addEventListener("click", function () {
      var panel = btn.closest(".stackparams");
      if (!panel) return;
      panel.querySelectorAll("[data-default]").forEach(function (el) {
        var d = el.getAttribute("data-default") || "";
        if (el.type === "checkbox") {
          el.checked = ["1", "on", "true"].indexOf(d.toLowerCase()) !== -1;
        } else {
          el.value = d;
        }
      });
    });
  }
  function initResets(root) {
    (root || document).querySelectorAll(".sp-reset").forEach(wireReset);
  }
  initResets();
  document.addEventListener("lhpc:bodyloaded", function (e) { initResets((e.detail || {}).root); });

  // --- The rest is the start-confirm page only (the _params marker form) — a full page render, never
  // lazy-loaded, so it wires once and bails on the /stacks overview. ---
  var marker = document.querySelector('input[name="_params"]');
  var form = marker ? marker.form : null;
  if (!form) return;

  function normBool(v) {
    var s = String(v || "").toLowerCase();
    return (s === "1" || s === "on" || s === "true") ? "1" : "";
  }

  // Any stack/daemon param whose current value differs from its saved-config value (data-config).
  function anyChanged() {
    var els = form.querySelectorAll("[data-config]");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var cfg = el.getAttribute("data-config");
      var val, ref;
      if (el.type === "checkbox") { val = el.checked ? "1" : ""; ref = normBool(cfg); }
      else { val = el.value; ref = cfg; }
      if (String(val) !== String(ref)) return true;
    }
    return false;
  }

  // A Save/Reset button (not the main Apply) must never trigger the prompt.
  function isPanelSubmit(by) {
    if (!by) return false;
    if (by.name === "_save") return true;
    var c = by.className || "";
    return c.indexOf("dp-reset-inline") !== -1 || c.indexOf("sp-reset") !== -1;
  }

  function addHidden(name, value) {
    var el = form.querySelector('input[type=hidden][name="' + name + '"][data-injected]');
    if (!el) {
      el = document.createElement("input");
      el.type = "hidden"; el.name = name; el.setAttribute("data-injected", "1");
      form.appendChild(el);
    }
    el.value = value;
  }

  // Minimal 3-choice modal (Save+start / Start without saving / Cancel).
  var modal = document.createElement("div");
  modal.className = "modal-back";
  modal.innerHTML =
    '<div class="modal-box" role="dialog" aria-modal="true">' +
    '<p>You changed parameters. Save them to your config before starting?</p>' +
    '<div class="modal-btns">' +
    '<button type="button" class="act" data-choice="yes">Save &amp; start</button>' +
    '<button type="button" class="act" data-choice="no">Start without saving</button>' +
    '<button type="button" class="act" data-choice="cancel">Cancel</button>' +
    '</div></div>';
  document.body.appendChild(modal);

  var pendingSubmitter = null;
  function closeModal() { modal.classList.remove("open"); }
  modal.addEventListener("click", function (e) {
    if (e.target === modal) { closeModal(); return; }           // click backdrop = cancel
    var choice = e.target.getAttribute("data-choice");
    if (!choice) return;
    closeModal();
    if (choice === "cancel") return;
    if (choice === "yes") { addHidden("_save", "all"); addHidden("_save_then_start", "1"); }
    form.dataset.proceed = "1";
    form.requestSubmit(pendingSubmitter || undefined);          // fires submit -> FSK guard still runs
  });

  form.addEventListener("submit", function (e) {
    if (form.dataset.proceed === "1") { form.dataset.proceed = ""; return; }  // second pass: allow
    if (isPanelSubmit(e.submitter)) return;                     // Save/Reset: no prompt
    if (!anyChanged()) return;                                  // unchanged: ephemeral == config
    e.preventDefault();
    pendingSubmitter = e.submitter || null;
    modal.classList.add("open");
  });
})();
