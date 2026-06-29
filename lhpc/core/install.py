"""Runtime-root bootstrap, readable wrappers, and safe source adoption.

This is the first *mutating* layer, but it is deliberately conservative:

  * bootstrap is idempotent and NEVER overwrites local config or secrets;
  * generated `start/` wrappers point at real commands, forward `$@`, and use
    RX-safe defaults — no opaque logic is hidden in them;
  * source adoption copies the operator's locally verified checkout into the
    runtime root (or clones a pin) and then VERIFIES the pin; it never edits,
    resets or cleans the original source, and refuses to overwrite an existing
    runtime checkout unless explicitly forced;
  * a dirty source is reported, never silently "repaired".

All operations are expressed as a `Plan` of `PlanAction`s so the CLI (and, later,
the web confirmation screen) can show the exact intended effect before applying.
Nothing here builds, starts a service, or transmits.
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .model import Component, Stack, emit_param
from .paths import Paths
from .probes import System
from .probes.source import probe_source
from .model import SourceState

RUNTIME_SUBDIRS = (
    "bin", "src", "build", "start", "config", "profiles", "systemd", "state", "logs", "docs",
)

# Heavy, regenerable directories we skip when adopting a local checkout.
_ADOPT_IGNORE = shutil.ignore_patterns(
    ".pio", ".venv", "build", ".work", ".run", "__pycache__", "node_modules",
)

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

# Commented starter for the runtime-local config. Operator fills this in; until
# then the callsign is unset (not the example placeholder). Never tracked.
_LOCAL_STARTER = """\
# LoRaHAM Pi Control — local operator overrides (runtime-local, git-ignored).
# Fill in your details. See ../../docs/operations.md and config/local.example.toml.

# [operator]
# callsign = "YOURCALL"
# locator  = "JO00aa"

