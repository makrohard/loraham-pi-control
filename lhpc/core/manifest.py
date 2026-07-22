"""Manifest loading.

The manifest is the central, version-controllable description of every stack and
component. The loader parses the schema (per-band daemons, structured process
identity, probeable endpoints, resource compatibility modes, source pins and
runtime dependencies) into the `model` dataclasses.

Configuration layering (see docs/architecture.md):
  1. tracked defaults        -> lhpc/data/manifest.example.toml (shipped package data)
  2. known-working compositions -> runtime profiles/known-working/ (operator-confirmed)
  3. generated runtime state -> under the runtime root
  4. user-local overrides    -> <runtime>/config/local.toml   (git-ignored)
  5. secrets                 -> <runtime>/config/secrets.toml (git-ignored)

Uses the stdlib `tomllib` (Python 3.11+). Read-only: it never writes or fetches.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from . import commands
from .assets import asset_path
from .model import (
    Component,
    ComponentKind,
    ControllerSpec,
    EndpointSpec,
    FileConfig,
    FileParam,
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
        if value.startswith("/") or value.startswith("{") or ".." in value.split("/"):
            raise ManifestError(
                f"{label} must be '{{runtime}}/...', '{{asset}}/...' (base only) or a relative "
                f"source path, got {value!r}")
    return FileConfig(path=path, fmt=raw.get("fmt", "keyval"),
                      base=base, apply_cmd=raw.get("apply_cmd", ""),
                      params=params)

_DEFAULT_MANIFEST = asset_path("manifest.example.toml")   # package data (wheel-safe)


def default_manifest_path() -> Path:
    return _DEFAULT_MANIFEST


def load_manifest(path: Path | None = None) -> tuple[Stack, ...]:
    """Load and parse the manifest into Stack/Component objects (read-only). Also VALIDATES
    any present `[controller]` table (strict parser, result discarded) so an invalid
    controller declaration is rejected here — not silently ignored by dashboard, bootstrap,
    auto-install, or normal stack paths. The `tuple[Stack, ...]` return contract is unchanged."""
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


_READINESS = {"process", "endpoint", "daemon-band", "manual", "external-systemd"}
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
        elif inner not in ("band", "runtime", "source"):
            raise ManifestError(f"{cid}: unknown placeholder {tok!r}")
        return
    stripped = tok.replace("{runtime}", "").replace("{source}", "").replace("{band}", "")
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
            raise ManifestError(f"{cid}: malformed tcp endpoint address {e.address!r}")
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
    if comp.source and (comp.source.strategy or "") == "link":
        # CONTAINMENT: external link sources are not permitted — every source lives
        # under the runtime root as a managed clone. (The link machinery stays in code
        # so a LEGACY runtime symlink leaf is still recognized and refused safely.)
        raise ManifestError(f'{cid}: strategy="link" (external link source) is not '
                            "permitted — every source lives under the runtime root")
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
            tok = str(tok)
            if tok.startswith("{pkgconfig:") and tok.endswith("}"):
                continue            # build-only placeholder (resolved via pkg-config)
            if tok == "{asset}" or tok.startswith("{asset}/"):
                # Build-only placeholder: a helper SHIPPED as lhpc package data, for steps whose
                # logic cannot live in an upstream checkout. Resolved (and path-validated) by
                # commands._asset_token; read-only by construction.
                continue
            _check_token(cid, tok, names)
        # Optional quiet-step preamble written into the step log at step start: a string with
        # only {runtime}/{source} placeholders. Validated EAGERLY (dry-run substitution) so a
        # typo'd placeholder fails at manifest load, not minutes into a build.
        if "announce" in step:
            ann = step["announce"]
            if not isinstance(ann, str) or not ann.strip():
                raise ManifestError(f"{cid}: build-step announce must be a non-empty string")
            try:
                commands._paths_subst(ann, "r", "s", "")
            except commands.CommandError as exc:
                raise ManifestError(f"{cid}: build-step announce: {exc}")
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
    color = {cid: WHITE for cid in comp_of}

    def _visit(cid: str, path: list[str]) -> None:
        color[cid] = GRAY
        for dep in comp_of[cid].depends_on:
            if color[dep] == GRAY:                     # dep is on the current stack -> cycle
                i = path.index(dep)
                raise ManifestError("dependency cycle: " + " -> ".join(path[i:] + [dep]))
            if color[dep] == WHITE:
                _visit(dep, path + [dep])
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
                 c.source.branch, c.source.remote, c.source.strategy)
        prev = by_path.get(c.source.path)
        if prev is None:
            by_path[c.source.path] = (cid, ident)
        elif prev[1] != ident:
            raise ManifestError(
                f"components {prev[0]!r} and {cid!r} share source path "
                f"{c.source.path!r} but declare different source specs "
                "(pin/tag/branch/remote/strategy/artifact must be identical)")


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
        stacks.append(
            Stack(
                id=stack_raw["id"],
                name=stack_raw.get("name", stack_raw["id"]),
                summary=stack_raw.get("summary", ""),
                components=components,
                main=stack_raw.get("main", ""),
                operator_box=bool(stack_raw.get("operator_box", True)),
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
    not collide with any stack/component id. The `load_manifest` stack contract is
    UNCHANGED — controller state travels ONLY through this separate accessor."""
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
    """Map a legacy `{name}` placeholder to a structured token form."""
    if t.startswith("{") and t.endswith("}") and t.count("{") == 1 and "/" not in t:
        name = t[1:-1]
        if name == "callsign":
            return "{operator:" + name + "}"
        if name in ("runtime", "source", "band"):
            return t
        return "{param:" + name + "}"
    return t


