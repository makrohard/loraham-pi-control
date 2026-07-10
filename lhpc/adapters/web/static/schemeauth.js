// Scheme -> access-mode coupling for the webserver panels (console + per-stack web UI proxies).
//
// A client certificate is presented during the TLS handshake, so a plain-http listener has nothing
// to verify: `http` can only ever mean `no-auth`. This script keeps the form honest as the operator
// changes the scheme, so they are never offered a guarantee nginx cannot keep.
//
// COSMETIC ONLY. The service layer (`webserver_configure` / `stack_web_configure`) and the config
// writer both reject the combination server-side; a disabled <option> is a courtesy, not a control.
// External file, because the CSP forbids inline script.
(function () {
  function sync(sel) {
    var auth = document.getElementById(sel.dataset.authsel);
    if (!auth) { return; }
    var http = sel.value === "http";
    var opts = auth.options;
    for (var i = 0; i < opts.length; i++) {
      var isNoAuth = opts[i].value === "no-auth";
      opts[i].disabled = http && !isNoAuth;
    }
    // A disabled option must never stay selected — the browser would submit it.
    if (http && auth.value !== "no-auth") {
      for (var j = 0; j < opts.length; j++) {
        if (opts[j].value === "no-auth") { auth.selectedIndex = j; break; }
      }
    }
  }

  document.querySelectorAll("select.schemesel[data-authsel]").forEach(function (sel) {
    sync(sel);                                   // reflect the server-rendered state on load
    sel.addEventListener("change", function () { sync(sel); });
  });
})();
