"""The Meshtastic web client as its own component (docs/provenance.md, "The web client").

`meshtastic-web` is served by meshtasticd from `RootPath` (`build/tools/meshtastic-web/web`), outside
the meshtasticd tree a binary install replaces. Three selectors:

- `binary` (default, known-working): the manifest pin's upstream `build.tar`, fetched and verified by
  the component's own build step (`meshtastic-web-assets.sh <dest> <version> <sha256>`);
- `stable` / `dev`: an index-v3 entry (stack `meshtastic-web`) whose `channel` is the selector and
  whose recorded meshtasticd commit is the installed one, lab-smoked against that daemon.

`web.provenance`, beside the served root, records what is installed: version, sha256, the selector
(kept by a bare update or rebuild) and the meshtasticd commit it was paired with. The completion
marker is the static `.lhpc-built`, so a pin move changes nothing for a `stable`/`dev` client and
makes a `binary` one read *behind* (D3); a daemon change makes any client read *unproven*.
"""
from __future__ import annotations

import contextvars
import shutil
import tempfile
from pathlib import Path

from . import binary_install as bi
from . import reslock, runtime_fs
from .paths import PathContainmentError, Paths
from .service_base import ActionResult

COMPONENT = "meshtastic-web"
DAEMON = "meshtastic"
SCRIPT = "meshtastic-web-assets.sh"
SELECTORS = ("binary", "stable", "dev")
OLD_ROOT_REL = "build/tools/meshtasticd/web"         # the served root before Track F C3
ROOT_REL = "build/tools/meshtastic-web"
PROVENANCE_REL = ROOT_REL + "/web.provenance"
_PROVENANCE_MAX = 4096
# Set while `install(binary)` runs the component's build: the build fetches the pin even when a
# stable/dev client is recorded, and the record changes only when that build succeeded.
_FORCE_BINARY = contextvars.ContextVar("lhpc_web_force_binary", default=False)


def pin(comp) -> tuple[str, str]:
    """(version, sha256) of the known-working client: the arguments of the build step that runs
    the fetch script (the one place the bot moves the pin). ("", "") when there is no such step."""
    for st in comp.build_steps:
        argv = st.get("argv", ())
        if len(argv) == 5 and str(argv[1]).endswith("/" + SCRIPT):
            return str(argv[3]), str(argv[4])
    return "", ""


def current_root(value: str, runtime, default: str) -> str:
    """The one migration rule for a saved `web_root`: the OLD managed root (literal or rendered)
    serves from the new default; any other explicit value is the operator's and kept. Applied at
    every render, so it holds however the box was upgraded."""
    old = {"{runtime}/" + OLD_ROOT_REL, f"{runtime}/{OLD_ROOT_REL}"}
    return default if str(value).rstrip("/") in old else value


def lock(paths: Paths):
    """The one lock over `build/tools/meshtastic-web`: an index-v3 swap (`install`) and the
    `binary` build step (the service build loop) never interleave."""
    return reslock.operation_lock(paths, "web-client", "install", COMPONENT)


def read_provenance(paths: Paths) -> dict:
    """The recorded `key=value` lines, or {} when there is no readable record."""
    try:
        text = runtime_fs.read_text_regular(paths, paths.runtime_root / PROVENANCE_REL,
                                            max_bytes=_PROVENANCE_MAX)
    except (FileNotFoundError, OSError, PathContainmentError):
        return {}
    return dict(ln.split("=", 1) for ln in text.splitlines() if "=" in ln)


def write_provenance(paths: Paths, record: dict) -> None:
    text = "".join(f"{k}={record[k]}\n" for k in
                   ("web_version", "web_sha256", "selector", "source", "web_commit", "meshtasticd")
                   if record.get(k))
    runtime_fs.atomic_write(paths, paths.runtime_root / PROVENANCE_REL, text, 0o644)


def selector(prov: dict) -> str:
    """The installed client's selector; a record from before the selector existed is `binary`."""
    sel = prov.get("selector", "binary")
    return sel if sel in SELECTORS else "binary"


def state(prov: dict, pin_version: str, daemon_commit: str) -> tuple[str, bool]:
    """(`current` | `behind` | `differs` | `unknown`, unproven): `behind` = a `binary` client
    whose version is not the pin; `differs` = a `stable`/`dev` client (not the known-working
    one); unproven = it was paired with another meshtasticd than the installed one."""
    if not prov.get("web_version"):
        return "unknown", False
    sel = selector(prov)
    word = ("differs" if sel != "binary" else
            "current" if prov["web_version"] == pin_version else "behind")
    paired = prov.get("meshtasticd", "")
    return word, bool(daemon_commit) and paired != daemon_commit


