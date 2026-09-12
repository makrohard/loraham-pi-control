"""Manifest loading.

The manifest is the central, version-controllable description of every stack and
component. The loader parses the schema (per-band daemons, structured process
identity, probeable endpoints, resource compatibility modes, source pins and
runtime dependencies) into the `model` dataclasses.

Configuration layering: see docs/architecture.md, "Manifest and config layers".

Uses the stdlib `tomllib` (Python 3.11+). Read-only: it never writes or fetches.
"""

from __future__ import annotations

import dataclasses
import re
import tomllib
from pathlib import Path

from . import commands
from .assets import asset_path
from .model import (
    BinarySpec,
    Component,
    ComponentKind,
    ControllerSpec,
    EndpointSpec,
    FileConfig,
    FileParam,
    FirewallMeta,
    ProcessSpec,
    Requirement,
    ResourceClaim,
    ResourceKind,
    ResourceMode,
    RunParam,
    SourceSpec,
    Stack,
    SystemdScope,
    UnitRef,
)


def _parse_file_config(raw: dict | None) -> FileConfig | None:
    if not raw:
        return None
    params = tuple(
        FileParam(
            name=p["name"], key=p.get("key", p["name"]), section=p.get("section", ""),
            kind=p.get("kind", "str"),
            choices=tuple(str(c) for c in p.get("choices", [])),
            default=str(p.get("default", "")), label=p.get("label", ""),
            advanced=p.get("advanced", False), apply_mode=p.get("apply_mode", "restart"),
            min=p.get("min"), max=p.get("max"),
            band_defaults=tuple((str(k), str(v)) for k, v in p.get("band_defaults", {}).items()),
            hidden=p.get("hidden", False),
            validator=p.get("validator", ""),
            group=p.get("group", ""),
            omit_if_empty=p.get("omit_if_empty", False),
            secret_ref=str(p.get("secret_ref", "")),
            secret_file=str(p.get("secret_file", "")),
        )
        for p in raw.get("param", [])
    )
    path = raw["path"]
    base = raw.get("base", "")
    # Static containment policy (P1): a generated-config path/base must be a `{runtime}/...`
    # destination OR a RELATIVE path under the managed source — never an arbitrary absolute
    # path, unknown `{placeholder}`, or a `..` traversal. A BASE may additionally be
    # `{asset}/...` (a template shipped as lhpc package data, read-only); a generated
    # DESTINATION may not. Runtime-`Paths` checks happen at write time; this rejects
    # malformed manifest destinations at load.
    for label, value in (("config_file.path", path), ("config_file.base", base)):
        if not value:
            continue
        if value == "{runtime}" or value.startswith("{runtime}/"):
            continue
        if label == "config_file.base" and value.startswith("{asset}/"):
            if ".." in value.split("/"):
                raise ManifestError(f"{label} must not traverse, got {value!r}")
            continue
        if value.startswith(("/", "{")) or ".." in value.split("/"):
            raise ManifestError(
                f"{label} must be '{{runtime}}/...', '{{asset}}/...' (base only) or a relative "
                f"source path, got {value!r}")
    fmt = raw.get("fmt", "keyval")
    if fmt not in _CONFIG_FMTS:
        raise ManifestError(f"config_file.fmt {fmt!r} unknown "
                            f"(allowed: {', '.join(sorted(_CONFIG_FMTS))})")
    mode = raw.get("mode", 0o644)
    if isinstance(mode, str):
        mode = int(mode, 8)
    if mode not in _CONFIG_MODES:
        raise ManifestError(f"config_file.mode {mode:#o} not permitted "
                            f"(allowed: {', '.join(oct(m) for m in sorted(_CONFIG_MODES))})")
    # A secret must never be able to compete with an operator-settable value, and a file
    # carrying one must not be world-readable. Both secret sources share these invariants;
    # they differ only in WHERE the value comes from (operator secrets.toml vs a
    # controller-managed file under config/secrets/).
    for prm in params:
        if prm.secret_ref and prm.secret_file:
            raise ManifestError(f"param {prm.name!r} declares both secret_ref and secret_file "
                                f"— a secret has exactly one source")
        if prm.secret_ref:
            if prm.secret_ref.count(".") != 1 or not all(prm.secret_ref.split(".")):
                raise ManifestError(f"secret_ref {prm.secret_ref!r} must be '<table>.<key>'")
        elif prm.secret_file:
            # A BARE filename: config/secrets/<name>. Anything path-shaped could escape the
            # managed secrets directory, so it is rejected at LOAD, not at write time.
            if "/" in prm.secret_file or prm.secret_file in (".", "..") \
                    or prm.secret_file.startswith("."):
                raise ManifestError(f"secret_file {prm.secret_file!r} must be a bare filename "
                                    f"under config/secrets/")
        else:
            continue
        which = "secret_ref" if prm.secret_ref else "secret_file"
        if not prm.hidden:
            raise ManifestError(f"param {prm.name!r} has {which} and must be hidden")
        if prm.default or prm.band_defaults:
            raise ManifestError(f"param {prm.name!r} has {which} and must not declare "
                                f"a default or band_defaults — the secret is the only source")
        # OWNER-ONLY, not exactly 0600: a secret-bearing config may also be read-only to its
        # owner (0400), which is how a config LHPC writes but a co-resident client must not
        # is declared. LHPC's own writer is unaffected — `runtime_fs.atomic_write_bytes`
        # renames a fresh temp leaf over the target, and rename needs permission on the
        # DIRECTORY, not the file. What 0400 stops is an in-place `open(path, "wb")`.
        if mode not in _SECRET_CONFIG_MODES:
            raise ManifestError(f"config_file carrying {which} param {prm.name!r} must be "
                                f"owner-only (0600 or 0400), got {mode:#o}")
    return FileConfig(path=path, fmt=fmt, mode=mode,
                      base=base, apply_cmd=raw.get("apply_cmd", ""),
                      params=params)

