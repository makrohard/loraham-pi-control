"""F30 + F32 (approved mockups v11/v12) + the Running on line (0.14.0): a stack's title on the Home radio card
starts with its licence mark (📜 ham / ✅ free, from the manifest's `license`, with its meaning on hover), its name and
its id; the card's "Running on rN" line names each stack running on that radio with the name / call LHPC sets for that
instance (the daemon only when it runs alone); the "Running on" and "Start a stack" boxes fold (open); the Start
dropdown's options carry the mark. Through the rendered Home page (and the manifest loader / README for the licence
key)."""
import copy
import re
import subprocess
import sys
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


def _line(doc, rid):
    """The text of radio `rid`'s "Running on" line (its box's summary heading)."""
    return " ".join(doc.within(doc.within(doc.by_id(f"runon-{rid}")).find("summary")[0]).find("h3")[0].text.split())


def _no_ident(doc):
    return doc.find("b", class_="sident") == []


def test_two_instances_each_show_their_own_name_and_the_free_mark(tmp_path, web):
    # each radio's line names its own instance's names, never the other radio's
    two = (radios_fixture.R1, radios_fixture.R2)                                # two 433 radios
    svc = _running(tmp_path, "meshtastic", "meshtastic", ("r1", "r2"), radios=two,
                   saved={"r1": {"node_name": "Alpha Node", "node_short": "AL"},
                          "r2": {"node_name": "Bravo Node", "node_short": "BR"}})
    doc, t1 = _title(web, svc, "meshtastic@r1")
    _doc, t2 = _title(web, svc, "meshtastic@r2")
    assert _line(doc, "r1") == "Running on r1 Meshtastic Alpha Node · AL"
    assert _line(doc, "r2") == "Running on r2 Meshtastic Bravo Node · BR"
    assert " ".join(t1.text.split()) == "✅ Meshtastic meshtastic@r1" and "Bravo" not in t2.text and _no_ident(doc)
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
    doc, t = _title(web, svc, "meshcore@r2")
    assert _line(doc, "r2") == f"Running on r2 MeshCore (OpenHop) {want}" and want not in t.text


def test_a_ham_stack_shows_its_call_and_the_ham_mark(tmp_path, web):
    svc = _running(tmp_path, "chat", "loraham-chat", ("r1",), saved={"r1": {"file_call": "DJ0CHE-10"}})
    doc, t = _title(web, svc, "chat@r1")
    assert _line(doc, "r1") == "Running on r1 LoRaHAM Chat DJ0CHE-10"
    assert " ".join(t.text.split()) == "📜 LoRaHAM Chat chat@r1" and _no_ident(doc)      # the call left the title
    [mark] = t.find("span", class_="lic")
    assert mark.text == "📜" and mark["title"] == "Ham stack: amateur licence required"


def test_no_name_no_sident_and_a_failing_read_keeps_home_usable(tmp_path, web, monkeypatch):
    svc = _running(tmp_path, "kiss", "loraham-kiss-tnc", ("r1",))
    doc, t = _title(web, svc, "kiss@r1")
    assert _line(doc, "r1") == "Running on r1 LoRaHAM KISS TNC"                    # kiss: no name, no mark
    assert not t.find("span", class_="lic") and _no_ident(doc)
    svc2 = _running(tmp_path / "b", "meshtastic", "meshtastic", ("r1",),
                    saved={"r1": {"node_name": "Alpha Node", "node_short": "AL"}})
    monkeypatch.setattr(ControllerService, "_resolved_param_value", lambda *a, **k: 1 / 0)
    doc, _t = _title(web, svc2, "meshtastic@r1")                                   # the page still renders
    assert _line(doc, "r1") == "Running on r1 Meshtastic"


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
    assert _line(doc, "r1") == "Running on r1"            # it names no radio: the line names it neither (D1)