def status_line(prov: dict, pin_version: str, daemon_commit: str) -> str:
    """The one status/doctor line for an installed client ("" when none is recorded)."""
    word, unproven = state(prov, pin_version, daemon_commit)
    if word == "unknown":
        return ""
    line = (f"web client {prov['web_version']} ({selector(prov)}, {word}"
            + (f"; pin {pin_version}" if word == "behind" else "") + ")")
    if unproven:
        line += (f" — unproven for this meshtasticd ({daemon_commit[:9]}): "
                 f"lhpc update {DAEMON} --yes re-pairs it, or lhpc update {COMPONENT} "
                 "--source binary --yes")
    return line


# ---- the service side: each function takes the ControllerService ------------------------------

def component(svc):
    st = svc.stack(DAEMON)
    return st.component(COMPONENT) if st else None


def daemon_commit(svc) -> str:
    """The installed meshtasticd's commit: the binary receipt's, else the checkout's HEAD.
    "" when neither can be read (then nothing reads unproven and stable/dev is refused)."""
    state_, rec, _why = svc.binary_receipt_state(DAEMON)
    if state_ == "valid" and rec is not None:
        return rec.components.get(DAEMON, "")
    comp = svc.stack(DAEMON).component(DAEMON)
    if not svc._source_present(comp):
        return ""
    rc, out = svc._git_out(["git", "-C", str(svc._lifecycle().source_dir(comp)), "rev-parse", "HEAD"])
    return out.strip() if rc == 0 else ""


def served_root(svc) -> str:
    """The `RootPath` meshtasticd is given (the render rule of service_params, applied here)."""
    runtime = svc._paths.runtime_root
    raw = svc._resolved_param_value(DAEMON, "file", DAEMON, "web_root")
    return str(current_root(raw, runtime, "{runtime}/" + ROOT_REL + "/web")).replace(
        "{runtime}", str(runtime)).rstrip("/")


def status(svc) -> str:
    """The status/doctor line for the installed client ("" when none); "not served" when the
    operator's own `web_root` makes meshtasticd serve another directory."""
    comp = component(svc)
    if comp is None:
        return ""
    line = status_line(read_provenance(svc._paths), pin(comp)[0], daemon_commit(svc))
    root = served_root(svc)
    if line and root != str(svc._paths.runtime_root / ROOT_REL / "web"):
        line += f" — not served: meshtasticd's RootPath is {root} (an operator setting)"
    return line


def install(svc, selector_: str = "", apply: bool = False) -> ActionResult:
    """Install the web client for `selector_` (empty = the recorded one, `binary` when none):
    `binary` runs the component's build (the manifest pin), `stable`/`dev` take the newest
    index-v3 entry of that channel smoked against the installed meshtasticd. Called only from
    decorated service entries (update, build, binary_install), which drop the snapshot memo."""
    comp = component(svc)
    sel = selector_ or selector(read_provenance(svc._paths))
    binary_cmd = f"lhpc update {COMPONENT} --source binary --yes"
    if sel == "pinned":
        return ActionResult(False, f"'{COMPONENT}' has no compiled lane: it is a fetched "
                                   "release (binary, stable or dev).",
                            next_commands=[binary_cmd], data={"refusal": "no_compiled_lane"})
    if sel not in SELECTORS or comp is None:
        return ActionResult(False, f"invalid source '{sel}' for '{COMPONENT}' (choose "
                                   f"{', '.join(SELECTORS)})", next_commands=[binary_cmd])
    daemon = daemon_commit(svc)
    if sel == "binary":
        version = pin(comp)[0]
        if not apply:
            return ActionResult(True, f"Plan: install the known-working web client {version} "
                                      "(binary, the manifest pin).",
                                next_commands=[binary_cmd])
        token = _FORCE_BINARY.set(True)            # the record changes only on success
        try:
            return svc.build(COMPONENT, apply=True)
        finally:
            _FORCE_BINARY.reset(token)
    spec = svc.binary_spec(DAEMON)
    try:
        bi.require_zstd()
        entry = bi.select_web(bi.index_entries(bi.fetch_index(spec.index_url), COMPONENT), sel, daemon)
    except bi.BinaryInstallError as exc:
        kept = read_provenance(svc._paths).get("web_version")
        typed = isinstance(exc, bi.BinaryNonePaired)          # only "no pair" is the typed refusal
        return ActionResult(False, f"Web client {sel} {'refused' if typed else 'failed'}: {exc.message}",
                            details=[f"  the installed client {kept} stays as it is"] if kept else [],
                            next_commands=[binary_cmd],
                            data={"refusal": "none_paired"} if typed else {"binary_failed": True})
    what = (f"web client {entry.provenance['web_version']} ({sel}, web "
            f"{entry.components[COMPONENT][:9]}) for meshtasticd {daemon[:9]}")
    if not apply:
        return ActionResult(True, f"Plan: install the {what}.", details=[f"  artifact {entry.filename}"],
                            next_commands=[f"lhpc update {COMPONENT} --source {sel} --yes"])
    try:
        with lock(svc._paths):
            _publish(svc, comp, entry, sel)
    except reslock.ResourceBusy as busy:
        return ActionResult(False, f"Web client {sel} install blocked: {busy}",
                            next_commands=[f"lhpc update {COMPONENT} --source {sel} --yes"])
    except (bi.BinaryInstallError, OSError, PathContainmentError) as exc:
        return ActionResult(False, f"Web client {sel} install failed: {exc}", next_commands=[binary_cmd])
    return ActionResult(True, f"Installed the {what}.", next_commands=[f"lhpc status {DAEMON}"],
                        data={"changes": 1})