_CONFIG_FMTS = {"keyval", "env", "toml-update", "yaml-update", "ini-update"}
_CONFIG_MODES = {0o644, 0o640, 0o600, 0o400}
# The subset a config carrying a secret may declare: owner-only, nothing group- or
# world-readable. 0400 additionally keeps a co-resident client from rewriting it in place.
_SECRET_CONFIG_MODES = {0o600, 0o400}

_DEFAULT_MANIFEST = asset_path("manifest.example.toml")   # package data (wheel-safe)


def default_manifest_path() -> Path:
    return _DEFAULT_MANIFEST


def load_manifest(path: Path | None = None) -> tuple[Stack, ...]:
    """Load and parse the manifest into Stack/Component objects (read-only). Also VALIDATES
    any present `[controller]` table (strict parser, result discarded) so an invalid
    controller declaration is rejected here — not silently ignored by dashboard, bootstrap,
    auto-install, or normal stack paths. """
    stacks, _controller = _load_stacks_and_controller(path)
    return stacks


def _load_stacks_and_controller(path: Path | None):
    """Shared load+parse: stacks + the (validated) controller. `parse_controller` raises
    `ManifestError` on any invalid `[controller]` table (unknown key, nested sub-table,
    fixed-path/branch violation, id collision)."""
    manifest_path = path or _DEFAULT_MANIFEST
    with manifest_path.open("rb") as fh:
        data = tomllib.load(fh)
    stacks = parse_manifest(data)
    known = {s.id for s in stacks} | {c.id for s in stacks for c in s.components}
    return stacks, parse_controller(data, known)


class ManifestError(Exception):
    """A manifest declared an invalid or unsafe lifecycle spec (fail early)."""


# "gps-feed": verified from the feed's own readiness marker (its upstream source),
# never from the endpoint path existing — a PTY exists the instant it is created.
_READINESS = {"process", "endpoint", "daemon-band", "manual", "external-systemd",
              "gps-feed"}
_PRE_KINDS = {"mkdir", "chmod", "symlink"}
_POST_KINDS = {"delay", "exec", "tcp_wait", "tcp_send"}
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A require's `module` probe is a TOP-LEVEL python module name only. Dotted names are refused on
# purpose: `importlib.util.find_spec("parent.child")` IMPORTS the parent package to locate the child,
# and this probe runs on read-only dependency/status paths that must stay side-effect free.
_MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _require_module(value, cid: str) -> str:
    """Validate a require's optional `module` probe name."""
    s = str(value or "").strip()
    if not s:
        return ""
    if not _MODULE_NAME.fullmatch(s):
        raise ManifestError(
            f"{cid}: require module must be a top-level python module name "
            f"(no dots — find_spec would import the parent package), got {s!r}")
    return s


def _check_token(cid: str, tok: str, names: set) -> None:
    """Validate one argv token's grammar: a whole placeholder must reference a known
    param/operator/controller name; a literal may embed only {runtime}/{source}/{band}
    and must contain no other stray braces."""
    if tok.startswith("{") and tok.endswith("}") and tok.count("{") == 1:
        inner = tok[1:-1]
        kind, _, name = inner.partition(":")
        if kind == "param":
            if name not in names:
                raise ManifestError(f"{cid}: unknown parameter placeholder {tok!r}")
        elif kind == "operator":
            if name != "callsign":
                raise ManifestError(f"{cid}: unknown operator placeholder {tok!r}")
        # `controller_python` is controller-derived like runtime/source: it resolves to THIS
        # controller's interpreter, so lhpc's own internal services (the GPS bridge) never
        # run through whatever python3 happens to be on PATH.
        # `gps_mode` / `gps_fixed_args` are controller-owned post-step values resolved from
        # the ONE global GPS plan (see core/gps.py) — a stack cannot choose its own source.
        elif inner not in ("band", "runtime", "source", "controller_python",
                           "gps_mode", "gps_fixed_args", "gps_args"):
            raise ManifestError(f"{cid}: unknown placeholder {tok!r}")
        return
    # `{asset}/...` — a packaged-data path (build steps and run commands both resolve it via
    # commands._asset_token, which validates each path segment). Allow it in literal tokens.
    stripped = (tok.replace("{runtime}", "").replace("{source}", "").replace("{band}", "")
                   .replace("{controller_python}", "").replace("{asset}", ""))
    if "{" in stripped or "}" in stripped:
        raise ManifestError(f"{cid}: malformed command token {tok!r} (stray brace)")


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _validate_endpoint(cid: str, e) -> None:
    """Validate one endpoint declaration. A `ready=true` endpoint that gates start/stop
    must be LOCAL: a TCP readiness host must be loopback (never an arbitrary remote),
    and a Unix readiness path must be a contained runtime path unless explicitly marked
    `external=true` (external endpoints may be observed but never gate readiness)."""
    if e.kind == "tcp":
        # Use the ONE shared endpoint parser (also used by the runtime readiness probe).
        from .probes.endpoints import parse_endpoint
        try:
            host, _port, _fam = parse_endpoint(e.address)
        except ValueError:
            raise ManifestError(f"{cid}: malformed tcp endpoint address {e.address!r}") from None
        if e.ready and host not in _LOOPBACK_HOSTS:
            raise ManifestError(f"{cid}: a ready=true tcp endpoint must be loopback "
                                f"(got {host!r}) — readiness must not probe a remote host")
    elif e.kind == "unix":
        if not e.address:
            raise ManifestError(f"{cid}: unix endpoint requires an address")
        if e.ready and getattr(e, "external", False):
            raise ManifestError(f"{cid}: an external endpoint cannot be a ready=true "
                                "readiness/cessation gate")
    elif e.kind == "path":
        if not e.address:
            raise ManifestError(f"{cid}: path endpoint requires an address")
    else:
        raise ManifestError(f"{cid}: unknown endpoint kind {e.kind!r}")


