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

The staged migration's data is guarded here too: `unit-templates-released.sha256` is the
append-only history of every digest a release shipped (it only grows, and every current digest is
in it), and `unit-templates-compatible.sha256` pins the other release's units
(`updater_units._COMPATIBLE_EDITS`) both ways, none equal to its current unit and every one released.
The 0.12.0/0.12.1 units stay in it in every later release (docs/maintenance.md, *Changing a unit*): a
box that went back to 0.12.1 and then straight to a later release still restores at boot.
"""
from __future__ import annotations

import hashlib

import repo_paths
from lhpc.core import firewall as fw
from lhpc.core import updater_units as U

FROZEN = repo_paths.TESTS / "data" / "unit-templates.sha256"
RELEASED = repo_paths.TESTS / "data" / "unit-templates-released.sha256"
COMPATIBLE = repo_paths.TESTS / "data" / "unit-templates-compatible.sha256"
MESSAGE = ("a unit's bytes change only through the staged unit migration (docs/maintenance.md): first "
           "a release that accepts the new bytes (updater_units._COMPATIBLE_EDITS), then the one that "
           "ships them, its digests added to the released history")
# The 0.12.0/0.12.1 units: every later release keeps them `compatible` (docs/maintenance.md).
PRE_CONTRACT = {"lhpc-web.service": "67ee9e95fa5f14b3f1e0c7c2ab8a76671f3b5521963438f5980c08110eab267e",
                "lhpc-nginx.service": "101d941f78e321c87340ed5dba74c93a910f642a662dc5de709241a8367fcf61",
                "lhpc-boot-restore.service":
                    "ecb2dde6c6f48476acc0d6f216fb30d3d9ef8087a530607b1e876448382c3652"}
# The released history as 0.12.0 left it: the history only grows, so every one of these stays.
SEED = """67ee9e95fa5f14b3f1e0c7c2ab8a76671f3b5521963438f5980c08110eab267e  lhpc-web.service  0.5.0-0.12.0
c10ab4d516da4b38824c9af955ba2cd012d0de4d6d776fcd297bade33108813e  lhpc-selfupdate.service  0.5.0-0.12.0
b0adcfda127b1c2dd37b1b85b3a020ba862db105f02ec22479177c0de8770b2d  lhpc-selfupdate.path  0.5.0-0.12.0
101d941f78e321c87340ed5dba74c93a910f642a662dc5de709241a8367fcf61  lhpc-nginx.service  0.5.0-0.12.0
047a1056eb4994ea7f23219a69792c955fdc9c62d377a13e419a0df392394fc6  lhpc-nginx-restart.service  0.5.0-0.12.0
23deeea7fec3725a8beb72c6da1105ad2c0cae48de8be6fbcfb088785aa218b2  lhpc-nginx-restart.path  0.5.0-0.12.0
ecb2dde6c6f48476acc0d6f216fb30d3d9ef8087a530607b1e876448382c3652  lhpc-boot-restore.service  0.5.0-0.12.0
c319e238350b56f5dc5ce23ef14e7d6e2f599b9d4c2e87edf3e2c1a4633ee769  lhpc-firewall.service  0.12.0
9b0928ac6b1debbf792a140dd5886201d663f80cc6ba8114843ab4bf3dee9d0b  lhpc-firewall-check.service  0.12.0
c4d477cb51b813e18a025612cc469e340320afb9ba5c7536701c5abb565ee818  lhpc-firewall-check.timer  0.12.0
"""


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


def _pairs(path) -> dict[str, str]:
    return {name: digest for digest, name in
            (line.split("  ")[:2] for line in path.read_text().splitlines())}


def _history(text: str) -> list[tuple[str, str]]:
    return [tuple(line.split("  ")[:2]) for line in text.splitlines() if line]


def _compatible_renders() -> dict[str, str]:
    root = "%h/loraham-pi-control"
    return {kind: hashlib.sha256(U.render_compatible(kind, root, f"{root}/src/loraham-pi-control",
                                                     f"{root}/venv/lhpc").encode("utf-8")).hexdigest()
            for kind in U.ALL_UNITS if U.render_compatible(kind, root, root, root) is not None}


def _guard(current: dict, history: list, pinned: dict, rendered: dict) -> list[str]:
    """The migration data's rules, as named errors."""
    errors = []
    held = {}
    for digest, name in history:
        held.setdefault(name, set()).add(digest)
    errors += [f"history lost a released line: {name}" for digest, name in _history(SEED)
               if digest not in held.get(name, set())]
    errors += [f"current digest not in the released history: {name}"
               for name, digest in current.items() if digest not in held.get(name, set())]
    errors += [f"firewall unit's history is not its current digest alone: {name}"
               for name in current if name not in U.ALL_UNITS and held.get(name) != {current[name]}]
    if pinned != rendered:
        errors.append(f"compatible units differ from their pinned digests: "
                      f"{sorted(set(pinned.items()) ^ set(rendered.items()))}")
    errors += [f"compatible unit equals its current unit: {name}"
               for name, digest in rendered.items() if current.get(name) == digest]
    errors += [f"compatible digest not in the released history: {name}"
               for name, digest in pinned.items() if digest not in held.get(name, set())]
    errors += [f"0.12.0/0.12.1 unit no longer compatible: {name}"
               for name, digest in PRE_CONTRACT.items() if pinned.get(name) != digest]
    return errors