def _derive_structured(raw: dict) -> None:
    """Fill structured run/build/test fields from simple legacy command strings, so
    every shipped component executes shell-free. Commands with shell syntax must be
    migrated to explicit run_argv/build_steps in the manifest (no shell fallback)."""
    if not raw.get("run_argv") and _is_simple(raw.get("run", "")):
        raw["run_argv"] = [_tok(t) for t in raw["run"].split()]
        raw.setdefault("run_cwd", "{source}")
    if not raw.get("build_steps") and _is_simple(raw.get("build", "")):
        raw["build_steps"] = [{"argv": raw["build"].split()}]
    if not raw.get("test_argv") and _is_simple(raw.get("test", "")):
        raw["test_argv"] = raw["test"].split()


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
        source=_parse_source(raw.get("source")),
        log_paths=tuple(raw.get("log_paths", [])),
        start_order=raw.get("start_order"),
        note=raw.get("note", ""),
        start_note=raw.get("start_note", ""),
        build_cmd=raw.get("build", ""),
        run_cmd=raw.get("run", ""),
        test_cmd=raw.get("test", ""),
        pre_cmd=raw.get("pre", ""),
        post_start=raw.get("post_start", ""),
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
                        gui=bool(r.get("gui", False)))
            for r in raw.get("require", [])
        ),
        optional=raw.get("optional", False),
        run_params=tuple(_parse_param(p, raw.get("id", "?"))
                         for p in raw.get("param", [])),
        requires_daemon_tx=raw.get("requires_daemon_tx", ""),
        interactive=raw.get("interactive", False),
        bands=tuple(str(b) for b in raw.get("bands", [])),
        config_file=_parse_file_config(raw.get("config_file")),
    )


_PARAM_KEYS = frozenset((
    "name", "kind", "choices", "choice_labels", "default", "flag", "label",
    "min", "max", "advanced", "arg", "apply_mode", "band_defaults",
    "validator", "group",
))


def _parse_param(p: dict, cid: str) -> RunParam:
    # FAIL CLOSED on stray keys: a bare key placed AFTER a [[…param]] table binds to that
    # table in TOML, so a misplaced component scalar (note, test, …) would otherwise be
    # silently swallowed here — exactly the trap that lost a component note once.
    stray = set(p) - _PARAM_KEYS
    if stray:
        raise ManifestError(
            f"{cid}: param {p.get('name', '?')!r} has unknown key(s) "
            f"{sorted(stray)} — a component-level key placed after a [[…param]] table "
            f"binds to that table; move it above the param tables.")
    return RunParam(
        name=p["name"], kind=p.get("kind", "enum"),
        choices=tuple(str(c) for c in p.get("choices", [])),
        choice_labels=tuple((str(pair[0]), str(pair[1]))
                            for pair in p.get("choice_labels", []) if len(pair) == 2),
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
        group=raw.get("group", ""),
        requirement=raw.get("requirement", ""),
        note=raw.get("note", ""),
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
    )


def _parse_source(raw: dict | None) -> SourceSpec | None:
    if not raw:
        return None
    return SourceSpec(
        path=raw.get("path", ""),
        pin_commit=raw.get("pin_commit", ""),
        pin_tag=raw.get("pin_tag", ""),
        remote=raw.get("remote", ""),
        branch=raw.get("branch", ""),
        local_dir=raw.get("local_dir", ""),
        strategy=raw.get("strategy", ""),
        artifact=bool(raw.get("artifact", False)),
    )
