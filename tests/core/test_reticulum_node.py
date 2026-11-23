"""The Reticulum node's config is node-wide (plan reti v21 §1d, B3 design note v2): one `[[LoRa <rid>]]` section per
interface — the owner's, enabled, and each intended member's (its saved `member_of`), rendered disabled from its OWN
settings and band — and one section of every other kind. A radio that is a member never starts as an owner. Box H:
r1 loraham 433, r2 loraham 868 (SPI), r3 CH341-RFM98 433 and r4 PineDio 868 (USB)."""
from __future__ import annotations

import dataclasses
import re

import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ActionResult, ControllerService

BOX_H = radios_fixture.BOX_H


def _save(tmp_path, rid, **keys):
    d = tmp_path / "config" / "stacks"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"reticulum@{rid}.toml").write_text("".join(f'{k} = "{v}"\n' for k, v in keys.items()))


def _svc(tmp_path, rows=BOX_H):
    radios_fixture.write(tmp_path, rows)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _render(tmp_path, svc, rid="r2"):
    writes = svc.write_config_files("rns", "868", inst=instances.copy_of(svc, "reticulum", rid)[0])
    assert all(w.status == "written" for w in writes if w.component == "rns"), writes
    return (tmp_path / "state" / "reticulum" / "config").read_text()


def _sections(text):
    return re.findall(r"^\s*\[\[([^\[\]]+)\]\]\s*$", text, re.M)


def _keys(text, rid):
    """{key: value} of interface `rid`'s block (read here, not with the code under test)."""
    block = re.search(rf"^\s*\[\[LoRa {rid}\]\]\s*$(.*?)(?=^\s*\[)", text, re.M | re.S).group(1)
    return dict(ln.strip().split(" = ", 1) for ln in block.splitlines() if " = " in ln and ln.strip()[:1] != "#")


def test_an_owner_alone_renders_its_own_named_interface_and_the_runner_names_it(tmp_path):
    svc = _svc(tmp_path)
    text = _render(tmp_path, svc)
    # red-before (466de981): one bare [[LoRa]] section and `--interface LoRa`
    assert _sections(text) == ["LoRa r2", "Client access", "Internet"]
    argv = svc.manual_start_command(svc.stack("reticulum").component("rns"), instances.copy_of(svc, "reticulum", "r2")[0])
    assert "--interface 'LoRa r2'" in argv


def test_members_render_disabled_from_their_own_settings_and_band(tmp_path):
    svc = _svc(tmp_path, (*BOX_H[:2], dataclasses.replace(BOX_H[2], enabled=False), BOX_H[3]))
    _save(tmp_path, "r4", member_of="r2", dp_FREQ="868.300")             # r4 (868, USB, daemon): a member of r2; B2: its
    #                                                                      radio values are its own daemon panel's
    _save(tmp_path, "r1", member_of="r2")                                   # r1 (433): a member on the other band
    _save(tmp_path, "r3", member_of="r2")                                   # r3: disabled radio -> absent
    text = _render(tmp_path, svc)
    # red-before (466de981): no member section at all
    assert _sections(text) == ["LoRa r2", "LoRa r1", "LoRa r4", "Client access", "Internet"]
    owner, r1, r4 = _keys(text, "r2"), _keys(text, "r1"), _keys(text, "r4")
    assert (owner["enabled"], r1["enabled"], r4["enabled"]) == ("yes", "no", "no")
    assert (r1["band"], r1["frequency"], r4["band"], r4["frequency"]) == ("433", "434500000", "868", "868300000")
    assert owner["frequency"] == "868500000" and r4["type"] == "LoRaDaemonInterface"
    assert text.count("# Attach point for external clients") == 1            # the next section's comment stays its own


# what rns-lora-interface v0.4.0 (f196175) requires of a daemon section: docs/daemon-interface.md, section.py:100-142
DAEMON_REQUIRED = ("type", "band", "frequency", "conf_socket", "data_socket")