def _validate_component(comp) -> None:
    cid = comp.id
    runnable = bool(comp.run_argv)
    if comp.readiness and comp.readiness not in _READINESS:
        raise ManifestError(f"{cid}: unknown readiness {comp.readiness!r} "
                            f"(allowed: {', '.join(sorted(_READINESS))})")
    if comp.interactive and comp.readiness != "manual":
        raise ManifestError(f"{cid}: interactive component must declare readiness=\"manual\"")
    if comp.units and not comp.run_argv and comp.readiness not in ("", "external-systemd"):
        raise ManifestError(f"{cid}: systemd-only component must use readiness=\"external-systemd\"")
    if runnable and not comp.readiness:
        raise ManifestError(f"{cid}: runnable component must declare a readiness policy")
    if comp.readiness == "endpoint" and not any(e.ready for e in comp.endpoints):
        raise ManifestError(f"{cid}: readiness=\"endpoint\" requires at least one "
                            f"endpoint marked ready = true")
    if not (0.0 <= comp.readiness_timeout <= 600.0):
        raise ManifestError(f"{cid}: readiness_timeout must be between 0 and 600 seconds "
                            f"(got {comp.readiness_timeout})")
    for e in comp.endpoints:
        _validate_endpoint(cid, e)
    names = {p.name for p in comp.run_params}
    for tok in comp.run_argv:
        _check_token(cid, tok, names)
    for tok in comp.test_argv:
        _check_token(cid, tok, names)
    for step in comp.build_steps:
        for tok in step.get("argv", []):
            tok_str = str(tok)
            if tok_str.startswith("{pkgconfig:") and tok_str.endswith("}"):
                continue            # build-only placeholder (resolved via pkg-config)
            if tok_str == "{asset}" or tok_str.startswith("{asset}/"):
                # Build-only placeholder: a helper SHIPPED as lhpc package data, for steps whose
                # logic cannot live in an upstream checkout. Resolved (and path-validated) by
                # commands._asset_token; read-only by construction.
                continue
            _check_token(cid, tok_str, names)
        # Optional quiet-step preamble written into the step log at step start: a string with
        # only {runtime}/{source} placeholders. Validated EAGERLY (dry-run substitution) so a
        # typo'd placeholder fails at manifest load, not minutes into a build.
        # The producer half of the release lane's build-attribution contract: a step declaring
        # itself `attributable` says a failure there is this component's own build regression
        # (see the comment on the daemon's build step). A non-bool would be truthy and freeze a
        # stack's pins on a typo.
        if "attributable" in step and not isinstance(step["attributable"], bool):
            raise ManifestError(f"{cid}: build-step attributable must be true or false")
        if "announce" in step:
            ann = step["announce"]
            if not isinstance(ann, str) or not ann.strip():
                raise ManifestError(f"{cid}: build-step announce must be a non-empty string")
            try:
                commands._paths_subst(ann, "r", "s", "")
            except commands.CommandError as exc:
                raise ManifestError(f"{cid}: build-step announce: {exc}") from exc
    for step in comp.pre_steps:
        if step.get("kind") not in _PRE_KINDS:
            raise ManifestError(f"{cid}: invalid pre-step kind {step.get('kind')!r}")
    for step in comp.post_steps:
        if step.get("kind") not in _POST_KINDS:
            raise ManifestError(f"{cid}: invalid post-step kind {step.get('kind')!r}")
    for k, _v in comp.run_env:
        if not _ENV_NAME.fullmatch(k):
            raise ManifestError(f"{cid}: invalid environment variable name {k!r}")


