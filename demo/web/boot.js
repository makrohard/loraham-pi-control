"use strict";
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.2/full/";
const $ = (id) => document.getElementById(id);
const setStatus = (t) => { const s = $("status"); if (s) s.textContent = t; };
let bridge = null;

// One document per page, as on a box: each page the app renders is loaded into the iframe #app (srcdoc), and the
// product's own scripts run there once per page, unchanged. Their listeners, timers and late callbacks die with the
// page's document. The demo adds only the transport: the frame's fetch of an app path goes to demoFetch (the
// in-browser app), its links and forms go to go(), and a load the demo did not start (a script's location.reload()
// or location.href = an app path) is rendered again through the bridge, so Pyodide never boots twice.
const BASE = new URL(".", location.href);            // the index.html directory (on Pages: the repo's path)
let current = "/";                                    // the app path the frame shows
let ours = false;                                     // the frame's next load is a render of ours
let hash = "";                                        // the fragment of the path being rendered

async function main() {
  try {
    try { sessionStorage.setItem("demoBoots", String(Number(sessionStorage.getItem("demoBoots") || 0) + 1)); } catch (_) {}
    setStatus("starting Python…");
    const py = await loadPyodide({ indexURL: PYODIDE });
    setStatus("loading packages…");
    await py.loadPackage("micropip");
    const micropip = py.pyimport("micropip");
    setStatus("installing LHPC…");
    const wheels = await (await fetch("./wheels.json", { cache: "no-cache" })).json();
    await micropip.install(wheels, { keep_going: true });
    bridge = await py.runPythonAsync("import lhpc_demo.bridge as _b; _b");
    bridge.boot(localStorage.getItem("lhpc_demo_state") || "");
    $("app").addEventListener("load", loaded);
    go("GET", "/");
    $("loader").style.display = "none";
    $("app").hidden = false;
  } catch (e) {
    setStatus("failed to start: " + (e && e.message ? e.message : e));
    console.error(e);
  }
}

function persist() {
  try { localStorage.setItem("lhpc_demo_state", bridge.dump_state()); } catch (_) {}
}

// One request through the in-browser app; the browser's cookies (stacks_state.js's lhpc_open) go with it.
function ask(method, path, formJson) {
  return JSON.parse(bridge.handle(method, path, formJson || "", document.cookie));
}

// An app path: no scheme (http:, javascript:, data:, mailto:…), no protocol-relative //host, no in-page #anchor.
const isInternal = (href) =>
  typeof href === "string" && href !== "" && !/^[a-z][a-z0-9+.\-]*:/i.test(href) && !href.startsWith("//") && !href.startsWith("#");
// `href` resolved against the page shown, as the browser resolves it on a box: path + query (+ fragment).
const appPath = (href, keepHash) => {
  const u = new URL(href, "http://app" + current);
  return u.pathname + u.search + (keepHash ? u.hash : "");
};

// The frame's fetch of an app path (the prelude below routes it here): the bridge's {status, ctype, body} as a Response.
// A URLSearchParams or string body (taskbanner.js, dash.js: form-urlencoded) becomes the form fields the bridge takes.
window.demoFetch = function (url, init) {
  const o = init || {}, method = String(o.method || "GET").toUpperCase();
  const form = o.body == null ? "" : JSON.stringify(Object.fromEntries(new URLSearchParams(String(o.body))));
  try {
    const r = ask(method, appPath(url), form);
    if (method !== "GET") persist();
    const nobody = [101, 103, 204, 205, 304].includes(r.status);
    return Promise.resolve(new Response(nobody ? null : r.body, { status: r.status, headers: { "Content-Type": r.ctype || "" } }));
  } catch (e) {
    return Promise.reject(new TypeError("demo bridge: " + (e && e.message ? e.message : e)));
  }
};