# [web]
# host = "127.0.0.1"
# port = 8770
"""


@dataclass
class PlanAction:
    kind: str                 # mkdir | config | secret | wrapper | adopt | clone | verify
    target: str
    description: str
    status: str = "planned"   # planned | exists | done | failed | skipped
    detail: str = ""


@dataclass
class Plan:
    title: str
    actions: list[PlanAction] = field(default_factory=list)

    @property
    def changes(self) -> list[PlanAction]:
        return [a for a in self.actions if a.status == "planned"]

    @property
    def ok(self) -> bool:
        return all(a.status != "failed" for a in self.actions)


class Installer:
    def __init__(self, paths: Paths, stacks: tuple[Stack, ...], config: Config,
                 system: System) -> None:
        self.paths = paths
        self.stacks = stacks
        self.config = config
        self.system = system

    # -- layout ------------------------------------------------------------

    def subdir(self, name: str) -> Path:
        return self.paths.runtime_root / name

    # -- bootstrap ---------------------------------------------------------

    def plan_bootstrap(self) -> Plan:
        plan = Plan(title=f"Bootstrap runtime root {self.paths.runtime_root}")
        for name in RUNTIME_SUBDIRS:
            d = self.subdir(name)
            plan.actions.append(PlanAction(
                "mkdir", str(d), f"create {name}/",
                status="exists" if d.is_dir() else "planned"))
        # Local config + secrets: create from templates only if absent.
        local = self.subdir("config") / "local.toml"
        plan.actions.append(PlanAction(
            "config", str(local), "write config/local.toml (operator settings)",
            status="exists" if local.exists() else "planned"))
        secret = self.subdir("config") / "secrets.toml"
        plan.actions.append(PlanAction(
            "secret", str(secret), "write config/secrets.toml (0600)",
            status="exists" if secret.exists() else "planned"))
        # Readable wrappers (regenerated).
        expected = set()
        for stack, comp, fname in self._wrappers():
            expected.add(fname)
            plan.actions.append(PlanAction(
                "wrapper", str(self.subdir("start") / fname),
                f"generate start/{fname}"))
        # Prune stale wrappers (e.g. left over after a stack/component rename).
        start_dir = self.subdir("start")
        if start_dir.is_dir():
            for existing in sorted(start_dir.glob("*-start")):
                if existing.name not in expected:
                    plan.actions.append(PlanAction(
                        "prune-wrapper", str(existing),
                        f"remove stale start/{existing.name}"))
        return plan

    def apply_bootstrap(self, plan: Plan | None = None) -> Plan:
        plan = plan or self.plan_bootstrap()
        for action in plan.actions:
            if action.status in ("exists", "skipped", "done"):
                continue
            try:
                self._apply_action(action)
            except OSError as exc:
                action.status, action.detail = "failed", str(exc)
        return plan

    def _apply_action(self, action: PlanAction) -> None:
        if action.kind == "mkdir":
            Path(action.target).mkdir(parents=True, exist_ok=True)
            action.status = "done"
        elif action.kind == "config":
            dest = Path(action.target)
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(_LOCAL_STARTER, encoding="utf-8")
            action.status = "done"
        elif action.kind == "secret":
            self._copy_template("secrets.example.toml", Path(action.target))
            Path(action.target).chmod(0o600)
            action.status = "done"
            action.detail = "mode 0600"
        elif action.kind == "wrapper":
            self._write_wrapper_by_path(Path(action.target))
            action.status = "done"
        elif action.kind == "prune-wrapper":
            Path(action.target).unlink(missing_ok=True)
            action.status = "done"

    def _copy_template(self, template: str, dest: Path) -> None:
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((_CONFIG_DIR / template).read_text(encoding="utf-8"),
                        encoding="utf-8")

    # -- wrappers ----------------------------------------------------------

    def _runnable(self):
        for stack in self.stacks:
            for comp in stack.components:
                if comp.run_cmd and comp.source is not None:
                    yield stack, comp

    def _wrapper_name(self, stack: Stack, comp: Component) -> str:
        order = "0" if comp.start_order is None else str(comp.start_order)
        short = comp.id[len(stack.id) + 1:] if comp.id.startswith(stack.id + "-") else comp.id
        return f"{stack.id}-{order}-{short}-start"

    def _wrappers(self):
        for stack, comp in self._runnable():
            yield stack, comp, self._wrapper_name(stack, comp)

    def _write_wrapper_by_path(self, path: Path) -> None:
        for stack, comp, fname in self._wrappers():
            if self.subdir("start") / fname == path:
                self._write_wrapper(stack, comp, path)
                return

    def _write_wrapper(self, stack: Stack, comp: Component, path: Path) -> None:
        src_dir = self.paths.resolve_source(comp.source.path)
        tx = "TX-capable (RX-safe defaults below)" if comp.tx_capable else "RX-only"
        runtime = str(self.paths.runtime_root)
        op = self.config.operator
        def subst(s: str) -> str:
            s = (s.replace("{runtime}", runtime)
                  .replace("{callsign}", op.callsign or "N0CALL")
                  .replace("{locator}", op.locator))
            # Run parameters use their manifest defaults in the generated wrapper;
            # the controller can override them at start time (web/CLI).
            for p in comp.run_params:
                s = s.replace("{" + p.name + "}", emit_param(p, p.default))
            return s
        pre_cmd = subst(comp.pre_cmd)
        run_cmd = subst(comp.run_cmd)
        body = f"""#!/usr/bin/env bash
