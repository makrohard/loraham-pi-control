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
import re
from collections import defaultdict

from .model import Component, ResourceClaim, ResourceConflict, ResourceMode

RADIO_KEY_PREFIX = "loraham.radio."
SOCKET_KEY_PREFIX = "loraham.daemon-socket."
# A radio key names its band's radio as a template (the shipped manifest) or, in a 0.13.x-shaped manifest, the
# band itself: both are that band's radio, so neither is ever dropped as "not a radio claim".
_BAND_RADIO = re.compile(r"(loraham\.(?:radio|daemon-socket))\.(?:\{radio:(433|868)\}|(433|868))")


def radio_claim(band: str, prefix: str = RADIO_KEY_PREFIX) -> str:
    """The claim key of `band`'s radio (stage L5, W4-4): `loraham.radio.{radio:<band>}`. One key per radio
    as the hardware list resolves the band (radio_start.key_for): its one enabled radio, else the band, so two
    radios of one band share the key and conflict (fail-closed, B4-D39)."""
    return f"{prefix}{{radio:{band}}}"


def radio_band(key: str) -> str:
    """The band of a radio or daemon-socket claim key; "" for any other key."""
    m = _BAND_RADIO.fullmatch(str(key))
    return (m.group(2) or m.group(3)) if m else ""


def normal_key(key: str) -> str:
    """One form per radio key (the template), so a band literal and its template are the same claim."""
    m = _BAND_RADIO.fullmatch(str(key))
    return f"{m.group(1)}.{{radio:{m.group(2) or m.group(3)}}}" if m else key


def claim_name(key: str, key_of) -> str:
    """The key as it leaves the conflict engine (a lock name, a message): `key_of(band)` names the radio."""
    m = _BAND_RADIO.fullmatch(str(key))
    return f"{m.group(1)}.{key_of(m.group(2) or m.group(3))}" if m else key


def limit_radio_claims(comp: Component, bands: set) -> Component:
    """`comp` with its radio claims restricted to `bands`: only the radio claims whose band is in `bands`
    are kept, every other claim is untouched. An EMPTY `bands` strips every radio claim — a component whose
    band is unknown must never claim both radios. Whether a component needs restricting at all is the
    caller's decision (the lifecycle knows the daemon)."""
    keep = tuple(r for r in comp.resources
                 if not (r.key.startswith(RADIO_KEY_PREFIX) and radio_band(r.key) not in bands))
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
    components: list[Component], running_ids: set[str], key_of=None
) -> list[ResourceConflict]:
    """Return declared/observed conflicts across all given components; `key_of(band)` names a radio key's
    radio in the result (claim_name), as the hardware list resolves it."""
    by_key: dict[str, list[tuple[str, ResourceClaim]]] = defaultdict(list)
    for comp in components:
        for claim in comp.resources:
            by_key[normal_key(claim.key)].append((comp.id, claim))

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
                name = claim_name(key, key_of) if key_of else key
                conflicts.append(
                    ResourceConflict(
                        resource_key=name,
                        holders=(id_a, id_b),
                        observed=observed,
                        advisory=bool(claim_a.advisory or claim_b.advisory),
                        message=(
                            f"{id_a} ({claim_a.mode.value}) vs {id_b} "
                            f"({claim_b.mode.value}) on {name}"
                            + (" — both running" if observed else " — declared only")
                        ),
                    )
                )
    return conflicts
