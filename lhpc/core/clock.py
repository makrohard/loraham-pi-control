"""Clock policy, from explicit inputs (the kernel's sync state, `now`). It reads no file or host
(the kernel probe is `service_system.read_kernel_time_state`); it reads the clock only when `now`
is not given, and only after the kernel checks — the order the old function had."""
from __future__ import annotations

import time

# Nothing lhpc writes can predate the commit that introduced this check. A realtime clock reading
# before this is not merely unsynchronised, it is demonstrably wrong.
# The earliest date this software can plausibly run. PUBLIC and purpose-named because it is
# shared: the clock gate below, the Time row's "bad" floor, and provisional PKI issuance (pki.py),
# which uses it as the fixed notBefore of material minted while the clock is unverified.
PKI_NOT_BEFORE = 1_735_689_600   # 2025-01-01T00:00:00Z
GREEN_MAXERROR_US = 1_000_000    # <= 1 s of estimated error: the gate's bound, the Time row's green


# --- the clock gate the PKI uses -------------------------------------------------------------
# ONE predicate, so nothing downstream has to invent what "verified" means. It answers a narrower
# question than the Time row: not "is this clock good enough to show the operator", but "may this
# clock date material that outlives the boot".
#
# What it CANNOT do, stated so nothing claims otherwise: prove the time is CORRECT. LHPC reads
# the kernel's synchronisation evidence, not a trusted date. A source that is synchronised and
# wrong passes this gate, and a rolled-back GPS receiver is exactly such a source. The property
# actually enforced is "unverified or unsynchronised time may not mutate the PKI".
# NOTE: verdict() deliberately uses NO filesystem timestamps. See its docstring.
def verdict(kernel: dict | None, now: float | None = None) -> tuple[bool, str]:
    """(ok, reason) for the kernel state `kernel` ({"synced": bool, "maxerror_us": int}, or None
    when it could not be read) at the realtime `now` (when None: the clock, read after the kernel
    checks). `reason` is operator-facing and names what failed, never just "bad clock"; it is ""
    when ok.

    Three conditions, all from evidence that already exists:
      1. the kernel says synchronised (not STA_UNSYNC, and ntp_adjtime did not return TIME_ERROR);
      2. maxerror is inside GREEN_MAXERROR_US -- the same bound the Time row calls green;
      3. the clock is at or above PKI_NOT_BEFORE -- a STABLE compile-time lower bound.

    **File mtimes are deliberately NOT an input, and this is the correction that matters.** An
    earlier version took the newest mtime among the runtime and PKI paths as a floor, reasoning
    that the clock cannot legitimately read earlier than something this box has written. That is
    circular: those timestamps were produced by the same possibly-wrong clock. A CRL minted while
    the clock was a year fast has a file mtime a year in the future, so the floor then rejects the
    CORRECTED time -- and CRL repair, which needs a verified clock, refuses to replace the very
    file that is locking the operator out. The box stays locked out until the erroneous future
    date, and no operator override helps because the watchdog is unattended.

    A floor is only useful if it is independent of the thing being checked. `PKI_NOT_BEFORE` is;
    mtimes are not. They remain fine as DIAGNOSTICS -- the Time row still uses its write floor to
    label an obviously implausible clock -- but they must not authorise issuance or repair.

    _CLOCK_EPOCH_FLOOR from the GPS time source is deliberately NOT an input: this must hold on a
    box that never re-ran bootstrap and has no such floor.
    """
    if kernel is None:
        return False, "kernel time state unavailable — cannot tell whether the clock is synchronised"
    if not kernel["synced"]:
        return False, "the clock is not synchronised (no time source has set it yet)"
    maxerror = int(kernel["maxerror_us"])
    if maxerror > GREEN_MAXERROR_US:
        return False, (f"the clock's estimated error is {maxerror / 1_000_000:.1f} s, above the "
                       f"{GREEN_MAXERROR_US / 1_000_000:.0f} s this needs")
    stamp = time.time() if now is None else now
    if stamp < float(PKI_NOT_BEFORE):
        return False, ("the clock reads "
                       f"{time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(stamp))}, before the "
                       "earliest date this software can plausibly run")
    return True, ""


# The operator's way past it. ONE-SHOT and NON-PERSISTENT by construction: it is a parameter on
# the call, never a stored setting, so it cannot leak into the next operation. It is also
# separate from every destructive confirmation in the codebase -- "yes, replace my certificates"
# and "yes, I accept that this box's clock is unverified" are different statements, and treating
# one as the other is how an operator ends up with certificates dated to 1970 they never agreed
# to. The flag is named for what the operator accepts.
CLOCK_OVERRIDE_FLAG = "--accept-unverified-clock"


def clock_refusal(reason: str, what: str) -> str:
    """The refusal text. Names the clock as the cause, what was NOT done, and the exact way to
    proceed anyway -- a refusal the operator cannot act on is a dead end, not a safeguard."""
    return (f"refusing to {what}: {reason}. Nothing was changed. "
            f"Fix the clock (see `lhpc doctor`), or accept the risk with {CLOCK_OVERRIDE_FLAG}.")