def test_the_released_history_and_the_compatible_units_agree_with_the_templates():
    current = {name: hashlib.sha256(text.encode("utf-8")).hexdigest()
               for name, text in _renders().items()}
    assert _guard(current, _history(RELEASED.read_text()), _pairs(COMPATIBLE), _compatible_renders()) == []


def test_a_byte_change_without_its_history_line_fails():
    current = _pairs(FROZEN)
    history = _history(RELEASED.read_text())
    current[U.WEB_UNIT] = "0" * 64                              # a changed template's new digest
    assert _guard(current, history, _pairs(COMPATIBLE), _pairs(COMPATIBLE)) == [
        f"current digest not in the released history: {U.WEB_UNIT}"]
    assert _guard(current, history + [("0" * 64, U.WEB_UNIT)], _pairs(COMPATIBLE), _pairs(COMPATIBLE)) == []


def test_a_released_line_removed_from_the_history_fails():
    history = [(d, n) for d, n in _history(RELEASED.read_text()) if n != U.HELPER_UNIT]
    errors = _guard(_pairs(FROZEN), history, _pairs(COMPATIBLE), _pairs(COMPATIBLE))
    assert f"history lost a released line: {U.HELPER_UNIT}" in errors


def test_a_compatible_unit_without_its_pinned_digest_or_equal_to_current_fails():
    current, history = _pairs(FROZEN), _history(RELEASED.read_text())
    rendered = dict(_pairs(COMPATIBLE), **{U.HELPER_UNIT: "1" * 64})  # an edit with no pinned line
    assert any(e.startswith("compatible units differ") for e in _guard(current, history,
                                                                       _pairs(COMPATIBLE), rendered))
    same = dict(_pairs(COMPATIBLE), **{U.WEB_UNIT: current[U.WEB_UNIT]})
    assert f"compatible unit equals its current unit: {U.WEB_UNIT}" in _guard(current, history, same,
                                                                               same)


def test_a_compatible_unit_never_released_or_the_pre_contract_units_dropped_fails():
    current, history = _pairs(FROZEN), _history(RELEASED.read_text())
    unreleased = dict(_pairs(COMPATIBLE), **{U.NGINX_UNIT: "2" * 64})
    errors = _guard(current, history, unreleased, unreleased)
    assert f"compatible digest not in the released history: {U.NGINX_UNIT}" in errors
    assert f"0.12.0/0.12.1 unit no longer compatible: {U.NGINX_UNIT}" in errors
    dropped = {k: v for k, v in _pairs(COMPATIBLE).items() if k != U.BOOT_RESTORE_UNIT}
    assert f"0.12.0/0.12.1 unit no longer compatible: {U.BOOT_RESTORE_UNIT}" in _guard(
        current, history, dropped, dropped)


def test_every_compatible_edit_matches_its_template_once_and_has_no_brace():
    assert set(U._COMPATIBLE_TEMPLATES) == set(U._COMPATIBLE_EDITS)
    for kind, edits in U._COMPATIBLE_EDITS.items():
        for old, new in edits:
            assert U._TEMPLATES[kind].count(old) == 1, (kind, old)
            assert "{" not in new and "}" not in new, (kind, new)


def test_an_edit_that_does_not_match_is_left_out_never_raised(monkeypatch):
    monkeypatch.setitem(U._COMPATIBLE_EDITS, U.WEB_UNIT, (("no such text\n", "x\n"),))
    assert U._compatible_template(U.WEB_UNIT) is None
    monkeypatch.setitem(U._COMPATIBLE_EDITS, U.WEB_UNIT, ((" /tmp\n", " {root}\n"),))
    assert U._compatible_template(U.WEB_UNIT) is None
