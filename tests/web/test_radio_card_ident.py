"""F30 + F32 (approved mockups v11/v12): a stack's title on the Home radio card starts with its licence mark (📜 ham /
✅ free, from the manifest's `license`, with its meaning on hover) and shows, bold and unlabelled, the name / call LHPC
sets for that instance; the "Running on" and "Start a stack" boxes fold (open); the Start dropdown's options carry the
mark. Through the rendered Home page (and the manifest loader / README for the licence key)."""
import copy
import re
import tomllib
from pathlib import Path

import pytest
import radios_fixture
from htmlq import parse
from seams import entry_args, exe

from lhpc.core import instances, manifest
from lhpc.core.manifest import ManifestError
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

LOCAL = {"X-LHPC-Peer": "loopback", "Host": "127.0.0.1:8443"}
README = Path(__file__).resolve().parents[2] / "README.md"


def _running(tmp_path, stack, comp, rids, radios=radios_fixture.RADIOS, saved=None, ports=()):
    """`stack`'s component `comp` running as an instance on each radio of `rids`; `saved` {rid: values} saved first."""
    radios_fixture.write(tmp_path, radios)
    paths = Paths(runtime_root=tmp_path)
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    table = {}
    for n, rid in enumerate(rids):
        inst = instances.parse_target(plain, f"{stack}@{rid}", "start")[1]
        for key, values in (saved or {}).items():
            if key == rid:
                assert plain.save_config_bundle(stack, values=values, inst=inst).ok, values
        c = plain.stack(stack).component(comp)
        opt, val = instances._token(plain, c, inst, next(r.band for r in radios if r.id == rid))
        table[900 + n] = [exe(c.process, c.process.exec_name), *entry_args(c.process),
                          *[a for a in c.process.all_args if a != opt], opt, val]
    return ControllerService(system=FakeSystem(cmdlines_data=table, listeners=[
        Listener("ipv4", "127.0.0.1", p, p) for p in ports]).system, paths=paths)


def _title(web, svc, stack_key):
    doc = parse(web(service_factory=lambda: svc).get("/", headers=LOCAL).get_data(as_text=True))
    [t] = [doc.within(s) for s in doc.find("summary", class_="stitle") if s["data-stack"] == stack_key]
    return doc, t


def test_two_instances_each_show_their_own_name_and_the_free_mark(tmp_path, web):
    # red before F30/F32: the title carried only the stack name and its id pill
    two = (radios_fixture.R1, radios_fixture.R2)                                # two 433 radios
    svc = _running(tmp_path, "meshtastic", "meshtastic", ("r1", "r2"), radios=two,
                   saved={"r1": {"node_name": "Alpha Node", "node_short": "AL"},
                          "r2": {"node_name": "Bravo Node", "node_short": "BR"}})
    _doc, t1 = _title(web, svc, "meshtastic@r1")
    _doc, t2 = _title(web, svc, "meshtastic@r2")
    assert [b.text for b in t1.find("b", class_="sident")] == ["Alpha Node · AL"]
    assert [b.text for b in t2.find("b", class_="sident")] == ["Bravo Node · BR"]
    [mark] = t1.find("span", class_="lic")
    assert mark.text == "✅" and mark["title"].startswith("Free stack, uses encryption") and mark["aria-label"] == mark["title"]
    assert t1.text.startswith("✅ ")                                               # the title's first visible character


@pytest.mark.parametrize("mode, want", [("chat", "CNode"), ("chat+repeater", "CNode · RPT2"), ("repeater", "RPT2")])
def test_meshcore_shows_the_names_of_its_running_mode(tmp_path, web, mode, want):
    # the openHop repeater name: its saved value (openHop's own file wins once it exists: row_value, F30-ANALYSIS)
    saved = {"r2": {"file_node_name": "CNode", "file_repeater_name": "RPT2"}}
    if mode != "chat":
        saved["r2"]["file_mode"] = mode
    svc = _running(tmp_path, "meshcore", "meshcore-node", ("r2",),
                   radios=(radios_fixture.R1, radios_fixture.R2_868), saved=saved)
    _doc, t = _title(web, svc, "meshcore@r2")
    assert [b.text for b in t.find("b", class_="sident")] == [want]


def test_a_ham_stack_shows_its_call_and_the_ham_mark(tmp_path, web):
    svc = _running(tmp_path, "chat", "loraham-chat", ("r1",), saved={"r1": {"file_call": "DJ0CHE-10"}})
    _doc, t = _title(web, svc, "chat@r1")
    assert [b.text for b in t.find("b", class_="sident")] == ["DJ0CHE-10"]
    [mark] = t.find("span", class_="lic")
    assert mark.text == "📜" and mark["title"] == "Ham stack: amateur licence required"


def test_no_name_no_sident_and_a_failing_read_keeps_home_usable(tmp_path, web, monkeypatch):
    svc = _running(tmp_path, "kiss", "loraham-kiss-tnc", ("r1",))
    _doc, t = _title(web, svc, "kiss@r1")
    assert not t.find("b", class_="sident") and not t.find("span", class_="lic")  # kiss: no name, no mark
    svc2 = _running(tmp_path / "b", "meshtastic", "meshtastic", ("r1",),
                    saved={"r1": {"node_name": "Alpha Node", "node_short": "AL"}})
    monkeypatch.setattr(ControllerService, "_resolved_param_value", lambda *a, **k: 1 / 0)
    _doc, t = _title(web, svc2, "meshtastic@r1")                                   # the page still renders
    assert not t.find("b", class_="sident")