const PRELUDE = "<script>(function(){var f=window.fetch;window.fetch=function(u,o){"
  + "if(typeof u==='string'&&u!==''&&!/^[a-z][a-z0-9+.\\-]*:/i.test(u)&&u.slice(0,2)!=='//')return parent.demoFetch(u,o);"
  + "return f.apply(window,arguments);};})();</script>";

function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }

// Render one app response into the frame. HTML gets the fetch prelude first in its <head>, and its root-relative
// /static/ becomes static/: an about:srcdoc page resolves relative URLs against this page (index.html's directory, also
// on Pages under the repo's path), where assemble.sh puts the static files. Any other body is shown as text.
function show(r) {
  let html;
  if ((r.ctype || "").includes("html")) {
    const head = PRELUDE;
    html = r.body.replace(/(\s(?:src|href)=["'])\/static\//g, "$1static/");
    html = /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, (m) => m + head) : head + html;
  } else {
    html = "<!doctype html><pre>" + esc(r.body) + "</pre>";
  }
  ours = true;
  $("app").srcdoc = html;
}

function go(method, path, formData) {
  const formJson = formData ? JSON.stringify(Object.fromEntries(formData.entries())) : "";
  const target = appPath(path, true), cut = target.indexOf("#");
  current = cut < 0 ? target : target.slice(0, cut);
  hash = cut < 0 ? "" : target.slice(cut);
  show(ask(method, current, formJson));
  persist();
}

// A frame load: ours -> wire the new document; not ours -> a script navigated it: replay through the bridge.
// A reload (about:srcdoc) renders the current path again; a navigation renders the path it went to (on Pages the
// frame first gets GitHub's same-origin 404 page; its path minus the index directory is the app path).
function loaded() {
  const win = $("app").contentWindow;
  let href = "";
  try { href = win.location.href; } catch (_) { /* another origin's page: unreadable, render the current path again */ }
  if (ours && href.startsWith("about:srcdoc")) {
    ours = false;
    wire(win.document);
    if (hash) { const h = hash; hash = ""; win.location.hash = h; }   // as a box loads /#x: scroll there, open its <details>
    return;
  }
  ours = false;
  if (!href || href.startsWith("about:")) { go("GET", current); return; }
  const loc = new URL(href);
  let p = loc.pathname;
  if (p.startsWith(BASE.pathname)) p = "/" + p.slice(BASE.pathname.length);
  go("GET", p + loc.search + loc.hash);
}

// The page's links and forms go through the bridge. A product handler that cancelled the event (a confirm the
// operator declined) wins: these run after the page's own listeners.
function wire(doc) {
  doc.addEventListener("click", (e) => {
    if (e.defaultPrevented) return;
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a) return;
    const href = a.getAttribute("href");
    // An in-page #anchor jumps inside this page, as on a box; left alone, an about:srcdoc page resolves it against
    // index.html and loads the demo itself into the frame.
    if (href.startsWith("#")) { e.preventDefault(); doc.defaultView.location.hash = href; return; }
    if (isInternal(href)) { e.preventDefault(); go("GET", href); return; }
    // Another site opens in a new tab: the frame stays the app (a page of another origin in it is unreadable).
    if (/^https?:/i.test(href) && !a.target) { e.preventDefault(); window.open(href, "_blank", "noopener"); }
  });
  doc.addEventListener("submit", (e) => {
    if (e.defaultPrevented) return;
    const f = e.target;
    if (!f || f.tagName !== "FORM") return;
    e.preventDefault();
    const fd = new FormData(f);
    if (e.submitter && e.submitter.name) fd.append(e.submitter.name, e.submitter.value);
    go((f.getAttribute("method") || "GET").toUpperCase(), f.getAttribute("action") || current, fd);
  });
}

// The demo bar stays on top of the frame.
document.addEventListener("click", (e) => {
  if (!(e.target.closest && e.target.closest("#resetbtn"))) return;
  e.preventDefault();
  if (!bridge) return;
  bridge.reset_state();
  localStorage.removeItem("lhpc_demo_state");
  go("GET", "/");
});

main();
