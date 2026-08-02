"""Binary channel — resolution helpers and coverage predicates (B3).

The "binary" channel is a fourth SELECTOR beside pinned/dev/stable, never a persisted
preference: a stack that DECLARES `[stack.binary]` and runs on a supported target defaults to
it, and a valid receipt keeps subsequent updates on it. Everything else — the source planners,
`adopt_source`, provenance — keeps seeing only the three source selectors.

This mixin owns the cheap questions ("may this stack use binary?", "is it on binary NOW?",
"which components does the artifact cover?") so no caller has to reach into the receipt module
or re-derive manifest facts.
"""

from __future__ import annotations

import platform

from . import binary_receipt as brx

# The only target the builder publishes for today; the index entry is checked against this at
# install time as well (a mismatch is a typed refusal, never a "try it and see").
_SUPPORTED_TARGETS = ("aarch64-trixie",)


class BinaryChannelMixin:

    # ---- capability ------------------------------------------------------------------------

    def binary_spec(self, stack_id: str):
        """The stack's `[stack.binary]` declaration, or None when it has no binary channel."""
        st = self.stack(stack_id)
        return getattr(st, "binary", None) if st is not None else None

    def binary_target(self) -> str:
        """This box's artifact target id, e.g. 'aarch64-trixie' ('' when unsupported)."""
        machine = (platform.machine() or "").lower()
        if machine not in ("aarch64", "arm64"):
            return ""
        osrel = ""
        try:
            with open("/etc/os-release", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("VERSION_CODENAME="):
                        osrel = line.split("=", 1)[1].strip().strip('"')
                        break
        except OSError:
            return ""
        return f"aarch64-{osrel}" if osrel else ""

    def binary_available(self, stack_id: str) -> tuple[bool, str]:
        """(available, reason). Declaration + platform only — no network, no index fetch, so
        this is safe on every GET/dashboard render."""
        spec = self.binary_spec(stack_id)
        if spec is None:
            return False, "no prebuilt binary is published for this stack"
        target = self.binary_target()
        if not target:
            return False, "this platform is not a supported binary target"
        if target not in _SUPPORTED_TARGETS:
            return False, f"no binary is published for {target}"
        return True, ""

    def allowed_channels(self, stack_id: str) -> tuple[str, ...]:
        """Selectors this stack may be installed/updated with, binary first when available."""
        ok, _why = self.binary_available(stack_id)
        return ((self.BINARY_CHANNEL, *self.SOURCE_CHOICES)) if ok else self.SOURCE_CHOICES

    def default_channel(self, stack_id: str) -> str:
        """The channel a bare `install`/`update` uses. Binary WHERE AVAILABLE (that is the
        feature: a fresh Pi should not compile for hours), else the historical default."""
        ok, _why = self.binary_available(stack_id)
        return self.BINARY_CHANNEL if ok else "dev"

    def channel_error(self, stack_id: str, channel: str) -> str:
        """"" when `channel` is usable for this stack, else the typed refusal reason."""
        if channel in self.SOURCE_CHOICES:
            return ""
        if channel != self.BINARY_CHANNEL:
            # Keep the historical wording — adapters and tests match on "invalid source".
            return (f"invalid source '{channel}' (choose "
                    f"{', '.join(self.allowed_channels(stack_id))})")
        ok, why = self.binary_available(stack_id)
        return "" if ok else f"binary channel unavailable for {stack_id!r}: {why}"

    # ---- current state ---------------------------------------------------------------------

    def binary_receipt_state(self, stack_id: str) -> tuple:
        """(state, receipt|None, reason) — the cheap four-state read (absent/valid/
        superseded/unsafe). Stacks without a binary declaration are always 'absent'."""
        if self.binary_spec(stack_id) is None:
            return "absent", None, ""
        # A receipt written INSIDE an open transaction is not authoritative yet: recovery may
        # still unwind it. Report unsafe until the transaction commits (audit finding).
        from . import binary_install as _bi
        _j, _js = _bi.read_journal(self._paths)
        if _js == "unsafe":
            return "unsafe", None, ("a binary-install transaction is in an unreadable state — "
                                    "resolve state/binary/install.journal.json by hand")
        if _js == "valid" and _j is not None and _j.get("stack") == stack_id \
                and _j.get("state") != "committed":
            return "unsafe", None, (f"a binary install of {stack_id!r} is in progress or was "
                                    "interrupted — run it again to recover")
        return brx.receipt_state(self._paths, stack_id)

    def on_binary_channel(self, stack_id: str) -> bool:
        """True while the stack is installed from a binary artifact (receipt VALID)."""
        return self.binary_receipt_state(stack_id)[0] == "valid"

    def binary_covers(self, component_id: str) -> bool:
        """True when this component's source/build is currently provided by a binary artifact.
        The single primitive every predicate/gate uses — never re-derive it."""
        sid = self.stack_of(component_id) or component_id
        spec = self.binary_spec(sid)
        if spec is None or component_id not in spec.covers:
            return False
        return self.on_binary_channel(sid)

    # The remedy an artifact-managed runtime dependency needs — ONE string, so the dependency
    # report and the start refusal cannot drift apart (they did: the report said "not needed" for
    # exactly what the start gate refused to start without — audit).
    ARTIFACT_MISSING_NOTE = ("binary-managed runtime dependency is missing — the artifact was "
                             "supposed to deliver it; reinstall the binary artifact")

    def binary_requirement_class(self, comp_id: str, req) -> str:
        """THE shared verdict for one requirement of one component: "blocker" | "irrelevant" |
        "artifact-missing". Used by BOTH `start_blocking_requirements()` and `deps.stack_report()`.

        A `provisioned` requirement is installed into the runtime root by the BUILD, and on the
        binary channel there is no build — but that does not make them all irrelevant:

          * PURE BUILD TOOLS (PlatformIO) are nothing the artifact ships and nothing the operator
            can act on -> "irrelevant". Blocking start on one is a dead end: MeshCom refused to
            start with "missing pio" on a box whose firmware came prebuilt (live-found on a Zero).
          * Things the ARTIFACT delivers (the Meshtastic CLI venv, the QEMU binary) are real runtime
            prerequisites -> "artifact-missing". The manifest is explicit that a box lacking the CLI
            venv must be "refused up front instead of starting and silently failing to apply the
            region", and cheap receipt validation only restats PROOF paths, so deleting just that
            venv leaves the receipt valid — this classification is the only thing that notices.

        The receipt says which is which: a path the receipt OWNS was supposed to be delivered."""
        if not getattr(req, "provisioned", False) or not self.binary_covers(comp_id):
            return "blocker"
        state, rec, _why = self.binary_receipt_state(self.stack_of(comp_id) or comp_id)
        if state != "valid" or rec is None:
            return "irrelevant"                    # no artifact owns anything here
        owned = set(rec.files) | set(getattr(rec, "owned_dirs", ()) or ())
        root = str(self._paths.runtime_root).rstrip("/") + "/"
        path = self._lifecycle()._resolve_req_path(getattr(req, "check_file", "") or "")
        rel = path[len(root):] if path.startswith(root) else ""
        if rel and (rel in owned or any(rel.startswith(d.rstrip("/") + "/") for d in owned)):
            return "artifact-missing"
        return "irrelevant"

    def binary_artifact_repair(self, comp_id: str) -> str:
        """The one command that repairs an artifact-managed runtime dependency."""
        return f"lhpc install {self.stack_of(comp_id) or comp_id} --source binary --yes"

    def _local_gpsd_needed(self) -> bool:
        """Is a gpsd on THIS box actually part of the configured position source?

        Read defensively: this sits on the start gate and on read-only dependency views, so a
        config problem must not raise here — and "no GPS configured" is the common case.
        """
        try:
            return bool(self.config().gps.local_gpsd)
        except (OSError, ValueError, AttributeError):
            return False

    def start_blocking_requirements(self, comp) -> list:
        """`missing_requirements(comp)` minus the ones a binary artifact makes IRRELEVANT, via the
        shared classifier. Returns `Requirement` objects unchanged for ordinary blockers; an
        artifact-managed one is returned with the binary remedy substituted, so `req_remediation()`
        and every downstream renderer keep working untouched."""
        import dataclasses as _dc
        miss = self._lifecycle().missing_requirements(comp)
        # A CONDITIONAL soft dependency must never block a start. `gpsd` is only real when the
        # configured position source is a gpsd on THIS box; with the source off, remote, or
        # reading a device directly there is nothing to install, and treating it as missing
        # made a whole stack refuse to start over a package it does not need.
        miss = [r for r in miss
                if not (getattr(r, "gps", False) and not self._local_gpsd_needed())]
        if not miss or not self.binary_covers(comp.id):
            return miss
        out = []
        for req in miss:
            verdict = self.binary_requirement_class(comp.id, req)
            if verdict == "irrelevant":
                continue
            out.append(req if verdict != "artifact-missing" else
                       _dc.replace(req, install=self.binary_artifact_repair(comp.id),
                                   note=self.ARTIFACT_MISSING_NOTE))
        return out

    def binary_block_reason(self, stack_id: str, action: str = "build") -> str:
        """Reason string for actions a binary-installed stack cannot do (build, host tests,
        HMAC changes), or "" when the action is fine."""
        if not self.on_binary_channel(stack_id):
            return ""
        return (f"{stack_id} is installed from a prebuilt binary — {action} needs the source "
                f"channel (install it with --source pinned first)")