def test_running_on_and_start_a_stack_fold_open_and_hardware_stays_last(tmp_path, web):
    svc = _running(tmp_path, "chat", "loraham-chat", ("r1",))
    doc = parse(web(service_factory=lambda: svc).get("/", headers=LOCAL).get_data(as_text=True))
    run, start = doc.by_id("runon-r1"), doc.by_id("starton-r1")
    assert run.has_attr("open") and start.has_attr("open")
    assert "Running on r1" in doc.within(doc.within(run).find("summary")[0]).text
    assert "Start a stack on r1" in doc.within(doc.within(start).find("summary")[0]).text
    assert run.end < start.index                                                   # Start is outside Running on
    hw = doc.by_id("rd-hw-r1")
    assert hw.index > start.end                                                    # Hardware last, outside both
    opts = {o.text.strip() for o in doc.within(start).find("option")}
    assert any(o.startswith("📜 ") for o in opts) and any(o.startswith("✅ ") for o in opts)
    assert any(o.startswith("LoRaHAM KISS") for o in opts)                         # kiss: no mark


def test_the_signature_poll_reads_no_name(tmp_path, web, monkeypatch):
    svc = _running(tmp_path, "chat", "loraham-chat", ("r1",), saved={"r1": {"file_call": "DJ0CHE-10"}})
    seen = []
    real = ControllerService._resolved_param_value
    monkeypatch.setattr(ControllerService, "_resolved_param_value", lambda self, *a, **k: seen.append(a) or real(self, *a, **k))
    assert web(service_factory=lambda: svc).get("/api/dash-signature").status_code == 200
    assert not [a for a in seen if a and a[-1] in ("call", "node_name", "node_short", "repeater_name")]


def _raw():
    with open(manifest._DEFAULT_MANIFEST, "rb") as fh:
        return tomllib.load(fh)


def test_bad_license_or_card_ident_fails_the_load():
    raw = _raw()
    assert manifest.parse_manifest(copy.deepcopy(raw))
    bad = copy.deepcopy(raw)
    next(s for s in bad["stack"] if s["id"] == "chat")["license"] = "maybe"
    with pytest.raises(ManifestError, match="license"):
        manifest.parse_manifest(bad)
    bad = copy.deepcopy(raw)
    chat = next(s for s in bad["stack"] if s["id"] == "chat")
    p = next(p for c in chat["component"] for p in c.get("config_file", {}).get("param", []) + c.get("param", [])
             if p.get("card_ident"))
    p["card_ident"] = "first"
    with pytest.raises(ManifestError, match="card_ident"):
        manifest.parse_manifest(bad)


def test_the_license_key_matches_the_readme_and_the_identity_checks(tmp_path):
    # his word: the README's "Free to use" column is the checked list; the manifest key must agree with it, and with
    # the identity validators where a stack has an identity param (callsign = ham, node name = free)
    rows = dict(re.findall(r"^\|[^|]*\| `([a-z]+)` \|([^|]*)\|", README.read_text(), re.M))
    mark = {"📜🛜": "ham", "✅": "free", "": ""}
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert set(rows) == {s.id for s in svc.stacks()}, (set(rows), [s.id for s in svc.stacks()])   # both directions
    for s in svc.stacks():
        assert s.id in rows, s.id
        assert s.license == mark[rows[s.id].strip()], (s.id, s.license, rows[s.id])
        idf = svc._identity_field(s.id)
        if idf:
            assert s.license == {"licensed": "ham", "unlicensed": "free"}[idf["enforce"]], (s.id, idf)


def test_a_process_no_instance_claims_shows_its_mark_and_no_name(tmp_path, web):
    # the card also lists, fail-safe, a stack process on the radio's band that no instance claims (its band entry, no
    # radio id): it shows its licence mark, and NO name / call, because no instance's saved values are known to be its
    # own (a stack-level value could name another instance). G2-FINDSTRAING B2: the band entry carries no ident.
    radios_fixture.write(tmp_path, (radios_fixture.R1,))
    paths = Paths(runtime_root=tmp_path)
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    assert plain.save_config_bundle("meshtastic", values={"node_name": "Stack Node", "node_short": "SN"}).ok
    c = plain.stack("meshtastic").component("meshtastic")
    svc = ControllerService(system=FakeSystem(cmdlines_data={901: [exe(c.process, c.process.exec_name),
                                                                    *entry_args(c.process), *c.process.all_args]}).system,
                            paths=paths)
    doc = parse(web(service_factory=lambda: svc).get("/", headers=LOCAL).get_data(as_text=True))
    titles = [doc.within(s) for s in doc.find("summary", class_="stitle") if s["data-stack"] == "meshtastic"]
    assert len(titles) == 1, [s["data-stack"] for s in doc.find("summary", class_="stitle")]
    [mark] = titles[0].find("span", class_="lic")
    assert mark.text == "✅" and titles[0].find("b", class_="sident") == []
