"""Read-only probe layer.

Probes turn the system's real state into evidence, behind injectable backend
interfaces so production logic is testable without hardware or live services.
Every probe is bounded (short timeouts), performs no network access, no build,
no mutation and no RF, and turns every error into evidence rather than an
exception.

The composition that maps probe results onto component status lives in
`lhpc.core.status`; conflict interpretation lives in `lhpc.core.resources`.
"""

from .backends import (
    CommandResult,
    CommandRunner,
    FakeSystem,
    RealSystem,
    System,
)

__all__ = [
    "CommandResult",
    "CommandRunner",
    "System",
    "RealSystem",
    "FakeSystem",
]
