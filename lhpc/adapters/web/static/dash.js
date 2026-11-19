// Radio dashboard live updater. For each radio card (W285/W286: one per radio, its band a property) it polls
// the read-only daemon API of that radio and refreshes the RSSI meter, TX-mode, daemon badge and RX/TX feed.
// The hardware list's daemon badges (`hw-badge-<rid>`, on /hardware and in the daemon's Hardware submenu, also
// when a Stacks body arrives later) are polled the same way, and so is the badge of every Monitor · rX / Live · rX
// block and of a daemon view's own page (UI-REORG s4: class dbadge + data-rid, no id). One poll per radio per page.
// State contract (this file owns it): per radio the last answer is cached, one lhpc:daemonstate event per answer
// ({rid, up, data}; daemoncfg.js acts on a change), a failed poll keeps the cache (the next one retries), and a block
// that arrives later reads the cache through window.lhpcDaemonState(rid) only when it is fresher than one poll.
// Same-origin only. The polling is read-only and, since 0.7.0, genuinely passive: it asks the
// daemon for channel data WITHOUT a CAD scan, because a scan destroys a frame in flight. The one
// action this file takes is the explicit "Scan now" button, which is a POST with a CSRF token.
(function () {
  "use strict";
  var cols = document.querySelectorAll("[data-radio-rid]");

  function set(id, text) {
    var el = document.getElementById(id);
    // Skip no-op writes so unchanged fields don't re-render (avoids flicker).
    if (el && text !== undefined && text !== null && el.textContent !== String(text)) {
      el.textContent = text;
    }
  }

  // A badge shows the answer `d` (UI-REORG s9: the server's ONE readiness -> label mapping), or, with no `d`, a neutral
  // "–" and no state class: a block pill that is not shown (its block closed, s13) never keeps a stale state.
  function paint(badge, d) {
    var text = d ? d.label : "–";
    if (badge.textContent !== text) badge.textContent = text;   // aria-live: announce changes only
    badge.className = "badge" + (d ? " badge-" + (d.ready ? "running" : (d.reachable ? "degraded" : "stopped")) : "")
      + (badge.classList.contains("dbadge") ? " dbadge" : "");
  }

  // Resolves to whether the poll succeeded: an HTTP or network error, an unreadable body or a throw
  // while rendering is a failure, which the caller answers with the slow interval.
  // A card's element ids end in its radio's id. A card without a radio (a stray band) is not polled: the daemon
  // views are per radio (/api/daemon/<rid>), and that card shows its server-rendered state.
  function poll(rid) {
    var key = rid;
    return fetch("/api/daemon/" + encodeURIComponent(rid))
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) {
        if (!d) return;
        cache[key] = { up: !!d.reachable, data: d, at: Date.now() };
        document.dispatchEvent(new CustomEvent("lhpc:daemonstate", { detail: { rid: key, up: !!d.reachable, data: d } }));
        var badges = Array.prototype.filter.call(document.querySelectorAll(".dbadge[data-rid]"),
                                                 function (b) { return b.getAttribute("data-rid") === key; });
        ["rd-badge-", "hw-badge-"].forEach(function (prefix) {
          var b = document.getElementById(prefix + key);
          if (b) badges.push(b);
        });
        badges.forEach(function (badge) {
          // neutral only in a closed block (not for a hidden tab: a full page's pill keeps the answer)
          paint(badge, !badge.classList.contains("dbadge") || !badge.closest("details:not([open])") ? d : null);
        });
        if (!d.reachable) return;
        var rssi = (d.channel && d.channel.LIVERSSI) || "";
        var meter = document.getElementById("rd-rssi-" + key);
        if (meter && rssi !== "") meter.value = rssi;
        set("rd-rssiv-" + key, rssi || "?");
        if (d.channel) {
          // CADSCAN=0 means NO CAD verdict was taken — this poll is deliberately passive so it
          // cannot cost reception. Never render CADSTATE as a verdict in that case: show a dash
          // and let the operator ask for a real measurement with "Scan now". PENDING is real
          // information (a received packet is waiting) and is shown as-is.
          var scanned = d.channel.CADSCAN === "1";
          var cad = d.channel.CADSTATE || "?";
          set("rd-cad-" + key, (scanned || cad === "PENDING") ? cad : "—");
          set("rd-pktrssi-" + key, d.channel.PACKETRSSI || "?");
        }
        if (d.status) {
          if (d.status.TXMODE) { set("rd-txmode-" + key, d.status.TXMODE); }
          set("rd-tx-" + key, d.status.TX || "?");
          if (d.status.RADIO) set("rd-radio-" + key, d.status.RADIO);
          if (d.status.CADRSSI) set("rd-cadrssi-" + key, d.status.CADRSSI);
          if (d.status.CADWAIT) set("rd-cadwait-" + key, d.status.CADWAIT);
        }
        if (d.stats) {
          set("rd-rx-" + key, d.stats.RX || "?");
          set("rd-txok-" + key, d.stats.TXOK || "?");
          set("rd-txerr-" + key, d.stats.TXERR || "?");
          set("rd-uptime-" + key, (d.stats.UPTIME || "?") + " s");
        }
        var feed = document.getElementById("rd-feed-" + key);
        if (feed) {
          // Only rewrite when the content actually changed — replacing textContent
          // every tick re-renders the <pre> and makes it flicker.
          var next = (d.feed && d.feed.length)
            ? d.feed.join("\n") : "(no recent RX/TX activity)";
          if (feed.textContent !== next) feed.textContent = next;
        }
      })
      .then(function () { return true; }, function () { return false; });
  }

  // The discipline of taskbanner.js, per radio: no timer while the radio is not eligible, never a second request while
  // one is still out (a slow daemon answer is not overlapped), and after a failure a fixed slow interval instead of the
  // normal one. UI-REORG s13: a radio is eligible while the tab is visible AND a Home radio card shows it (its
  // page-level poll) or at least one of its badges is shown (shown.js: every <details> around it open; a full
  // page's pill has none). Rechecked on any toggle, visibilitychange or lhpc:bodyloaded: eligible again -> one request at once.
  var POLL_MS = 3000, ERROR_MS = 15000, radios = {}, cache = {}, cards = {};
  window.lhpcDaemonState = function (rid) {
    var c = cache[rid];
    return c && Date.now() - c.at < POLL_MS ? c : null;
  };
  function eligible(rid) {
    if (document.hidden) return false;
    if (cards[rid]) return true;
    var hosts = Array.prototype.filter.call(document.querySelectorAll(".dbadge[data-rid]"),
                                            function (b) { return b.getAttribute("data-rid") === rid; });
    var hw = document.getElementById("hw-badge-" + rid);
    if (hw) hosts.push(hw);
    return hosts.some(window.lhpcShown);
  }
  function start(rid) {
    if (!rid || radios[rid]) return;           // one poller per radio, however many badges show it
    var r = radios[rid] = { timer: null, polling: false };

    function clearTimer() {
      if (r.timer !== null) { clearTimeout(r.timer); r.timer = null; }
    }
    function schedule(ms) {
      clearTimer();
      if (eligible(rid)) r.timer = setTimeout(r.tick, ms);
    }
    r.tick = function () {
      clearTimer();
      if (r.polling || !eligible(rid)) return;
      r.polling = true;
      poll(rid).then(function (ok) {
        r.polling = false;
        schedule(ok ? POLL_MS : ERROR_MS);
      });
    };
    r.recheck = function () {
      if (!eligible(rid)) clearTimer();
      else if (r.timer === null && !r.polling) r.tick();
    };
    r.tick();
  }
  function recheck() {
    if (!document.hidden) {                     // a hidden tab shows nothing: its pills keep their text
      Array.prototype.forEach.call(document.querySelectorAll(".dbadge[data-rid]"), function (b) {
        var c = window.lhpcDaemonState(b.getAttribute("data-rid"));
        if (!window.lhpcShown(b)) paint(b, null);
        else if (c) paint(b, c.data);           // shown again: a fresh answer, else the request that goes out now
      });
    }
    Object.keys(radios).forEach(function (rid) { radios[rid].recheck(); });
  }
  cols.forEach(function (col) {
    var rid = col.getAttribute("data-radio-rid") || "";
    cards[rid] = true;
    start(rid);
  });
  function listBadges(root) {
    Array.prototype.forEach.call(root.querySelectorAll('[id^="hw-badge-"]'), function (el) {
      start(el.id.slice("hw-badge-".length));
    });
    Array.prototype.forEach.call(root.querySelectorAll(".dbadge[data-rid]"), function (el) {
      start(el.getAttribute("data-rid"));
    });
  }
  listBadges(document);
  recheck();                                    // the pills of closed blocks: neutral from the start
  // A Stacks body loaded on first expand (stacklazy.js) may hold the hardware list or a Monitor/Live block.
  document.addEventListener("lhpc:bodyloaded", function (e) {
    listBadges((e.detail && e.detail.root) || document);
    recheck();
  });
  document.addEventListener("toggle", recheck, true);   // toggle does not bubble: caught on the way down
  document.addEventListener("visibilitychange", recheck);

  // Auto-refresh the dashboard ONLY when its structural state changes (a stack
  // starts/stops, the daemon comes up/down, an interactive app is launched). We
  // poll a cheap signature and reload just on change — so the page doesn't reflow
  // (and the monitor windows don't flash) every tick. Live fields (RSSI/feed/…)
  // already update in place above. Poll faster while an interactive app is pending
  // so it flips to "running" quickly after the operator starts it in a terminal.
  // NOTE: this block (and the restore above it) runs even with ZERO radio columns —
  // the System box and the signature reload exist on a radio-less dashboard too.
  //
  // <details> open states persist across the reload STRICTLY BY ELEMENT ID: the reload
  // fires exactly when the details population may have changed (radio-mode flip added or
  // removed monitors), so index-keyed state silently opened the wrong panels — e.g. it
  // could pop the collapsed System box open and start its polling unrequested. Elements
  // without an id are deliberately not persisted; anything but an id-keyed object is
  // ignored and removed.
  try {
    var saved = JSON.parse(sessionStorage.getItem("dashDetails") || "null");
    sessionStorage.removeItem("dashDetails");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) {
      document.querySelectorAll("details").forEach(function (el) {
        if (el.id && typeof saved[el.id] === "number") el.open = saved[el.id] === 1;
      });
    }
  } catch (e) { /* ignore */ }
  var grid = document.querySelector("[data-dash-sig]");
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
              var states = {};
              document.querySelectorAll("details").forEach(function (el) {
                if (el.id) states[el.id] = el.open ? 1 : 0;   // id-keyed ONLY (see restore above)
              });
              sessionStorage.setItem("dashDetails", JSON.stringify(states));
            } catch (e) { /* private mode etc. */ }
            location.reload();
          }
        })
        .catch(function () { /* transient */ });
    }, pending ? 2000 : 10000);
  }

  // "Scan now": ONE real CAD measurement, on explicit click only.
  //
  // POST + CSRF, never a GET: this genuinely changes radio state (it enters CAD and can discard
  // an arriving frame), and this console's contract is that GET is read-only. A GET here could be
  // fired by a refresh, prefetch or retry — which is exactly the accident this whole change undoes.
  Array.prototype.forEach.call(
    document.querySelectorAll("[data-scan-rid]"), function (btn) {
      btn.addEventListener("click", function () {
        var rid = btn.getAttribute("data-scan-rid") || "";
        var cell = document.getElementById("rd-cad-" + rid);
        var token = btn.getAttribute("data-csrf") || "";
        if (!rid || !token) return;
        var body = new URLSearchParams();
        body.set("_csrf", token);
        btn.disabled = true;
        if (cell) cell.textContent = "scanning…";
        fetch("/api/daemon/" + encodeURIComponent(rid) + "/scan",
              { method: "POST", cache: "no-store", body: body })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) {
            if (cell) {
              cell.textContent = (d && d.channel && d.channel.CADSTATE) ? d.channel.CADSTATE : "?";
            }
          })
          .catch(function () { if (cell) cell.textContent = "?"; })
          .then(function () { btn.disabled = false; });
      });
    });

  // Webserver box open-state persists across ORDINARY reloads too (the id-keyed sessionStorage
  // restore above only survives dash.js's own signature reloads). Server renders it collapsed;
  // we reopen if the operator left it open. Mirrors the System box's localStorage contract.
  (function () {
    var box = document.getElementById("wsbox");
    if (!box) return;
    var KEY = "lhpc:wsbox";
    try { if (localStorage.getItem(KEY) === "1" && !box.open) box.open = true; }
    catch (e) { /* private mode */ }
    box.addEventListener("toggle", function () {
      try { localStorage.setItem(KEY, box.open ? "1" : "0"); } catch (e) { /* private mode */ }
    });
  })();
})();