def _validate_graph(stacks: tuple[Stack, ...]) -> None:
    """Whole-manifest integrity AFTER every stack/component parses: unique stack IDs,
    globally-unique component IDs, each `main` in its OWN stack, every dependency
    resolvable, no self-dependency or cycle (with cycle evidence), and valid declared
    bands. A structurally-broken manifest fails here, never at launch time."""
    from .daemon_control import ALLOWED_BANDS

    seen_stacks: set[str] = set()
    for s in stacks:
        if s.id in seen_stacks:
            raise ManifestError(f"duplicate stack id {s.id!r}")
        seen_stacks.add(s.id)

    comp_of: dict[str, object] = {}
    comp_stack: dict[str, str] = {}
    for s in stacks:
        for c in s.components:
            if c.id in comp_of:
                raise ManifestError(f"duplicate component id {c.id!r} (in stacks "
                                    f"{comp_stack[c.id]!r} and {s.id!r})")
            comp_of[c.id] = c
            comp_stack[c.id] = s.id

    for s in stacks:                                   # main resolves to an OWN component
        if s.main and s.main not in {c.id for c in s.components}:
            raise ManifestError(f"stack {s.id!r} main {s.main!r} is not one of its "
                                f"components {sorted(c.id for c in s.components)}")

    for cid, c in comp_of.items():                     # dependencies resolvable, no self-dep
        for dep in c.depends_on:
            if dep == cid:
                raise ManifestError(f"component {cid!r} depends on itself")
            if dep not in comp_of:
                raise ManifestError(f"component {cid!r} depends on unknown component {dep!r}")
        for dep in c.build_requires:                   # build deps: known SOURCE components
            if dep == cid:
                raise ManifestError(f"component {cid!r} build_requires itself")
            if dep not in comp_of:
                raise ManifestError(f"component {cid!r} build_requires unknown "
                                    f"component {dep!r}")
            if comp_of[dep].source is None:
                raise ManifestError(f"component {cid!r} build_requires {dep!r}, which "
                                    "declares no source checkout")

    WHITE, GRAY, BLACK = 0, 1, 2                        # cycle detection with evidence
    color = dict.fromkeys(comp_of, WHITE)

    def _visit(cid: str, path: list[str]) -> None:
        color[cid] = GRAY
        for dep in comp_of[cid].depends_on:
            if color[dep] == GRAY:                     # dep is on the current stack -> cycle
                i = path.index(dep)
                raise ManifestError("dependency cycle: " + " -> ".join([*path[i:], dep]))
            if color[dep] == WHITE:
                _visit(dep, [*path, dep])
        color[cid] = BLACK

    for cid in comp_of:
        if color[cid] == WHITE:
            _visit(cid, [cid])

    for cid, c in comp_of.items():                     # declared bands are real bands
        for b in ([c.band] if c.band else []) + list(getattr(c, "bands", ()) or ()):
            if b and b not in ALLOWED_BANDS:
                raise ManifestError(f"component {cid!r} declares unknown band {b!r} "
                                    f"(allowed: {', '.join(ALLOWED_BANDS)})")

    # SHARED-SOURCE COHERENCE: every component consuming ONE checkout dir (same source.path)
    # must declare the IDENTICAL source spec — selector resolution, the ownership registry and
    # uninstall refcounting all key on the path, so disagreeing pins/remotes/branches/artifact
    # flags would make "the version of src/X" ambiguous. Fail at load, never at mutation time.
    by_path: dict[str, tuple] = {}
    for cid, c in comp_of.items():
        if not c.source or not c.source.path:
            continue
        ident = (c.source.artifact, c.source.pin_commit, c.source.pin_tag,
                 c.source.branch, c.source.remote)
        prev = by_path.get(c.source.path)
        if prev is None:
            by_path[c.source.path] = (cid, ident)
        elif prev[1] != ident:
            raise ManifestError(
                f"components {prev[0]!r} and {cid!r} share source path "
                f"{c.source.path!r} but declare different source specs "
                "(pin/tag/branch/remote/artifact must be identical)")

    # PROXY PAGE IDS: one proxied web page per component with a client http/https endpoint; a
    # stack's first page keeps the stack id, further ones are `<stack_id>-<component_id>`
    # (`model.web_pages`). Stack ids and component ids are each unique, but a DERIVED id can still
    # collide with a stack id (stack `foo-bar` — with OR without a web UI — vs stack `foo` +
    # component `bar`), and two ids that FOLD to one nginx identifier (`two-b` vs `two_b`, see
    # `webserver.nginx_token`) would hand one UI the other's access policy at apply time. So the
    # complete derived set is checked once, here — against every stack id and by nginx token —
    # never at proxy time.
    from .model import web_pages
    from .webserver import nginx_token  # imports only pki/config/paths: cycle-free
    seen_pages: dict[str, str] = {s.id: f"stack {s.id!r}" for s in stacks}
    tokens: dict[str, str] = {}
    for s in stacks:
        for page in web_pages(s):
            owner = f"{s.id}/{page.component_id}"
            if not page.primary:                        # a first page IS its stack id
                if page.page_id in seen_pages:
                    raise ManifestError(
                        f"proxy page id {page.page_id!r} is derived for {owner} but already "
                        f"names {seen_pages[page.page_id]} — rename one")
                seen_pages[page.page_id] = owner
            tok = nginx_token(page.page_id)
            if tokens.get(tok, page.page_id) != page.page_id:
                raise ManifestError(
                    f"proxy page ids {tokens[tok]!r} and {page.page_id!r} both fold to the nginx "
                    f"identifier {tok!r} — rename one before both can be proxied")
            tokens[tok] = page.page_id


_BINARY_KEYS = frozenset({"index_url", "covers", "publish_roots", "proof_paths",
                          "clone_required", "probes"})


def _rel_path_ok(p: str) -> bool:
    """Runtime-root-relative, normalized, never escaping."""
    if not isinstance(p, str) or not p or p.startswith(("/", "~")):
        return False
    parts = p.split("/")
    return all(seg not in ("", ".", "..") for seg in parts)