def _publish(svc, comp, entry, sel: str) -> None:
    """Download, verify and swap in an index-v3 client. The marker goes first and comes back
    last, so an interrupted swap reads NOT built rather than built with half a client. Any
    failure after the previous client moved aside puts it and its sidecars back; that client
    is deleted only once every write succeeded (or the rollback did)."""
    from .lifecycle import BUILD_MARKER_TEXT
    root = Path(svc._paths.runtime_root)
    dest = root / ROOT_REL / "web"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".web-", dir=dest.parent))
    keep_tmp = False
    try:
        tar, stage = tmp / entry.filename, tmp / "stage"
        stage.mkdir()
        bi.download_artifact(entry, tar)
        bi.validate_and_extract(tar, stage, (ROOT_REL + "/web",))
        new = stage / ROOT_REL / "web"
        if not (new / "index.html").is_file():
            raise bi.BinaryInstallError("the web client artifact has no index.html")
        marker = root / comp.build_marker
        saved = {p: _read_or_none(svc, p) for p in   # the marker last, as on the way in
                 (svc.build_inputs_path(comp), root / PROVENANCE_REL, marker)}
        marker.unlink(missing_ok=True)
        old = tmp / "old"
        if dest.exists():
            dest.rename(old)
        try:
            new.rename(dest)
            runtime_fs.atomic_write(svc._paths, svc.build_inputs_path(comp),
                                    svc.build_inputs_text(comp), 0o644)
            write_provenance(svc._paths, {
                "web_version": entry.provenance["web_version"], "web_sha256": entry.sha256,
                "selector": sel, "source": "index-v3", "web_commit": entry.components[COMPONENT],
                "meshtasticd": entry.components[DAEMON]})
            runtime_fs.atomic_write(svc._paths, marker, BUILD_MARKER_TEXT, 0o644)
        except Exception:
            keep_tmp = True                        # a failed rollback must not delete `old`
            if not new.exists():                   # the new client was swapped in
                shutil.rmtree(dest, ignore_errors=True)
            if old.exists():                       # put the previous client back
                old.rename(dest)
            for p, text in saved.items():
                if text is None:
                    p.unlink(missing_ok=True)
                else:
                    runtime_fs.atomic_write(svc._paths, p, text, 0o644)
            keep_tmp = False
            raise
    finally:
        if not keep_tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _read_or_none(svc, path: Path):
    """A sidecar's text to restore after a failed swap; None only when it does not exist. Any
    other read error propagates before the swap starts, so nothing is touched."""
    try:
        return runtime_fs.read_text_regular(svc._paths, path)
    except FileNotFoundError:
        return None


def record_binary(svc) -> None:
    """After the component's build (the `binary` selector): the fetch script wrote version and
    sha256; add the selector and the meshtasticd it is paired with."""
    prov = read_provenance(svc._paths)
    write_provenance(svc._paths, {**prov, "selector": "binary", "source": "lhpc-manifest",
                                  "web_commit": "", "meshtasticd": daemon_commit(svc)})


def kept_index_install(svc):
    """In a build of the web component: a recorded `stable`/`dev` choice is re-resolved for
    the installed meshtasticd instead of fetching the pin (D3: a rebuild keeps the choice).
    None for `binary` (the build steps run). No paired entry keeps the installed client."""
    sel = selector(read_provenance(svc._paths))
    if sel == "binary" or _FORCE_BINARY.get():
        return None
    res = install(svc, sel, apply=True)
    if not res.ok and res.data.get("refusal") == "none_paired":
        return ActionResult(True, res.summary,
                            details=[*res.details, f"  [unproven] {COMPONENT}: kept, "
                                     "not paired with this meshtasticd"],
                            next_commands=res.next_commands)
    return res


def after_daemon(svc, res: ActionResult) -> ActionResult:
    """After meshtastic's binary install: the web client follows the daemon in the same run
    (its kept selector), unless it is already the one for this meshtasticd."""
    comp = component(svc)
    word, unproven = state(read_provenance(svc._paths), pin(comp)[0], daemon_commit(svc))
    if not res.ok or (word in ("current", "differs") and not unproven and svc.is_built(comp)):
        return res
    web = kept_index_install(svc) or install(svc, "binary", apply=True)
    return ActionResult(res.ok and web.ok, res.summary,
                        details=[*res.details, f"  web client: {web.summary}", *web.details],
                        next_commands=res.next_commands if web.ok else web.next_commands,
                        data=res.data)