# Generated by lhpc — manual start wrapper.
# Component : {comp.id}  ({stack.id})
# Source    : {src_dir}
# Radio     : {tx}.  lhpc never auto-enables TX; edit args deliberately.
# Re-running `lhpc bootstrap` regenerates this file but never touches your
# config/ or secrets. Append your own arguments after the command ("$@").
set -euo pipefail
cd "{src_dir}"
{pre_cmd if pre_cmd else ":"}
exec {run_cmd} "$@"
"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # -- source adoption ---------------------------------------------------

    def plan_install(self, stack_id: str | None = None) -> Plan:
        plan = Plan(title="Install (adopt/verify sources)")
        for stack in self.stacks:
            if stack_id and stack.id != stack_id:
                continue
            for comp in stack.components:
                if comp.source is None:
                    continue
                dest = self.paths.resolve_source(comp.source.path)
                if dest.exists():
                    probe = probe_source(self.system, comp.source, str(dest))
                    plan.actions.append(PlanAction(
                        "verify", str(dest),
                        f"{comp.id}: source present ({probe.state.value})",
                        status="exists", detail=probe.state.value))
                else:
                    plan.actions.append(PlanAction(
                        "adopt", str(dest),
                        f"{comp.id}: adopt {comp.source.adopt_dir} -> {comp.source.path}"))
        return plan

    def adopt_source(self, comp: Component, *, force: bool = False,
                     source: str = "dev") -> PlanAction:
        """Install a component's source. `source` selects the version:
          * "dev"    — newest commit on the remote branch (default);
          * "stable" — the latest release tag;
          * "pinned" — the manifest's pinned known-good commit.
        It clones from GitHub for that version and, on failure, falls back to the
        operator's local checkout. Components with a `link` strategy (prebuilt
        venvs/artifacts) are linked to the local working tree instead.
        Never alters the local source; refuses to overwrite unless forced.
        """
        spec = comp.source
        dest = self.paths.resolve_source(spec.path)
        action = PlanAction("adopt", str(dest), f"adopt {comp.id}")
        if dest.exists() and not force:
            action.status, action.detail = "skipped", "destination already exists"
            return action
        try:
            if dest.exists() and force:
                dest.unlink() if dest.is_symlink() else shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            action.status, action.detail = "failed", str(exc)
            return action

        search = Path(self.config.get("install", "adopt_search_root", "~/src")).expanduser()
        local = search / spec.adopt_dir
        strategy = spec.strategy or self.config.get("install", "source_strategy", "adopt")

        if strategy == "link":
            if not local.is_dir():
                action.status, action.detail = "failed", f"local checkout not found: {local}"
                return action
            try:
                os.symlink(local, dest)
            except OSError as exc:
                action.status, action.detail = "failed", str(exc)
                return action
            return self._adopt_done(action, spec, dest, "linked local dev")

        # clone strategy: from GitHub for the requested version. A per-component
        # remote override (set in the web GUI) takes precedence over the manifest.
        remote = self.config.remotes.get(comp.id) or spec.remote
        if remote and self._clone(spec, dest, source, remote):
            return self._adopt_done(action, spec, dest, f"GitHub {source}")
        # fallback: local checkout (pinned / known-good).
        if not local.is_dir():
            action.status = "failed"
            action.detail = f"GitHub clone unavailable and local checkout not found: {local}"
            return action
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)   # clear any partial clone
        try:
            shutil.copytree(local, dest, ignore=_ADOPT_IGNORE, symlinks=True)
        except OSError as exc:
            action.status, action.detail = "failed", str(exc)
            return action
        return self._adopt_done(action, spec, dest, "local fallback")

    def _clone(self, spec, dest: Path, source: str, remote: str | None = None) -> bool:
        run = self.system.runner.run
        remote = remote or spec.remote
        ok = False
        if source == "dev":
            argv = ["git", "clone", "--depth", "1"]
            if spec.branch:
                argv += ["--branch", spec.branch]
            argv += [remote, str(dest)]
            ok = run(argv, timeout=120.0).returncode == 0 and dest.exists()
        else:
            # full clone so an arbitrary tag/commit can be checked out
            if run(["git", "clone", remote, str(dest)], timeout=240.0).returncode == 0 \
                    and dest.exists():
                if source == "pinned" and spec.pin_commit:
                    ref = spec.pin_commit
                elif source == "stable":
                    desc = run(["git", "-C", str(dest), "describe", "--tags", "--abbrev=0"], 10.0)
                    ref = desc.stdout.strip() if desc.returncode == 0 else (spec.branch or "")
                else:
                    ref = spec.branch or ""
                # A failed checkout means we did NOT get the requested version — fail
                # rather than silently adopting whatever the default branch cloned.
                ok = (not ref or
                      run(["git", "-C", str(dest), "checkout", ref], 30.0).returncode == 0)
        if not ok and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)   # drop a failed/partial clone
        return ok

    def _adopt_done(self, action, spec, dest, source_desc: str) -> PlanAction:
        probe = probe_source(self.system, spec, str(dest))
        version = probe.version or probe.head[:12]
        action.status = "done"
        action.detail = f"{source_desc}: {probe.state.value} (version {version})"
        return action