def test_meshcom_names_its_call_in_the_running_on_line_not_in_its_title(tmp_path, web):
    # "Running on r1 MeshCom (QEMU) DJ0CHE-11", the title "📜 MeshCom (QEMU) meshcom@r1"
    svc = _running(tmp_path, "meshcom", "meshcom-qemu", ("r1",), saved={"r1": {"mc_callsign": "DJ0CHE-11"}})
    doc, t = _title(web, svc, "meshcom@r1")
    assert _line(doc, "r1") == "Running on r1 MeshCom (QEMU) DJ0CHE-11"
    assert " ".join(t.text.split()) == "📜 MeshCom (QEMU) meshcom@r1" and _no_ident(doc)


@pytest.fixture
def daemon_proc():
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    yield p
    p.kill()
    p.wait(timeout=5)


def _with_daemon(svc, pid):
    """`svc` plus a verified launch record of daemon@r1 (a live process lhpc owns), as Home shows it."""
    stk = svc.stack("daemon")
    assert svc._lifecycle().record_launch(stk, stk.main_component, pid, band="433", extra={"rid": "r1"})
    return svc


def test_the_daemon_is_named_only_when_it_runs_alone(tmp_path, web, daemon_proc):
    radios_fixture.write(tmp_path / "a", (radios_fixture.R1,))
    alone = _with_daemon(ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path / "a")),
                         daemon_proc.pid)
    doc = parse(web(service_factory=lambda: alone).get("/", headers=LOCAL).get_data(as_text=True))
    assert _line(doc, "r1") == "Running on r1 LoRaHAM daemon"
    kiss = _running(tmp_path / "b", "kiss", "loraham-kiss-tnc", ("r1",), radios=(radios_fixture.R1,))
    chat = _running(tmp_path / "c", "chat", "loraham-chat", ("r1",), radios=(radios_fixture.R1,))
    paths = Paths(runtime_root=tmp_path / "b")
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    assert plain.save_config_bundle("chat", values={"file_call": "DJ0CHE-10"},
                                    inst=instances.parse_target(plain, "chat@r1", "start")[1]).ok
    table = {**kiss._system.procfs.cmdlines(), **{pid + 50: v for pid, v in chat._system.procfs.cmdlines().items()}}
    svc = _with_daemon(ControllerService(system=FakeSystem(cmdlines_data=table).system, paths=paths), daemon_proc.pid)
    doc = parse(web(service_factory=lambda: svc).get("/", headers=LOCAL).get_data(as_text=True))
    order = [t["data-stack"] for t in doc.within(doc.by_id("runon-r1")).find("summary", class_="stitle")]
    names = {"kiss@r1": "LoRaHAM KISS TNC", "chat@r1": "LoRaHAM Chat DJ0CHE-10"}
    assert "daemon@r1" in order and set(names) <= set(order), order           # their blocks stay below
    assert _line(doc, "r1") == "Running on r1 " + ", ".join(names[k] for k in order if k in names)


def test_a_radio_with_nothing_running_reads_running_on_and_its_idle_note(tmp_path, web):
    radios_fixture.write(tmp_path, (radios_fixture.R1,))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    doc = parse(web(service_factory=lambda: svc).get("/", headers=LOCAL).get_data(as_text=True))
    assert _line(doc, "r1") == "Running on r1"
    assert "No stack running on this radio." in doc.within(doc.by_id("runon-r1")).text


def test_a_long_name_with_markup_characters_is_shown_escaped(tmp_path, web):
    name = 'Node <b>&"x</b> with a long name 1234567'[:30]
    svc = _running(tmp_path, "meshtastic", "meshtastic", ("r1",), saved={"r1": {"node_name": name, "node_short": "LN"}})
    html = web(service_factory=lambda: svc).get("/", headers=LOCAL).get_data(as_text=True)
    doc = parse(html)
    assert _line(doc, "r1") == f"Running on r1 Meshtastic {name} · LN"
    assert "<b>&" not in html.split('id="runon-r1"')[1].split("</summary>")[0]      # escaped, no markup injected
