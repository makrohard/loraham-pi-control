"""The kit's power read of a Reticulum row (testlab/hardware/rf-proof.sh, plan KIT-RT-FORMS v3): on the daemon backend
(rows 12 and 13) the read is the radio's txpower as its section renders it (`rt_dpget`, the product's own panel_section);
on spi (row 5) the Config page's value (`rt_pget`). The rendered form runs against this head's product on a temporary
runtime (box H's radios; {RT}/venv/lhpc is this python, {LH} this lhpc). No box, no radio."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

HW = Path(__file__).resolve().parents[2] / "hardware"
REPO = HW.parents[1]
sys.path.insert(0, str(REPO / "tests"))
import radios_fixture  # noqa: E402


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RADIO_", "PEER_", "BOX_", "DRY_", "LANE", "ROWS_ONLY"))}
    env.update(TMPDIR=str(tmp_path), **extra)
    return env


def _sourced(tmp_path, script):
    """`script` after sourcing rf-proof.sh in source-only mode (live), with $EVD a fresh evidence directory."""
    evd = tmp_path / "evd"
    evd.mkdir(exist_ok=True)
    prelude = f'RFPROOF_SOURCE_ONLY=1 . "{HW}/rf-proof.sh"; EVD="{evd}"; OUT=$EVD; MODE=live; : > "$EVD/state.env"\n'
    return subprocess.run(["bash", "-c", prelude + script], env=_env(tmp_path), stdin=subprocess.DEVNULL, cwd=evd,
                          capture_output=True, text=True, timeout=120, check=False)


def _box(tmp_path, own=None, spi=()):
    """A box runtime with box H's radios; `own`: {rid: dp_POWER} of reticulum@rid; `spi`: the radios on backend spi."""
    root = tmp_path / "rt"
    radios_fixture.write(root, radios_fixture.BOX_H)
    radios_fixture.reticulum_backend(root, list(spi))
    stacks = root / "config" / "stacks"
    stacks.mkdir(parents=True, exist_ok=True)
    for rid, power in (own or {}).items():
        f = stacks / f"reticulum@{rid}.toml"
        f.write_text((f.read_text() if f.exists() else "") + f'dp_POWER = "{power}"\n')
    bin_ = root / "venv" / "lhpc" / "bin"
    bin_.mkdir(parents=True)
    (bin_ / "python").symlink_to(sys.executable)
    (bin_ / "lhpc").write_text(f'#!/bin/sh\nexec "{sys.executable}" -m lhpc.adapters.cli.main "$@"\n')
    (bin_ / "lhpc").chmod(0o755)
    return root


def _read(tmp_path, root, row, rid, form="rt_dpget"):
    """The power-read form `form` as row `row` names it (RFORMS; none = not named), rendered by the script's own `form`, run
    on the box `root`; then the script's pnum of its output: (output, pnum)."""
    r = _sourced(tmp_path, f'f=$(rforms {row} | tr " " "\\n" | grep -xF {form}); form "$f" rid={rid}')
    assert r.returncode == 0 and r.stdout, r.stderr
    cmd = r.stdout.replace("{LH}", f"{root}/venv/lhpc/bin/lhpc").replace("{RT}", str(root))
    out = subprocess.run(["bash", "-c", cmd], env=_env(tmp_path, LHPC_RUNTIME_ROOT=str(root),
                                       PYTHONPATH=f"{REPO}:{REPO}/testlab"),
                         stdin=subprocess.DEVNULL, cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False)
    assert not out.stderr.strip() or out.stdout, out.stderr[-1500:]
    (tmp_path / "evd" / "PW.txt").write_text(out.stdout)
    p = _sourced(tmp_path, 'pnum PW.txt rt_dpget')
    return out.stdout, p.stdout


@pytest.mark.parametrize("row", [12, 13])
def test_a_daemon_rows_power_read_is_the_section_its_radio_renders(tmp_path, row):
    root = _box(tmp_path, own={"r4": "5"})
    out, num = _read(tmp_path, root, row, "r4")
    # red-before (93419bfe): `lhpc config reticulum@r4 txpower` -> "ERR txpower ... is on its daemon panel", no number
    assert (out, num) == ("5\n", "5")


def test_the_spi_rows_power_read_stays_the_config_pages_value(tmp_path):
    root = _box(tmp_path, spi=["r2"])
    assert _read(tmp_path, root, 5, "r2", "rt_pget") == ("14\n", "14")


def test_a_daemon_read_without_a_panel_gives_no_number(tmp_path):
    root = _box(tmp_path, spi=["r2"])
    out, num = _read(tmp_path, root, 12, "r2")                      # r2 on spi: panel_section refuses
    assert out == "NO txpower: reticulum@r2 uses backend spi: its radio settings are on its Config page\n" and num == ""


@pytest.mark.parametrize("row, forms", [("5 Z:Z868", "SP rt_pget"), ("12 H:HMS", "SP rt_dpget"),
                                        ("13 H:H868", "SP rt_dpget\nSP rt_dpget\nSP rt_pget")])
def test_each_reticulum_row_reads_its_power_with_its_backends_form(tmp_path, row, forms):
    rid, call = row.split()
    stubs = ('needs() { :; }; notrun_row() { :; }; rnsread() { :; }; undo_mark() { echo 0; }; getk() { echo r4; }; '
             'undo_push() { :; }; gstop() { :; }; spower() { echo "SP $2"; }; hpower() { exit 0; }; ')
    r = _sourced(tmp_path, stubs + f"row_{rid} {call}")
    assert r.returncode == 0 and r.stdout.strip() == forms, (r.stdout, r.stderr)
