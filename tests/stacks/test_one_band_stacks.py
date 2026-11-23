"""Reticulum runs on 868 only and Voice on 433 only (each keeps one fixed `band`, no `bands`): the band list, the
radios an instance may be on, the files a start writes, MeshCore's frequency floor, and the band how-to for the next
coder. Driven through the service and the manifest the product ships."""
from __future__ import annotations

import re

import radios_fixture

from lhpc.core import instances
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from repo_paths import REPO


def _svc(tmp_path):
    radios_fixture.write(tmp_path)                       # r1 on 433, r2 on 868 (a LoRaHAM board each)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_t1_reticulum_runs_on_868_and_has_an_instance_on_the_868_radio_only(tmp_path):
    svc = _svc(tmp_path)
    assert instances.stack_bands(svc.stack("reticulum")) == ("868",)
    assert svc.stack_bands("reticulum") == ()                               # one band: no band chooser
    assert instances.radios_of(svc.stack("reticulum"), svc._paths) == ("r2",)
    assert instances.parse_target(svc, "reticulum@r1", "start")[3]          # r1 is 433: refused by name
    # nor attached to the node as one of its other radios: r1's saved member_of renders no interface (and so no join)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "stacks" / "reticulum@r1.toml").write_text('member_of = "r2"\n')
    svc._invalidate_config()
    svc.write_config_files("rns", "868", inst=instances.copy_of(svc, "reticulum", "r2")[0])
    sections = re.findall(r"^\s*\[\[(LoRa [^\]]*)\]\]", (tmp_path / "state" / "reticulum" / "config").read_text(), re.M)
    assert sections == ["LoRa r2"], sections


def test_t2_voice_runs_on_433_and_has_an_instance_on_the_433_radio_only(tmp_path):
    svc = _svc(tmp_path)
    assert instances.stack_bands(svc.stack("voice")) == ("433",)
    assert svc.stack_bands("voice") == ()                                   # one band: no band chooser
    assert instances.radios_of(svc.stack("voice"), svc._paths) == ("r1",)
    assert instances.parse_target(svc, "voice@r2", "start")[3]              # r2 is 868: refused by name


def _lines(path):
    return [ln.strip() for ln in open(path).read().splitlines()]


def test_t3_the_files_a_start_writes_carry_the_one_band(tmp_path):
    # GUARD (green before and after F34): voice@r1's loraham_voice.conf holds the 433 keys with the 433 values and
    # active_band=0, never a band-less `_freq` or an `868_` key; reticulum@r2's node config names band 868.
    svc = _svc(tmp_path)
    rows = svc.write_config_files("voice", "433", inst=Instance("r1", band="433"))
    conf = next(w.path for w in rows if w.component == "loraham-voice" and w.status == "written")
    voice = _lines(conf)
    assert {"433_freq=434.700", "433_sf=7", "433_bw=125.0", "active_band=0"} <= set(voice), voice
    assert not [ln for ln in voice if re.match(r"(_|868_)\w*=", ln)], voice
    rows = svc.write_config_files("reticulum", "868", inst=Instance("r2", band="868"))
    node = next(w.path for w in rows if w.component == "rns" and w.status == "written")
    lora = open(node).read().split("[[LoRa r2]]", 1)[1].split("[[", 1)[0]
    assert "band = 868" in [ln.strip() for ln in lora.splitlines()], lora


def test_t5_meshcore_takes_no_frequency_below_the_868_band(tmp_path):
    svc = _svc(tmp_path)
    r2 = instances.copy_of(svc, "meshcore", "r2")[0]
    low = svc.save_config_bundle("meshcore", values={"file_frequency": "433175000"}, inst=r2)
    assert not low.ok and low.details == ["frequency: below minimum 863000000"], (low.summary, low.details)
    assert svc.save_config_bundle("meshcore", values={"file_frequency": "869618000"}, inst=r2).ok


def test_t7_the_band_how_to_is_linked_and_names_only_paths_that_exist():
    howto = (REPO / "docs" / "adding-a-band.md").read_text()
    assert "adding-a-band.md" in (REPO / "docs" / "adding-a-stack.md").read_text()
    assert "adding-a-band.md" in (REPO / "docs" / "README.md").read_text()
    named = set(re.findall(r"\b(lhpc/[\w./-]+\.(?:py|toml|conf|json))\b", howto))
    assert named, "the how-to names the files to edit"
    assert sorted(p for p in named if not (REPO / p).is_file()) == []
