"""Resource-claim interpretation and conflict detection (read-only).

This module interprets the declared resource claims and reports both *declared*
conflicts (incompatible claims on the same resource) and *observed* conflicts
(the two claimants are actually running now). It is pure interpretation; the
service layer (`run_blockers`) uses it to block a conflicting start.

Compatibility rules (see model.ResourceMode):
  * COOPERATIVE + COOPERATIVE on the same resource  -> OK (e.g. daemon 433/868
    sharing SPI via internal serialization).
  * EXCLUSIVE vs anything else on the same resource  -> conflict.
  * PROVIDER + PROVIDER on the same resource          -> conflict (two owners).
  * CONSUMER / REQUIREMENT                             -> never a conflict source
    (consumers record a dependency; requirements are configuration constraints).
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict

from .model import Component, ResourceClaim, ResourceConflict, ResourceMode

RADIO_KEY_PREFIX = "loraham.radio."


def limit_radio_claims(comp: Component, bands: set) -> Component:
    """`comp` with its `loraham.radio.<band>` claims restricted to `bands`: only the radio claims
    whose band is in `bands` are kept, every other claim is untouched. An EMPTY `bands` strips every
    radio claim — a component whose band is unknown must never claim both radios. Whether a
    component needs restricting at all is the caller's decision (the lifecycle knows the daemon)."""
    keep = tuple(r for r in comp.resources
                 if not (r.key.startswith(RADIO_KEY_PREFIX)
                         and r.key.rsplit(".", 1)[-1] not in bands))
    return dataclasses.replace(comp, resources=keep)


def _conflicting(a: ResourceMode, b: ResourceMode) -> bool:
    blocking = {ResourceMode.EXCLUSIVE, ResourceMode.COOPERATIVE, ResourceMode.PROVIDER}
    if a not in blocking or b not in blocking:
        return False
    if a is ResourceMode.COOPERATIVE and b is ResourceMode.COOPERATIVE:
        return False  # cooperative peers co-exist
    if a is ResourceMode.EXCLUSIVE or b is ResourceMode.EXCLUSIVE:
        return True
    return bool(a is ResourceMode.PROVIDER and b is ResourceMode.PROVIDER)


def interpret_conflicts(
    components: list[Component], running_ids: set[str]
) -> list[ResourceConflict]:
    """Return declared/observed conflicts across all given components."""
    by_key: dict[str, list[tuple[str, ResourceClaim]]] = defaultdict(list)
    for comp in components:
        for claim in comp.resources:
            by_key[claim.key].append((comp.id, claim))

    conflicts: list[ResourceConflict] = []
    for key, claimants in by_key.items():
        for i in range(len(claimants)):
            for j in range(i + 1, len(claimants)):
                id_a, claim_a = claimants[i]
                id_b, claim_b = claimants[j]
                if id_a == id_b:
                    continue
                if not _conflicting(claim_a.mode, claim_b.mode):
                    continue
                observed = id_a in running_ids and id_b in running_ids
                conflicts.append(
                    ResourceConflict(
                        resource_key=key,
                        holders=(id_a, id_b),
                        observed=observed,
                        advisory=bool(claim_a.advisory or claim_b.advisory),
                        message=(
                            f"{id_a} ({claim_a.mode.value}) vs {id_b} "
                            f"({claim_b.mode.value}) on {key}"
                            + (" — both running" if observed else " — declared only")
                        ),
                    )
                )
    return conflicts
