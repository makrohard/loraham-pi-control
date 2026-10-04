"""The systemd unit templates LHPC installs are frozen: their rendered bytes match the checked-in
hashes in `tests/data/unit-templates.sha256`.

The seven user units (`updater_units.ALL_UNITS`) are verified byte for byte on the box: a unit
whose installed bytes differ from the render is not canonical, so boot restore refuses and the box
comes back from a power cycle with nothing running — and no update path repairs it (the in-process
repair renders the PRE-update templates; the systemd-helper route cannot write units at all,
`ProtectHome=read-only`). A changed COMMENT is enough: an audit fix once reverted a directive but
kept a reworded comment beside it, and `lhpc-web.service`'s bytes still changed. The three firewall
units are installed by the operator's sudo apply script, which nothing re-runs on an update. So no
release may change any of them silently: keep the bytes (redirect new state into `{root}` with an
environment variable, as `KIVY_HOME` does), or change them together with the staged unit
migration ([backlog](../../docs/backlog.md#two-stage-unit-template-migration)) and update the
hashes in the same change.

The user units are hashed as rendered for the shipped `deploy/` copies (`%h` root), so the file is
also `sha256sum deploy/<unit>`; `tests/host/test_updater_units.py` proves those copies are the
renders.
"""
from __future__ import annotations

import hashlib

import repo_paths
from lhpc.core import firewall as fw
from lhpc.core import updater_units as U

FROZEN = repo_paths.TESTS / "data" / "unit-templates.sha256"
MESSAGE = ("unit templates are frozen until the staged unit migration exists (see "
           "docs/architecture.md); update the hash only together with that migration")


def _renders() -> dict[str, str]:
    root = "%h/loraham-pi-control"
    units = {kind: U.render(kind, root, f"{root}/src/loraham-pi-control", f"{root}/venv/lhpc")
             for kind in U.ALL_UNITS}
    units.update({fw.LOADER_UNIT: fw.render_loader_unit(),
                  fw.CHECKER_UNIT: fw.render_checker_unit(),
                  fw.CHECKER_TIMER: fw.render_checker_timer()})
    return units


def _frozen() -> dict[str, str]:
    pinned = {}
    for line in FROZEN.read_text().splitlines():
        digest, name = line.split("  ", 1)
        pinned[name] = digest
    return pinned


def test_every_unit_template_renders_its_frozen_bytes():
    frozen = _frozen()
    rendered = {name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for name, text in _renders().items()}
    assert set(rendered) == set(frozen), f"{MESSAGE} — a unit was added or removed: " \
        f"{sorted(set(rendered) ^ set(frozen))}"
    drifted = sorted(name for name in rendered if rendered[name] != frozen[name])
    assert not drifted, f"{MESSAGE} — changed: {drifted}"
