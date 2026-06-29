"""Manifest loading.

The manifest is the central, version-controllable description of every stack and
component. The loader parses the schema (per-band daemons, structured process
identity, probeable endpoints, resource compatibility modes, source pins and
runtime dependencies) into the `model` dataclasses.

Configuration layering (see docs/architecture.md):
  1. tracked defaults        -> config/manifest.example.toml (this loader's default)
  2. known-good profiles     -> config/profiles.example.toml
  3. generated runtime state -> under the runtime root
  4. user-local overrides    -> config/local.toml   (git-ignored)
  5. secrets                 -> config/secrets.toml (git-ignored)

Uses the stdlib `tomllib` (Python 3.11+). Read-only: it never writes or fetches.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from .model import (
    Component,
    ComponentKind,
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
        )
        for p in raw.get("param", [])
    )
    return FileConfig(path=raw["path"], fmt=raw.get("fmt", "keyval"),
                      base=raw.get("base", ""), apply_cmd=raw.get("apply_cmd", ""),
                      params=params)

_DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "config" / "manifest.example.toml"


def default_manifest_path() -> Path:
    return _DEFAULT_MANIFEST


def load_manifest(path: Path | None = None) -> tuple[Stack, ...]:
    """Load and parse the manifest into Stack/Component objects (read-only)."""
    manifest_path = path or _DEFAULT_MANIFEST
    with manifest_path.open("rb") as fh:
        data = tomllib.load(fh)
    return parse_manifest(data)


def parse_manifest(data: dict) -> tuple[Stack, ...]:
    """Parse an already-loaded TOML mapping (kept separate for testing)."""
    stacks: list[Stack] = []
    for stack_raw in data.get("stack", []):
        components = tuple(
            _parse_component(c) for c in stack_raw.get("component", [])
        )
        stacks.append(
            Stack(
                id=stack_raw["id"],
                name=stack_raw.get("name", stack_raw["id"]),
                summary=stack_raw.get("summary", ""),
                components=components,
                main=stack_raw.get("main", ""),
            )
        )
    return tuple(stacks)


def _parse_component(raw: dict) -> Component:
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
        source=_parse_source(raw.get("source")),
        log_paths=tuple(raw.get("log_paths", [])),
        start_order=raw.get("start_order"),
        note=raw.get("note", ""),
        build_cmd=raw.get("build", ""),
        run_cmd=raw.get("run", ""),
        test_cmd=raw.get("test", ""),
        pre_cmd=raw.get("pre", ""),
        post_start=raw.get("post_start", ""),
        bin=raw.get("bin", ""),
        requires=tuple(
            Requirement(cmd=r.get("cmd", ""), install=r.get("install", ""),
                        check_file=r.get("check_file", ""), note=r.get("note", ""))
            for r in raw.get("require", [])
        ),
        optional=raw.get("optional", False),
        run_params=tuple(
            RunParam(
                name=p["name"], kind=p.get("kind", "enum"),
                choices=tuple(str(c) for c in p.get("choices", [])),
                default=str(p.get("default", "")),
                flag=p.get("flag", ""), label=p.get("label", ""),
                min=p.get("min"), max=p.get("max"),
                advanced=p.get("advanced", False),
                arg=p.get("arg", ""), apply_mode=p.get("apply_mode", "restart"),
                band_defaults=tuple((str(k), str(v))
                                    for k, v in p.get("band_defaults", {}).items()),
            )
            for p in raw.get("param", [])
        ),
        requires_daemon_tx=raw.get("requires_daemon_tx", ""),
        interactive=raw.get("interactive", False),
        bands=tuple(str(b) for b in raw.get("bands", [])),
        config_file=_parse_file_config(raw.get("config_file")),
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
    )