def _parse_binary(raw, stack_id: str, components: tuple[Component, ...]) -> BinarySpec:
    """STRICT `[stack.binary]` parse. The declaration is a security surface (it decides what
    an artifact may replace and where downloads come from), so every field is allow-listed
    and validated; a covered component must keep a pinned git source (the components-map
    pin comparison dies silently otherwise)."""
    if not isinstance(raw, dict):
        raise ManifestError(f"[{stack_id}.binary] must be a table")
    unknown = set(raw) - _BINARY_KEYS
    if unknown:
        raise ManifestError(f"[{stack_id}.binary]: unknown key(s) {sorted(unknown)}")
    url = raw.get("index_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ManifestError(f"[{stack_id}.binary].index_url must be an https:// URL")

    def _str_tuple(key, required):
        v = raw.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            raise ManifestError(f"[{stack_id}.binary].{key} must be a list of strings")
        if required and not v:
            raise ManifestError(f"[{stack_id}.binary].{key} must be non-empty")
        return tuple(v)

    covers = _str_tuple("covers", required=True)
    roots = _str_tuple("publish_roots", required=True)
    proofs = _str_tuple("proof_paths", required=True)
    clone_req = _str_tuple("clone_required", required=False)
    by_id = {c.id: c for c in components}
    for cid in covers:
        c = by_id.get(cid)
        if c is None:
            raise ManifestError(f"[{stack_id}.binary].covers: unknown component {cid!r}")
        if c.source is None or not c.source.pin_commit:
            raise ManifestError(
                f"[{stack_id}.binary].covers: component {cid!r} has no pinned source — "
                "the index components-map comparison requires a pin_commit")
    for cid in clone_req:
        if cid not in covers:
            raise ManifestError(
                f"[{stack_id}.binary].clone_required: {cid!r} must also be in covers")
    for p in (*roots, *proofs):
        if not _rel_path_ok(p):
            raise ManifestError(f"[{stack_id}.binary]: unsafe path {p!r}")
    for p in proofs:
        if not any(p == r or p.startswith(r + "/") for r in roots):
            raise ManifestError(
                f"[{stack_id}.binary].proof_paths: {p!r} not under any publish root")
    probes_raw = raw.get("probes", [])
    if not isinstance(probes_raw, list):
        raise ManifestError(f"[{stack_id}.binary].probes must be a list of argv lists")
    probes = []
    for pr in probes_raw:
        if (not isinstance(pr, list) or not pr
                or not all(isinstance(x, str) and x for x in pr)):
            raise ManifestError(f"[{stack_id}.binary].probes: invalid argv {pr!r}")
        if not _rel_path_ok(pr[0]):
            raise ManifestError(f"[{stack_id}.binary].probes: unsafe binary path {pr[0]!r}")
        probes.append(tuple(pr))
    return BinarySpec(index_url=url, covers=covers, publish_roots=roots,
                      proof_paths=proofs, clone_required=clone_req,
                      probes=tuple(probes))


def parse_manifest(data: dict) -> tuple[Stack, ...]:
    """Parse an already-loaded TOML mapping (kept separate for testing). Validates
    each component's structured lifecycle spec AND the whole dependency graph — an
    invalid manifest fails here rather than launching a misconfigured process."""
    stacks: list[Stack] = []
    for stack_raw in data.get("stack", []):
        components = tuple(
            _parse_component(c) for c in stack_raw.get("component", [])
        )
        for comp in components:
            _validate_component(comp)
        binary = None
        if "binary" in stack_raw:
            binary = _parse_binary(stack_raw["binary"], stack_raw["id"], components)
        stacks.append(
            Stack(
                id=stack_raw["id"],
                name=stack_raw.get("name", stack_raw["id"]),
                summary=stack_raw.get("summary", ""),
                components=components,
                main=stack_raw.get("main", ""),
                binary=binary,
            )
        )
    result = tuple(stacks)
    _validate_graph(result)
    return result


# ---- controller identity (LHPC's OWN checkout; a dedicated non-stack entity) --------------

CONTROLLER_KEYS = frozenset({"id", "display_name", "source_path", "branch", "remote"})
CONTROLLER_SOURCE_PATH = "src/loraham-pi-control"
CONTROLLER_BRANCH = "main"


def parse_controller(data: dict, known_ids: set[str] | None = None) -> ControllerSpec | None:
    """Parse the SINGLE top-level `[controller]` table into a `ControllerSpec`, or None if
    absent. STRICT: an EXACT allow-list (any unknown key OR nested sub-table -> typed
    error), a FIXED `source_path`/`branch`, and no id collision with any stack/component.
    This is a dedicated identity — it is NEVER fed through stack/source machinery."""
    raw = data.get("controller")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ManifestError("[controller] must be a single table")
    # An `[[controller]]` array-of-tables decodes as a list; a nested `[controller.x]`
    # decodes as a dict value under a key not in the allow-list -> both rejected below.
    unknown = set(raw) - CONTROLLER_KEYS
    if unknown:
        raise ManifestError(
            f"[controller]: unknown key(s) {sorted(unknown)} — allowed only "
            f"{sorted(CONTROLLER_KEYS)}")
    for key in CONTROLLER_KEYS:
        val = raw.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ManifestError(f"[controller].{key} must be a non-empty string")
    if raw["source_path"] != CONTROLLER_SOURCE_PATH:
        raise ManifestError(
            f'[controller].source_path must be exactly "{CONTROLLER_SOURCE_PATH}"')
    if raw["branch"] != CONTROLLER_BRANCH:
        raise ManifestError(f'[controller].branch must be exactly "{CONTROLLER_BRANCH}"')
    if known_ids and raw["id"] in known_ids:
        raise ManifestError(
            f"[controller].id {raw['id']!r} collides with a stack/component id")
    return ControllerSpec(id=raw["id"], display_name=raw["display_name"],
                          source_path=raw["source_path"], branch=raw["branch"],
                          remote=raw["remote"])


def load_controller(path: Path | None = None) -> ControllerSpec | None:
    """Load the manifest and return its `ControllerSpec` (or None). Validates the id does
    not collide with any stack/component id. Controller state travels ONLY through this separate accessor."""
    _stacks, controller = _load_stacks_and_controller(path)
    return controller


_SHELL_OPS = ("&&", "||", "|", ";", "$(", "`", ">", "<", "${", "&")
_SHELL_WORDS = {"cd", "env", "export", "exec", "sleep", "mkdir", "chmod", "ln", "rm", "set"}


def _is_simple(cmd: str) -> bool:
    """True if a manifest command is a plain `prog arg arg` line with no shell
    syntax — safe to tokenize on whitespace into a structured argv at parse time.
    Shell control WORDS (cd/env/…) are matched as whole tokens, not substrings, so
    `meshtasticd` (which contains 'cd') is not misclassified."""
    if not cmd or any(op in cmd for op in _SHELL_OPS):
        return False
    return not any(t in _SHELL_WORDS for t in cmd.split())


def _tok(t: str) -> str:
    """Map a `{name}` placeholder to its structured token form."""
    if t.startswith("{") and t.endswith("}") and t.count("{") == 1 and "/" not in t:
        name = t[1:-1]
        if name == "callsign":
            return "{operator:" + name + "}"
        if name in ("runtime", "source", "band"):
            return t
        return "{param:" + name + "}"
    return t


def _derive_structured(raw: dict) -> None:
    """Fill the structured run/build/test fields from the `run`/`build`/`test` shorthand — the
    authoring form for a plain `prog arg arg` command. Shell syntax has no shorthand and no
    shell fallback: it is written as explicit run_argv/build_steps/test_argv, and a shorthand
    that needs a shell is refused at parse time."""
    for key, field in (("run", "run_argv"), ("build", "build_steps"), ("test", "test_argv")):
        cmd = raw.get(key, "")
        if not cmd or raw.get(field):
            continue
        if not _is_simple(cmd):
            raise ManifestError(f"component {raw.get('id', '?')!r}: `{key}` shorthand uses shell "
                                f"syntax — write `{field}` explicitly")
        if key == "run":
            raw["run_argv"] = [_tok(t) for t in cmd.split()]
            raw.setdefault("run_cwd", "{source}")
        elif key == "build":
            raw["build_steps"] = [{"argv": cmd.split()}]
        else:
            raw["test_argv"] = cmd.split()


_PATCH_SCRIPT = "/openhop-apply-patch.sh"


def _with_patches(source, build_steps):
    """Record on the SourceSpec the LHPC-shipped patch files a build step applies to the
    checkout (`openhop-apply-patch.sh <source> {asset}/patches/<x>.patch`), so status and the
    update's overwrite gate can tell LHPC's own modifications from an operator's."""
    if source is None:
        return None
    pats = tuple(str(t) for st in build_steps for argv in [st.get("argv", [])]
                 if any(str(a).endswith(_PATCH_SCRIPT) for a in argv)
                 for t in argv if str(t).startswith("{asset}/patches/"))
    return dataclasses.replace(source, patches=pats) if pats else source


# A build input NAME is an identifier and a VALUE is a printable one-line scalar: both end up
# verbatim in the build-input sidecar, which is compared byte-for-byte, so a newline or a stray
# control character there would make a record that can never match what `is_built` recomputes.
_BUILD_INPUT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_BUILD_INPUT_VALUE = re.compile(r"[!-~][ -~]*\Z")


def _step_command(step: dict) -> str:
    """The program a build step RUNS: argv[0]'s file name, or the script's when the step is
    launched through `bash <script>` — the only wrapper shape the recipes use. Nothing more
    general: the two consumers below are a pip install and one shipped fetch script."""
    argv = [str(t) for t in step.get("argv", [])]
    if not argv:
        return ""
    if Path(argv[0]).name in ("bash", "sh") and len(argv) > 1:
        return Path(argv[1]).name
    return Path(argv[0]).name


def _parse_build_inputs(raw: dict) -> tuple[tuple[str, str], ...]:
    """`build_inputs = [{ name, value, command, token }]` — the non-source inputs recorded
    beside the completion marker. Declaring one without a `build_marker` is a manifest error
    rather than a silent no-op: the sidecar is written next to the marker, so without a marker
    there is nowhere for it to live and nothing for `is_built` to compare.

    `command` names the build step that CONSUMES the value (`"pip"`, `"meshtastic-web-assets.sh"`
    — see `_step_command`), and `token` is the argv token the value fills, with `{value}` where
    the value goes (`"meshtastic=={value}"` for a pip pin, the bare `"{value}"` for a version
    passed as a token of its own). Only `{value}` is substituted, so a token may carry LHPC's
    own `{runtime}`/`{source}` placeholders verbatim.

    The rendered string must be EXACTLY ONE argv token of the steps running that command."""
    items = raw.get("build_inputs", [])
    if not items:
        return ()
    cid = raw.get("id", "?")
    if not raw.get("build_marker"):
        raise ManifestError(f"component {cid!r} declares build_inputs without a build_marker")
    steps = raw.get("build_steps", [])
    out, seen = [], set()
    for entry in items:
        if not isinstance(entry, dict):
            raise ManifestError(f"component {cid!r} build_inputs entry must be a table")
        name, value = str(entry.get("name", "")), str(entry.get("value", ""))
        command, token = str(entry.get("command", "")), str(entry.get("token", ""))
        if not _BUILD_INPUT_NAME.match(name):
            raise ManifestError(f"component {cid!r} build_input name {name!r} is not an identifier")
        if not _BUILD_INPUT_VALUE.match(value):
            raise ManifestError(
                f"component {cid!r} build_input {name!r} value {value!r} must be a printable "
                f"single line")
        if name in seen:
            raise ManifestError(f"component {cid!r} declares build_input {name!r} twice")
        seen.add(name)
        # THE anti-drift rule. A build input mirrors a literal in a build step, and two copies
        # of one version can disagree: the marker would then record a value the build never
        # used, and `is_built` would answer about the wrong thing. So the recorded value is
        # bound to the token of the step that actually consumes it — the CONSUMER is selected
        # first, and only then is the token compared.
        #
        # Selecting the consumer is what a token match alone does not do. Matching a rendered
        # token anywhere in the recipe accepted a recorded CLI pin of "2.7.11" while the pip
        # step installed `meshtastic==2.7.110`, because a decoy elsewhere (a `printf
        # meshtastic==2.7.11`, another tool's argument) carried the same full token; and the
        # bare `"{value}"` of the web entry was satisfied by any unrelated bare version.
        if not command or "{value}" not in token:
            raise ManifestError(
                f"component {cid!r} build_input {name!r} must name the build step that consumes "
                f"it and the argv token it fills, e.g. command = \"pip\", token = "
                f"\"meshtastic=={{value}}\" — got command={command!r}, token={token!r}")
        want = token.replace("{value}", value)
        n = sum([str(t) for t in st.get("argv", [])].count(want)
                for st in steps if _step_command(st) == command)
        if n != 1:
            raise ManifestError(
                f"component {cid!r} build_input {name!r} = {value!r} is not what the recipe "
                f"consumes: {want!r} must be exactly one argv token of a build step running "
                f"{command!r}, and {n} such tokens exist. The recorded value must be the one "
                f"that step consumes — a matching token in another command is a different "
                f"value, and two of them are ambiguous.")
        out.append((name, value))
    return tuple(out)


def _parse_component(raw: dict) -> Component:
    _derive_structured(raw)
    return Component(
        id=raw["id"],
        name=raw.get("name", raw["id"]),
        kind=ComponentKind(raw.get("kind", "service")),
        purpose=raw.get("purpose", ""),
        band=str(raw.get("band", "")),
        tx_capable=raw.get("tx_capable", False),
        resources=tuple(_parse_resource(r) for r in raw.get("resource", [])),
        units=tuple(_parse_unit(u) for u in raw.get("unit", [])),
        process=_parse_process(raw.get("process")),
        endpoints=tuple(_parse_endpoint(e) for e in raw.get("endpoint", [])),
        depends_on=tuple(raw.get("depends_on", [])),
        build_requires=tuple(raw.get("build_requires", [])),
        build_inputs=_parse_build_inputs(raw),
        source=_with_patches(_parse_source(raw.get("source")), raw.get("build_steps", [])),
        log_paths=tuple(raw.get("log_paths", [])),
        start_order=raw.get("start_order"),
        note=raw.get("note", ""),
        start_note=raw.get("start_note", ""),
        build_root=raw.get("build_root", ""),
        release_repo=raw.get("release_repo", ""),
        ui_user=raw.get("ui_user", ""),
        ui_password_file=raw.get("ui_password_file", ""),
        ui_password_note=str(raw.get("ui_password_note", "") or ""),
        run_argv=tuple(str(t) for t in raw.get("run_argv", [])),
        run_cwd=raw.get("run_cwd", ""),
        run_env=tuple((str(k), str(v)) for k, v in raw.get("run_env", {}).items()),
        pre_steps=tuple(dict(s) for s in raw.get("pre_steps", [])),
        post_steps=tuple(dict(s) for s in raw.get("post_steps", [])),
        build_steps=tuple(dict(s) for s in raw.get("build_steps", [])),
        test_argv=tuple(str(t) for t in raw.get("test_argv", [])),
        test_requires_running=bool(raw.get("test_requires_running", False)),
        readiness=raw.get("readiness", ""),
        readiness_timeout=float(raw.get("readiness_timeout", 0.0) or 0.0),
        bin=raw.get("bin", ""),
        build_timeout=float(raw.get("build_timeout", 0.0) or 0.0),
        test_timeout=float(raw.get("test_timeout", 0.0) or 0.0),
        build_marker=raw.get("build_marker", ""),
        requires=tuple(
            Requirement(cmd=r.get("cmd", ""), install=r.get("install", ""),
                        check_file=r.get("check_file", ""), note=r.get("note", ""),
                        groups=tuple(r.get("groups", [])),
                        absent_file=r.get("absent_file", ""),
                        provisioned=bool(r.get("provisioned", False)),
                        module=_require_module(r.get("module", ""), raw.get("id", "?")),
                        gui=bool(r.get("gui", False)),
                        gps=bool(r.get("gps", False)))
            for r in raw.get("require", [])
        ),
        optional=raw.get("optional", False),
        gui_optional=raw.get("gui_optional", False),
        test_fixture=raw.get("test_fixture", False),
        reads_position=raw.get("reads_position", False),
        run_params=tuple(_parse_param(p, raw.get("id", "?"))
                         for p in raw.get("param", [])),
        requires_daemon_tx=raw.get("requires_daemon_tx", ""),
        interactive=raw.get("interactive", False),
        bands=tuple(str(b) for b in raw.get("bands", [])),
        config_file=_parse_file_config(raw.get("config_file")),
        show_config_link=raw.get("show_config_link"),
        show_log_link=raw.get("show_log_link"),
    )


_PARAM_KEYS = frozenset((
    "name", "kind", "choices", "default", "flag", "label",
    "choice_labels",   # accepted and ignored: the config-default migration parses the previous
                       # release's manifest (service_params), which carries it
    "min", "max", "advanced", "arg", "apply_mode", "band_defaults",
    "validator", "group",
))


def _parse_param(p: dict, cid: str) -> RunParam:
    # FAIL CLOSED on stray keys: a bare key placed AFTER a [[…param]] table binds to that
    # table in TOML, so a misplaced component scalar (note, test, …) would otherwise be
    # silently swallowed here.
    stray = set(p) - _PARAM_KEYS
    if stray:
        raise ManifestError(
            f"{cid}: param {p.get('name', '?')!r} has unknown key(s) "
            f"{sorted(stray)} — a component-level key placed after a [[…param]] table "
            f"binds to that table; move it above the param tables.")
    return RunParam(
        name=p["name"], kind=p.get("kind", "enum"),
        choices=tuple(str(c) for c in p.get("choices", [])),
        default=str(p.get("default", "")),
        flag=p.get("flag", ""), label=p.get("label", ""),
        min=p.get("min"), max=p.get("max"),
        advanced=p.get("advanced", False),
        arg=p.get("arg", ""), apply_mode=p.get("apply_mode", "restart"),
        band_defaults=tuple((str(k), str(v))
                            for k, v in p.get("band_defaults", {}).items()),
        validator=p.get("validator", ""),
        group=p.get("group", ""),
    )


def _parse_resource(raw: dict) -> ResourceClaim:
    return ResourceClaim(
        key=raw["key"],
        kind=ResourceKind(raw["kind"]),
        mode=ResourceMode(raw.get("mode", "exclusive")),
        requirement=raw.get("requirement", ""),
        note=raw.get("note", ""),
        advisory=bool(raw.get("advisory", False)),
    )


def _parse_unit(raw: dict) -> UnitRef:
    return UnitRef(name=raw["name"], scope=SystemdScope(raw.get("scope", "system")))


def _parse_process(raw: dict | None) -> ProcessSpec | None:
    if not raw:
        return None
    return ProcessSpec(
        exec_name=raw["exec_name"],
        all_args=tuple(raw.get("all_args", [])),
        any_args=tuple(raw.get("any_args", [])),
    )


def _parse_endpoint(raw: dict) -> EndpointSpec:
    return EndpointSpec(
        kind=raw["kind"],
        address=raw["address"],
        role=raw.get("role", "listener"),
        readiness=raw.get("readiness", "none"),
        ready=raw.get("ready", False),
        external=raw.get("external", False),
        description=raw.get("description", ""),
        client=raw.get("client", False),
        scheme=raw.get("scheme", ""),
        firewall=_parse_firewall_meta(raw.get("firewall")),
        proxy_deny_paths=_parse_proxy_deny_paths(raw.get("proxy_deny_paths")),
    )


# A conservative safe-path charset so an entry can only ever be a literal nginx
# `location = <path>`: letters, digits and the URL-path punctuation `-._~/`. This rejects
# braces, semicolons, whitespace and control characters that would otherwise be interpolated
# verbatim into the generated nginx directive (manifest-authored input, but the validator's
# stated contract must actually hold).
_DENY_PATH_RE = re.compile(r"\A/[A-Za-z0-9\-._~/]*\Z")


def _parse_proxy_deny_paths(raw) -> tuple:
    """Exact request paths a web-UI proxy must refuse. Each must be an absolute path, rendered as a spelling-tolerant nginx `location ~` block (`webserver.deny_location_regex`, 404) — only `[A-Za-z0-9-._~/]`, leading '/'."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ValueError("proxy_deny_paths must be a list of strings")
    for x in raw:
        if not _DENY_PATH_RE.match(x):
            raise ValueError(
                f"proxy_deny_paths entry is not a safe absolute path "
                f"([A-Za-z0-9-._~/], leading '/'): {x!r}")
    return tuple(raw)


_FIREWALL_KEYS = {"port_param", "bind_param", "allow_param", "auth", "deny"}
_FIREWALL_AUTH = ("none", "password", "mtls", "token")


def _parse_firewall_meta(raw) -> FirewallMeta | None:
    """FAIL-CLOSED firewall metadata: unknown keys are a load error (the stray-key policy —
    a typo must never silently weaken firewall semantics), auth is enumerated, deny is a
    real boolean."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ManifestError("endpoint firewall metadata must be a table")
    unknown = set(raw) - _FIREWALL_KEYS
    if unknown:
        raise ManifestError(f"endpoint firewall metadata: unknown key(s) {sorted(unknown)}")
    auth = raw.get("auth", "none")
    if auth not in _FIREWALL_AUTH:
        raise ManifestError(f"endpoint firewall metadata: auth must be one of {_FIREWALL_AUTH}")
    deny = raw.get("deny", False)
    if not isinstance(deny, bool):
        raise ManifestError("endpoint firewall metadata: deny must be a boolean")
    return FirewallMeta(port_param=str(raw.get("port_param", "")),
                        bind_param=str(raw.get("bind_param", "")),
                        allow_param=str(raw.get("allow_param", "")),
                        auth=auth, deny=deny)


def _parse_source(raw: dict | None) -> SourceSpec | None:
    if not raw:
        return None
    if "strategy" in raw:
        # There is no per-source strategy: every managed source is a clone under the runtime
        # root. Refuse rather than accept a key that would silently mean nothing.
        raise ManifestError("source.strategy is not a manifest field — every managed source is "
                            "a clone under the runtime root; remove it")
    return SourceSpec(
        path=raw.get("path", ""),
        pin_commit=raw.get("pin_commit", ""),
        pin_tag=raw.get("pin_tag", ""),
        remote=raw.get("remote", ""),
        branch=raw.get("branch", ""),
        local_dir=raw.get("local_dir", ""),
        artifact=bool(raw.get("artifact", False)),
    )
