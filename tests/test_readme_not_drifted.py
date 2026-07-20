"""Anti-drift guard for the README install guide.

The README's manual dependency list and hardware table are hand-written, but their CONTENT is
owned by generators:
  * apt packages  -> the same manifest `require` blocks that `lhpc deps --script` renders
                     (`ControllerService._declared_dep_scopes()` / `deps_script()`);
  * hardware rows -> `lhpc.core.config.HW_SETUPS`.

This test pins the docs to those generators so the manual list can never silently rot back into
the state it was in before the rewrite (GUI deps unconditional, no `--no-install-recommends`,
missing core packages such as cmake/curl/build-essential). It runs against BOTH README.md and the
German README.de.md — package tokens and hardware ids/presets are language-independent, so a stale
translation is caught by the identical contract.

The docs delimit the machine-checked regions with HTML-comment markers (invisible when rendered):
    <!-- test:deps-manual:start --> ... a bash block ... <!-- test:deps-manual:end -->
    <!-- test:hw-table:start -->    ... a markdown table ... <!-- test:hw-table:end -->
"""

import pathlib
import re
import tempfile

import pytest

from lhpc.core import deps
from lhpc.core.config import HW_SETUPS
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_REPO = pathlib.Path(__file__).resolve().parents[1]
_READMES = [_REPO / "README.md", _REPO / "README.de.md"]

_DEPS_START, _DEPS_END = "<!-- test:deps-manual:start -->", "<!-- test:deps-manual:end -->"
_HW_START, _HW_END = "<!-- test:hw-table:start -->", "<!-- test:hw-table:end -->"

# Hardware cell: "433 → loraham, 868 → loraham" (accepts → or ->).
_BAND_PRESET_RE = re.compile(r"(\d+)\s*(?:→|->)\s*([A-Za-z0-9-]+)")


def _svc():
    return ControllerService(system=FakeSystem().system,
                             paths=Paths(runtime_root=tempfile.mkdtemp()))


def _apt_tokens(command_line: str) -> set:
    """Package tokens from one `sudo apt[-get] install ...` line — the generator's own rule:
    match the install command, then drop flag tokens (leading `-`). Trailing `# comments` are
    stripped first so README annotations never leak into the token set."""
    line = command_line.split("#", 1)[0]
    m = deps._APT_INSTALL_RE.match(line.strip())
    if not m:
        return set()
    return {t for t in m.group(1).split() if not t.startswith("-")}


def _between(text: str, start: str, end: str, path: pathlib.Path) -> str:
    assert start in text and end in text, (
        f"{path.name}: missing markers {start} … {end} — the anti-drift test cannot locate the "
        f"region. Wrap it in those HTML comments.")
    return text[text.index(start) + len(start): text.index(end)]


# --- generator sides (computed once, language-independent) --------------------------------------

def _generator_sets():
    core_cmds, gui_cmds = _svc()._declared_dep_scopes()
    core = set().union(*(_apt_tokens(c) for c in core_cmds)) if core_cmds else set()
    gui = set().union(*(_apt_tokens(c) for c in gui_cmds)) if gui_cmds else set()
    return core, gui


GEN_CORE, GEN_GUI = _generator_sets()


# --- README sides -------------------------------------------------------------------------------

def _readme_apt_lines(readme_text: str, path: pathlib.Path) -> list:
    block = _between(readme_text, _DEPS_START, _DEPS_END, path)
    return [ln for ln in block.splitlines() if deps._APT_INSTALL_RE.match(ln.strip())]


@pytest.mark.parametrize("path", _READMES, ids=lambda p: p.name)
def test_manual_core_deps_match_generator(path):
    lines = _readme_apt_lines(path.read_text(), path)
    core_lines = [ln for ln in lines if "--with-gui" not in ln]
    readme_core = set().union(*(_apt_tokens(ln) for ln in core_lines)) if core_lines else set()
    missing = GEN_CORE - readme_core
    extra = readme_core - GEN_CORE
    assert not missing and not extra, (
        f"{path.name}: manual dependency list drifted from the generator. "
        f"MISSING (add): {sorted(missing)}; UNEXPECTED (remove): {sorted(extra)}. "
        f"Regenerate the source of truth with `lhpc deps --script` and mirror it here.")


@pytest.mark.parametrize("path", _READMES, ids=lambda p: p.name)
def test_gui_deps_are_isolated_under_with_gui(path):
    lines = _readme_apt_lines(path.read_text(), path)
    gui_lines = [ln for ln in lines if "--with-gui" in ln]
    readme_gui = set().union(*(_apt_tokens(ln) for ln in gui_lines)) if gui_lines else set()
    assert readme_gui == GEN_GUI, (
        f"{path.name}: the `--with-gui` line must carry exactly {sorted(GEN_GUI)}, "
        f"got {sorted(readme_gui)}. Regenerate with `lhpc deps --script`.")
    # No GUI-only package may appear in the core (non --with-gui) apt lines.
    core_lines = [ln for ln in lines if "--with-gui" not in ln]
    leaked = GEN_GUI & (set().union(*(_apt_tokens(ln) for ln in core_lines)) if core_lines else set())
    assert not leaked, (
        f"{path.name}: GUI-only package(s) {sorted(leaked)} leaked into the headless core list — "
        f"move them to the `--with-gui` line.")


@pytest.mark.parametrize("path", _READMES, ids=lambda p: p.name)
def test_every_apt_line_uses_no_install_recommends(path):
    offenders = [ln.strip() for ln in _readme_apt_lines(path.read_text(), path)
                 if "--no-install-recommends" not in ln]
    assert not offenders, (
        f"{path.name}: every manual apt line must use --no-install-recommends "
        f"(Recommends drag X11 in via git→openssh-client→xauth). Offenders: {offenders}")


@pytest.mark.parametrize("path", _READMES, ids=lambda p: p.name)
def test_hardware_table_matches_HW_SETUPS(path):
    block = _between(path.read_text(), _HW_START, _HW_END, path)
    rows = {}
    for ln in block.splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.strip().startswith("|") else []
        if len(cells) < 3 or not cells[0] or set(cells[0]) <= set("-: "):
            continue  # header separator / non-row
        setup_id = cells[0].strip("`")
        if setup_id not in HW_SETUPS:
            continue  # header row ("id"/"setup" label etc.)
        rows[setup_id] = {b: p for b, p in _BAND_PRESET_RE.findall(cells[-1])}
    expected = {sid: presets for sid, (_label, presets) in HW_SETUPS.items() if sid != "unset"}
    assert rows == expected, (
        f"{path.name}: hardware table drifted from lhpc/core/config.py HW_SETUPS. "
        f"Table={rows}; expected={expected}. Regenerate the rows from HW_SETUPS.")
