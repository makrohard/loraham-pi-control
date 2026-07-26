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
        return ((self.BINARY_CHANNEL,) + self.SOURCE_CHOICES) if ok else self.SOURCE_CHOICES

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

    def binary_block_reason(self, stack_id: str, action: str = "build") -> str:
        """Reason string for actions a binary-installed stack cannot do (build, host tests,
        HMAC changes), or "" when the action is fine."""
        if not self.on_binary_channel(stack_id):
            return ""
        return (f"{stack_id} is installed from a prebuilt binary — {action} needs the source "
                f"channel (install it with --source pinned first)")
