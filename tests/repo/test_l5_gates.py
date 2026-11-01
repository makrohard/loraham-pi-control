"""Stage L5 landing gates (B4-WIRING-TABLES W3-8, W4-4/W7-5, W7-10, W2-7): after L5 no radio is named by its
band where a radio id belongs. Each gate reads the shipped manifest or the start path's source as text; a hit
names its line, so a band-keyed socket, claim or setting reader cannot come back unnoticed."""
import ast
import re
from pathlib import Path

from lhpc.core import manifest

MANIFEST = Path(manifest._DEFAULT_MANIFEST)
CORE = Path(manifest.__file__).parent
LINES = MANIFEST.read_text().splitlines()


def _hits(pattern):
    rx = re.compile(pattern)
    return [f"{n}: {ln.strip()}" for n, ln in enumerate(LINES, 1) if rx.search(ln)]


def test_w3_8_no_band_socket_literal_in_the_manifest():
    # The daemon's sockets are its radio's (`lora{,f,conf}-<rid>.sock`); a 0.13.x band name never returns.
    assert _hits(r"lora(conf|f)?(433|868)\.sock") == []


def test_w3_8_no_socket_param_takes_a_band_default():
    # A socket param's value is its instance's radio template (`{rid}`), never a per-band default table.
    hits, in_socket = [], False
    for n, ln in enumerate(LINES, 1):
        s = ln.strip()
        if s.startswith("[[") or s.startswith("["):
            in_socket = False
        m = re.match(r'(name|key)\s*=\s*"([^"]*)"', s)
        if m:
            in_socket = "socket" in m.group(2)
        if in_socket and s.startswith("band_defaults"):
            hits.append(f"{n}: {s}")
    assert hits == []


def test_w4_4_no_claim_key_names_a_band():
    # Claims, locks and owners are per radio (A:206): no `loraham.radio.433/868` or daemon-socket band key.
    assert _hits(r"loraham\.(radio|daemon-socket)\.(433|868)\b") == []


# W7-10: every `{band}` left in the manifest, with the table row that keeps it.
KEPT_BAND = {
    "W7-3": {f'key = "{{band}}_{k}"' for k in ("freq", "sf", "bw", "cr", "crc", "preamble", "sync", "power", "ldro")},
    "W7-6": {'default = "{band}"'},
}


def test_w7_10_every_remaining_band_template_is_a_kept_row():
    kept = set().union(*KEPT_BAND.values())
    left = [h for h in _hits(r"\{band\}") if not h.split(": ", 1)[1].startswith("#")]
    assert [h for h in left if h.split(": ", 1)[1] not in kept] == []
    assert {h.split(": ", 1)[1] for h in left} == kept           # and each kept row is still there


BAND_READERS = {"hw_preset_for_band", "high_power_for_band", "ocp_for_band"}


def _names(tree):
    return {n.attr if isinstance(n, ast.Attribute) else n.id
            for n in ast.walk(tree) if isinstance(n, (ast.Attribute, ast.Name))}


def test_w2_7_the_daemon_start_reads_no_band_setting():
    # A radio's daemon params come only from radio_daemon.daemon_params via radio_start.start_plan (W1, IF §3):
    # the start plan and the start loop never read a band-keyed setting, which two radios of one band would share.
    rs = ast.parse((CORE / "radio_start.py").read_text())
    assert _names(rs) & BAND_READERS == set()
    ops = ast.parse((CORE / "service_lifecycle_ops.py").read_text())
    ensure = next(n for n in ast.walk(ops) if isinstance(n, ast.FunctionDef) and n.name == "_ensure_daemon")
    assert _names(ensure) & BAND_READERS == set()
