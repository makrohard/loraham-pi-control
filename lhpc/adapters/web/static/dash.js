// Radio dashboard live updater. For each per-band column it polls the read-only
// daemon API and refreshes the RSSI meter, TX-mode, daemon badge and RX/TX feed.
// Same-origin only; no actions are taken — display refresh exclusively.
(function () {
  "use strict";
  var cols = document.querySelectorAll("[data-radio-band]");
  if (!cols.length) return;

  function set(id, text) {
    var el = document.getElementById(id);
    // Skip no-op writes so unchanged fields don't re-render (avoids flicker).
    if (el && text !== undefined && text !== null && el.textContent !== String(text)) {
      el.textContent = text;
    }
  }

  function poll(band) {
    fetch("/api/daemon/" + encodeURIComponent(band))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var badge = document.getElementById("rd-badge-" + band);
        if (badge) {
          badge.textContent = d.reachable ? "daemon live" : "daemon offline";
          badge.className = "badge badge-" + (d.reachable ? "running" : "stopped");
        }
        if (!d.reachable) return;
        var rssi = (d.channel && d.channel.LIVERSSI) || "";
        var meter = document.getElementById("rd-rssi-" + band);
        if (meter && rssi !== "") meter.value = rssi;
        set("rd-rssiv-" + band, rssi || "?");
        if (d.channel) {
          set("rd-cad-" + band, d.channel.CADSTATE || "?");
          set("rd-pktrssi-" + band, d.channel.PACKETRSSI || "?");
        }
        if (d.status) {
          if (d.status.TXMODE) { set("rd-txmode-" + band, d.status.TXMODE); set("rd-txmode2-" + band, d.status.TXMODE); }
          set("rd-tx-" + band, d.status.TX || "?");
          if (d.status.RADIO) set("rd-radio-" + band, d.status.RADIO);
          if (d.status.CADRSSI) set("rd-cadrssi-" + band, d.status.CADRSSI);
          if (d.status.CADWAIT) set("rd-cadwait-" + band, d.status.CADWAIT);
        }
        if (d.stats) {
          set("rd-rx-" + band, d.stats.RX || "?");
          set("rd-txok-" + band, d.stats.TXOK || "?");
          set("rd-txerr-" + band, d.stats.TXERR || "?");
          set("rd-uptime-" + band, (d.stats.UPTIME || "?") + " s");
        }
        var feed = document.getElementById("rd-feed-" + band);
        if (feed) {
          // Only rewrite when the content actually changed — replacing textContent
          // every tick re-renders the <pre> and makes it flicker.
          var next = (d.feed && d.feed.length)
            ? d.feed.join("\n") : "(no recent RX/TX activity)";
          if (feed.textContent !== next) feed.textContent = next;
        }
      })
      .catch(function () { /* transient; retry on next tick */ });
  }

  cols.forEach(function (col) {
    var band = col.getAttribute("data-radio-band");
    poll(band);
    setInterval(function () { poll(band); }, 3000);
  });

  // Auto-refresh the dashboard ONLY when its structural state changes (a stack
  // starts/stops, the daemon comes up/down, an interactive app is launched). We
  // poll a cheap signature and reload just on change — so the page doesn't reflow
  // (and the monitor windows don't flash) every tick. Live fields (RSSI/feed/…)
  // already update in place above. Poll faster while an interactive app is pending
  // so it flips to "running" quickly after the operator starts it in a terminal.
  try {
    var saved = JSON.parse(sessionStorage.getItem("dashDetails") || "null");
    sessionStorage.removeItem("dashDetails");
    if (Array.isArray(saved)) {
      document.querySelectorAll("details").forEach(function (el, i) {
        if (typeof saved[i] === "number") el.open = saved[i] === 1;
      });
    }
  } catch (e) { /* ignore */ }
  var grid = document.querySelector(".radiogrid");
  if (grid) {
    var sig = grid.getAttribute("data-dash-sig");
    var pending = grid.getAttribute("data-pending-interactive") === "1";
    setInterval(function () {
      if (document.hidden) return;
      fetch("/api/dash-signature")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d || d.sig === sig) return;          // nothing structural changed
          var a = document.activeElement;
          // Only genuine text-entry defers the reload. A clicked BUTTON keeps focus
          // long after the click and would veto every tick (stale badges for 30s+).
          var busy = a && /^(SELECT|INPUT|TEXTAREA)$/.test(a.tagName);
          var selecting = window.getSelection && String(window.getSelection()).length > 0;
          // An OPEN <details> (e.g. the always-open daemon Monitor) must NOT veto the
          // reload — that froze stale badges (a booting node never turned green while
          // a panel was open). Open/closed states are preserved across the reload.
          if (!busy && !selecting) {
            try {
              var states = [];
              document.querySelectorAll("details").forEach(function (el) {
                states.push(el.open ? 1 : 0);
              });
              sessionStorage.setItem("dashDetails", JSON.stringify(states));
            } catch (e) { /* private mode etc. */ }
            location.reload();
          }
        })
        .catch(function () { /* transient */ });
    }, pending ? 2000 : 4000);
  }
})();
