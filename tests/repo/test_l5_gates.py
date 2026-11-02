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


# Every 0.13.x band socket name: raw `lora<band>.sock`, framed `lora<band>f.sock`, CONF `loraconf<band>.sock`
# (G2-0.14.0-1 item 4: the framed name puts its `f` after the band; the old pattern looked for `loraf<band>`).
BAND_SOCKET = re.compile(r"lora(conf|f)?(433|868)f?\.sock")
LEGACY_FORMS = [f"{d}/{n}" for d in ("/tmp", "/run/loraham") for b in ("433", "868")
                for n in (f"lora{b}.sock", f"lora{b}f.sock", f"loraconf{b}.sock")]


def test_the_band_socket_pattern_names_every_legacy_form_and_no_radio_socket():
    assert [f for f in LEGACY_FORMS if not BAND_SOCKET.search(f)] == []                      # 12 canaries
    rid = [f"{d}/{n}-r1.sock" for d in ("/tmp", "/run/loraham") for n in ("lora", "loraf", "loraconf")]
    assert [f for f in rid if BAND_SOCKET.search(f)] == []


def test_w3_8_no_band_socket_literal_in_the_manifest():
    # The daemon's sockets are its radio's (`lora{,f,conf}-<rid>.sock`); a 0.13.x band name never returns.
    assert _hits(BAND_SOCKET.pattern) == []


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


def test_w7_7_the_daemons_hw_choices_are_the_model_tables_presets():
    # T-M3: the start plan passes variant(radio, model).preset as --hw; the manifest's enum must hold exactly those
    # (LEG-D41: a meshstick-1262 row was refused "hw: 'meshstick-1262' not in (…)" before any launch).
    from lhpc.core import radio_models
    daemon = next(c for st in manifest.load_manifest() for c in st.components if c.id == "loraham-daemon")
    hw = next(p for p in daemon.run_params if p.name == "hw")
    presets = {v.preset for m in radio_models.load().values() for v in m.variants}
    assert set(hw.choices) == presets


def test_every_enum_the_start_plan_fills_holds_its_values():
    # The class (LEG-D41): each daemon param radio_daemon.daemon_params fills from the row or the model table has a
    # manifest constraint that admits every value it can produce.
    from lhpc.core import radio_models, radios
    daemon = next(c for st in manifest.load_manifest() for c in st.components if c.id == "loraham-daemon")
    by = {p.name: set(p.choices) for p in daemon.run_params if p.choices}
    models = radio_models.load().values()
    assert {b for m in models for b in m.bands} <= by["radio"]
    assert set(radios.TXMODES) <= by["txmode"] and set(radios.CADMONS) <= by["cadmon"]
    assert {"on", "off"} <= by["rf_log"]


def test_every_preset_keyed_table_holds_each_model_preset():
    # The class (LEG-D41): config's preset-keyed tables cover every preset of the model table, and the family
    # of each is that model's chip (chip_family reads HW_PRESET_FAMILY by the row's preset).
    from lhpc.core import config, radio_models
    pairs = {(v.preset, m.chip) for m in radio_models.load().values() for v in m.variants}
    assert {p: config.HW_PRESET_FAMILY.get(p) for p, _ in pairs} == dict(pairs)
    assert {p for p, _ in pairs} <= set(config.HW_PRESET_LABELS)


def test_the_shared_band_sentence_is_one_wording_in_code_docs_page_and_changelog():
    # PKT-D20 item 5: the refusal, daemon.md, the Hardware page and the CHANGELOG say the same sentence.
    from lhpc.core import radio_daemon
    repo = CORE.parent.parent
    words = " ".join(radio_daemon.SHARED_BAND_TEXT.split()).lower()
    for f in ("docs/stacks/daemon.md", "lhpc/adapters/web/templates/hardware.html", "CHANGELOG.md"):
        t = (repo / f).read_text()
        for a, b in (("<code>", ""), ("</code>", ""), ("`", ""), ("&lt;", "<"), ("&gt;", ">")):
            t = t.replace(a, b)
        assert words in " ".join(t.split()).lower(), f