def test_every_daemon_interface_the_node_renders_has_the_keys_the_interface_requires(tmp_path):
    # RETI-SOCKET: the owner and each member, each with ITS OWN radio's sockets; rf_log_path whenever rf_log is on
    svc = _svc(tmp_path, (*BOX_H[:2], dataclasses.replace(BOX_H[2], enabled=False), BOX_H[3]))
    _save(tmp_path, "r4", member_of="r2")
    text = _render(tmp_path, svc)
    for rid in ("r2", "r4"):
        k = _keys(text, rid)
        assert k["type"] == "LoRaDaemonInterface", (rid, k)
        # red-before (666d3661): conf_socket missing in both blocks
        assert [key for key in DAEMON_REQUIRED if not k.get(key)] == [], (rid, k)
        assert (k["conf_socket"], k["data_socket"]) == (f"/tmp/loraconf-{rid}.sock", f"/tmp/loraf-{rid}.sock"), rid
        assert k.get("rf_log") != "on" or k.get("rf_log_path") == f"{tmp_path}/logs/{rid}/rf-reticulum.log", (rid, k)


def test_a_member_of_another_node_or_an_spi_member_on_a_usb_radio_is_absent(tmp_path):
    svc = _svc(tmp_path)
    _save(tmp_path, "r3", member_of="r2")                                   # the one eligible member
    _save(tmp_path, "r1", member_of="r3")                                   # another owner's member
    _save(tmp_path, "r4", member_of="r2", backend="spi")                    # spi on a USB radio: not eligible (C2)
    _save(tmp_path, "r2", member_of="r2")                                   # the owner itself (C1 b)
    # red-before (466de981): no member section at all
    assert _sections(_render(tmp_path, svc)) == ["LoRa r2", "LoRa r3", "Client access", "Internet"]


@pytest.fixture
def box(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    calls = []
    monkeypatch.setattr(ControllerService, "run_action", lambda self, op, target, **kw: (
        calls.append((op, target, getattr(kw.get("inst"), "rid", None))), ActionResult(True, "ok", data={"changes": 1}))[1])

    def run(*argv):
        radios_fixture.write(tmp_path, BOX_H)
        return cli.main(list(argv)), capsys.readouterr(), calls
    return run


def test_a_member_radio_never_starts_as_an_owner_and_a_loop_starts_neither(tmp_path, box):
    _save(tmp_path, "r4", member_of="r2")
    # red-before (466de981): rc 0, reticulum@r4 started as a node of its own
    rc, out, calls = box("stack", "start", "reticulum@r4", "--yes")
    assert (rc, calls) == (1, []) and "reticulum@r4 is a member of the node on r2: start reticulum@r2, or remove r4" in out.err
    _save(tmp_path, "r2", member_of="r4")                                   # a loop (hand edits only)
    for rid in ("r2", "r4"):
        rc, out, calls = box("stack", "start", f"reticulum@{rid}", "--yes")
        assert (rc, calls) == (1, []) and "is a member of the node on" in out.err


def test_one_interface_block_is_swapped_removed_or_added_and_nothing_else_moves(tmp_path):
    from lhpc.core import reticulum_node
    svc = _svc(tmp_path)
    _save(tmp_path, "r4", member_of="r2")
    text = _render(tmp_path, svc)
    r4 = reticulum_node.section_text(text, "r4")
    gone = reticulum_node.replace_section(text, "r4", "")
    assert _sections(gone) == ["LoRa r2", "Client access", "Internet"]
    assert r4 in text and gone == text.replace(r4, "", 1)
    back = reticulum_node.replace_section(gone, "r4", r4)                # added right after the last LoRa block
    assert back == text
    swapped = reticulum_node.replace_section(text, "r4", r4.replace("enabled = no", "enabled = yes"))
    assert [a for a, b in zip(text.splitlines(), swapped.splitlines(), strict=True) if a != b] == ["    enabled = no"]


def test_each_interface_writes_its_own_radios_rf_log(tmp_path):
    svc = _svc(tmp_path)
    _save(tmp_path, "r1", member_of="r2")
    text = _render(tmp_path, svc)
    # red-before (f429a19c): every section named the node's one file logs/rf-reticulum.log
    assert _keys(text, "r2")["rf_log_path"] == f"{tmp_path}/logs/r2/rf-reticulum.log"
    assert _keys(text, "r1")["rf_log_path"] == f"{tmp_path}/logs/r1/rf-reticulum.log"
    assert (tmp_path / "logs" / "r2").is_dir() and (tmp_path / "logs" / "r1").is_dir()   # the interface opens, never makes it
