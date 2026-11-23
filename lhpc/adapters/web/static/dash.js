// Radio dashboard live updater. For each radio card (W285/W286: one per radio, its band a property) it polls
// the read-only daemon API of that radio and refreshes the RSSI meter, TX-mode, daemon badge and RX/TX feed.
// The hardware list's daemon badges (`hw-badge-<rid>`, on /hardware and in the daemon's Hardware submenu, also
// when a Stacks body arrives later) are polled the same way, and so is the badge of every Monitor / Live · rX
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

  // A badge shows the answer `d` (UI-REORG s9: the server's ONE readiness -> label mapping); with no `d` it is hidden
  // (F11): a block pill that is not shown (its block closed, s13) never shows a stale or load-time state.
  function paint(badge, d) {
    badge.hidden = !d;
    if (!d) return;
    if (badge.textContent !== d.label) badge.textContent = d.label;   // aria-live: announce changes only
    badge.className = "badge badge-" + (d.ready ? "running" : (d.reachable ? "degraded" : "stopped"))
      + (badge.classList.contains("dbadge") ? " dbadge" : "");
  }

  // the Monitor's RX / TX pill. TX while STATUS TX=1, or while this radio's TXOK+TXERR grew since
  // its previous poll (a frame sent between two polls). The first valid sample is the baseline; a lower total is a
  // reset (new baseline, no TX); missing or non-numeric STATS never count as 0: they drop the baseline, so the next
  // valid sample is a new one. One baseline per radio. Neither STATUS TX nor a valid count: hidden.
  var txtotal = {};
  function txcount(stats) {
    var ok = stats && stats.TXOK, err = stats && stats.TXERR;
    return /^[0-9]+$/.test(ok || "") && /^[0-9]+$/.test(err || "") ? Number(ok) + Number(err) : null;
  }
  function txpill(key, status, stats) {
    var n = txcount(stats), prev = txtotal[key];
    var grew = n !== null && prev !== undefined && prev !== null && n > prev;
    txtotal[key] = n;
    var pill = document.getElementById("rd-txpill-" + key);
    if (!pill) return;
    var tx = status ? status.TX : undefined;
    pill.hidden = tx === undefined && n === null;
    if (pill.hidden) return;
    var on = tx === "1" || grew;
    if (pill.textContent !== (on ? "TX" : "RX")) pill.textContent = on ? "TX" : "RX";
    pill.className = "badge txpill " + (on ? "tx-on" : "badge-running");
  }

  // the FREQ / PWR / BW / SF / SYNC line. A 1.5.0+ daemon (its STATUS carries RADIOERR=,
  // the marker daemon_control.read_radio keys on) is read ONLY through `radio` (GET RADIO): its values while its
  // RADIO=READY, else (FAILED, UNPLUGGED, any other, {} or absent) "-" for all, never STATUS. An older daemon:
  // STATUS FREQ / POWER / SF (BW and SYNC "-": STATUS has none). A missing or "-" value is "-", also after an earlier
  // value.
  var PARAMS = [["freq", "FREQ"], ["pwr", "POWER"], ["bw", "BW"], ["sf", "SF"], ["sync", "SYNC"]];
  function params(key, d) {
    var status = d.status || {}, radio = d.radio || {};
    var src = ("RADIOERR" in status) ? (radio.RADIO === "READY" ? radio : {}) : status;
    PARAMS.forEach(function (p) {
      var v = ((p[0] === "bw" || p[0] === "sync") && src === status) ? "" : (src[p[1]] || "");
      set("rd-" + p[0] + "-" + key, v === "" || v === "-" ? "-" : v);
    });
  }

  // Resolves to whether the poll succeeded: an HTTP or network error, an unreadable body or a throw
  // while rendering is a failure, which the caller answers with the slow interval. A 404 resolves to "gone": the
  // radio is no (enabled) radio of the hardware list any more (app.py _api_radio), so it is never asked again.
  // A card's element ids end in its radio's id. A card without a radio (a stray band) is not polled: the daemon
  // views are per radio (/api/daemon/<rid>), and that card shows its server-rendered state.
  function poll(rid) {
    var key = rid;
    // only a page that shows the radio's FREQ / PWR / BW / SF line (Home's Monitor) asks for its values (GET RADIO)
    var q = document.getElementById("rd-freq-" + key) ? "?radio=1" : "";
    return fetch("/api/daemon/" + encodeURIComponent(rid) + q)
      .then(function (r) {
        if (r.status === 404) throw GONE;
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) {
        if (!d) return;
        cache[key] = { up: !!d.reachable, data: d, at: Date.now() };
        document.dispatchEvent(new CustomEvent("lhpc:daemonstate", { detail: { rid: key, up: !!d.reachable, data: d } }));
        paintAll(key, d);
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
          if (d.status.RADIO) set("rd-radio-" + key, d.status.RADIO);
          if (d.status.CADRSSI) set("rd-cadrssi-" + key, d.status.CADRSSI);
        }
        txpill(key, d.status, d.stats);
        params(key, d);
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
      .then(function () { return true; }, function (e) { return e === GONE ? "gone" : false; });
  }

  // Every badge of radio `rid` shows `d`: hidden only in a closed block (not for a hidden tab: a full page's pill
  // keeps the answer).
  function paintAll(rid, d) {
    var badges = Array.prototype.filter.call(document.querySelectorAll(".dbadge[data-rid]"),
                                             function (b) { return b.getAttribute("data-rid") === rid; });
    ["rd-badge-", "hw-badge-"].forEach(function (prefix) {
      var b = document.getElementById(prefix + rid);
      if (b) badges.push(b);
    });
    badges.forEach(function (badge) {
      paint(badge, !badge.classList.contains("dbadge") || !badge.closest("details:not([open])") ? d : null);
    });
  }
  // A radio that left the hardware list (or was disabled) while the page is open: its badges say so (also after a
  // block is closed and opened again: recheck paints them from GONE), its poller stops, and its daemon blocks pause
  // (daemoncfg.js takes the state like a stopped daemon). Its cached answer is dropped.
  var GONE = { label: "removed \u2014 reload the page", ready: false, reachable: false }, gone = {};
  function goneRadio(rid) {
    gone[rid] = true;
    delete cache[rid];
    paintAll(rid, GONE);
    document.dispatchEvent(new CustomEvent("lhpc:daemonstate", { detail: { rid: rid, up: false, data: GONE } }));
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
      if (gone[rid] || r.polling || !eligible(rid)) return;
      r.polling = true;
      poll(rid).then(function (ok) {
        r.polling = false;
        if (ok === "gone") goneRadio(rid);
        else schedule(ok ? POLL_MS : ERROR_MS);
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
        var rid = b.getAttribute("data-rid"), c = gone[rid] ? { data: GONE } : window.lhpcDaemonState(rid);
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
  recheck();                                    // the pills of closed blocks: hidden from the start
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
  var snapshot = {};                               // ids the signature-reload snapshot set (they win over a stored choice)
  try {
    var saved = JSON.parse(sessionStorage.getItem("dashDetails") || "null");
    sessionStorage.removeItem("dashDetails");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) {
      document.querySelectorAll("details").forEach(function (el) {
        if (el.id && typeof saved[el.id] === "number") { el.open = saved[el.id] === 1; snapshot[el.id] = true; }
      });
    }
  } catch (e) { /* ignore */ }
  var grid = document.querySelector("[data-dash-sig]");
  if (grid) {
    var sig = grid.getAttribute("data-dash-sig");
    var pending = grid.getAttribute("data-pending-interactive") === "1";
    // A reload still loading is never restarted by a later poll: on a box whose page renders slower than the
    // poll, each new reload aborted the last and none ever finished. A reload the viewer stopped (Stop/Esc, or a
    // page restored from the back/forward cache) reloads on change again only after the viewer reloads it.
    var reloading = false;
    setInterval(function () {
      if (document.hidden) return;
      fetch("/api/dash-signature")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (reloading || !d || d.sig === sig) return;   // nothing structural changed, or the reload is on its way
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
            reloading = true;
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

  // A box's open-state persists across ORDINARY reloads too (the id-keyed sessionStorage restore above only survives
  // dash.js's own signature reloads, and wins over the stored choice): the Webserver box (rendered collapsed) and,
  // each radio card (rendered open; key lhpc:radiobox-<rid or band>). The stored "1" opens, "0"
  // closes. The toggle listener is attached after the restore and stores only a change from the state the box showed
  // then: the restore's own queued toggle event never writes this tab's older snapshot over a newer choice another
  // tab stored. Mirrors the System box's localStorage contract.
  function remember(box, KEY) {
    if (!box) return;
    if (!snapshot[box.id]) {
      try {
        var v = localStorage.getItem(KEY);
        if (v === "1" || v === "0") box.open = v === "1";
      } catch (e) { /* private mode */ }
    }
    var shown = box.open;
    box.addEventListener("toggle", function () {
      if (box.open === shown) return;
      shown = box.open;
      try { localStorage.setItem(KEY, box.open ? "1" : "0"); } catch (e) { /* private mode */ }
    });
  }
  remember(document.getElementById("wsbox"), "lhpc:wsbox");
  document.querySelectorAll("details.radiobox[id^='radiobox-']").forEach(function (box) { remember(box, "lhpc:" + box.id); });
  // F18: a radio card's stack blocks and component boxes keep the viewer's open/closed choice the same way (their
  // server-rendered defaults apply until one is toggled).
  document.querySelectorAll("details.runstack[id^='stk-'], details.cbox[id^='cbox-'], details.runon").forEach(function (box) {
    remember(box, "lhpc:" + box.id);
  });
})();
