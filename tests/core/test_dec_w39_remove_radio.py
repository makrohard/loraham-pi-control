"""DEC-W39 (the decision on W247, "a function to delete a hardware row"): `lhpc hardware remove
<rid>` removes one radio's row under the hardware list's lock (W217). Without the confirm phrase `remove-<rid>` it is
a dry run that changes nothing. It is refused while the radio's daemon or a stack on it runs, naming them, from ONE
answer (radio_start.radio_activity). The id stays reserved (`retired` in config/radios.toml): no later add takes it,
no row may use it, and every later save keeps it. Prior-boot ownership records of the radio are not touched; the
boot planner refuses them ("radio rN no longer exists") with their evidence named."""
from __future__ import annotations

import radios_fixture

from lhpc.core import boot_restore, radio_daemon, radio_start, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"),
        radios.Radio("r3", "spare", "meshstick-1262", "868", enabled=False, usb_port="1-1.9"))


def _svc(tmp_path, monkeypatch, procs=(), running=()):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    radios_fixture.write(tmp_path, ROWS)
    monkeypatch.setattr(radio_start, "_daemon_procs", lambda svc: list(procs))
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, sid: sid in running)
    monkeypatch.setattr(ControllerService, "running_band", lambda self, sid, default="": "868")
    return ControllerService(system=FakeSystem(files={"/proc/uptime": "100.0 200.0\n"}).system,
                             paths=Paths(runtime_root=tmp_path))


def _file(tmp_path) -> bytes:
    return (tmp_path / "config" / "radios.toml").read_bytes()


def _all(res) -> str:
    return " ".join([res.summary, *res.details, *res.next_commands])


def test_without_the_phrase_it_is_a_dry_run_that_changes_nothing(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    before = _file(tmp_path)
    for phrase in ("", "remove-r2", "remove r3", "REMOVE-R3"):
        res = radio_start.remove_radio(svc, "r3", phrase)
        assert not res.ok and res.data.get("reason") == "dry-run", res.summary
        assert "lhpc hardware remove r3 --confirm-phrase remove-r3" in res.next_commands
        assert _file(tmp_path) == before


def test_the_phrase_removes_the_row_and_reserves_the_id(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    res = radio_start.remove_radio(svc, "r3", "remove-r3")
    assert res.ok, res.summary
    state = radios.load(svc._paths)
    assert [r.id for r in state.radios] == ["r1", "r2"] and state.retired == ("r3",)
    assert radios.next_id(state.radios, state.retired) == "r4"


def test_a_save_after_a_remove_keeps_the_reserved_id(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    assert radio_start.remove_radio(svc, "r3", "remove-r3").ok
    state = radios.load(svc._paths)
    assert radios.save(svc._paths, (*state.radios, radios.Radio("r4", "more", "meshstick-1262", "868", enabled=False,
                                                                      usb_port="1-1.5"))) == []
    assert radios.load(svc._paths).retired == ("r3",)


def test_a_row_with_a_reserved_id_is_refused(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    assert radio_start.remove_radio(svc, "r3", "remove-r3").ok
    state = radios.load(svc._paths)
    before = _file(tmp_path)
    reasons = radios.save(svc._paths, (*state.radios, radios.Radio("r3", "again", "meshstick-1262", "868", enabled=False,
                                                                       usb_port="1-1.5")))
    assert any("r3" in r and "reserved" in r for r in reasons), reasons
    assert _file(tmp_path) == before
    text = (before.decode() + '\n[[radio]]\nid = "r3"\nname = "again"\nmodel = "meshstick-1262"\nband = "868"\n'
            'enabled = false\nusb_port = "1-1.5"\n')
    assert radios.load_text(text).kind == radios.INVALID


def test_a_bad_retired_value_makes_the_list_invalid():
    for bad in ('retired = "r3"', 'retired = ["r0"]', 'retired = ["r3", "r3"]', "retired = [3]"):
        assert radios.load_text(bad + "\n").kind == radios.INVALID, bad
    assert radios.load_text('retired = ["r3"]\n').retired == ("r3",)


def test_it_is_refused_while_the_radios_daemon_runs(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, procs=[radio_daemon.DaemonProc(4242, "id", rid="r2", band="868")])
    before = _file(tmp_path)
    res = radio_start.remove_radio(svc, "r2", "remove-r2")
    assert not res.ok and res.data.get("reason") == "running"
    assert "its daemon" in res.summary and _file(tmp_path) == before


def test_it_is_refused_while_a_stack_on_it_runs_naming_it(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, running={"meshcore"})
    before = _file(tmp_path)
    res = radio_start.remove_radio(svc, "r2", "remove-r2")
    assert not res.ok and res.data.get("reason") == "running" and "meshcore" in res.summary
    assert _file(tmp_path) == before


def test_an_unknown_process_blocks_it_as_the_daemon(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, procs=[radio_daemon.DaemonProc(77, "unknown", reason="cannot tell")])
    res = radio_start.remove_radio(svc, "r3", "remove-r3")
    assert not res.ok and res.data.get("reason") == "running"


def test_the_refusal_and_the_dry_run_name_the_same_activity(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, running={"meshcore"})
    row = next(r for r in radios.load(svc._paths).radios if r.id == "r2")
    act = radio_start.radio_activity(svc, row)
    assert act and all(a in radio_start.remove_radio(svc, "r2", "").summary for a in act)


def test_no_such_radio_and_a_removed_one_are_refused(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    assert not radio_start.remove_radio(svc, "r9", "remove-r9").ok
    assert radio_start.remove_radio(svc, "r3", "remove-r3").ok
    res = radio_start.remove_radio(svc, "r3", "remove-r3")
    assert not res.ok and "already removed" in res.summary


def test_the_confirm_names_what_stays_and_the_restore_items(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(radio_start, "_restore_records",
                        lambda svc, rid: [{"stack": "kiss", "launch_id": "L1", "rid": rid}])
    text = _all(radio_start.remove_radio(svc, "r3", ""))
    assert "kiss will not be restored at the next boot: radio r3 no longer exists" in text
    assert "r3 stays reserved" in text


def test_the_dry_run_names_only_kept_folders_that_exist(tmp_path, monkeypatch):
    # every "kept" folder line comes from a path on disk: none before the radio wrote a log, logs/r3 after
    svc = _svc(tmp_path, monkeypatch)
    kept = [d for d in radio_start.remove_radio(svc, "r3", "").details if "folder" in d]
    assert kept == ["  [stays] no folder of r3 is on disk"], kept
    (tmp_path / "logs" / "r3").mkdir(parents=True)
    kept = [d for d in radio_start.remove_radio(svc, "r3", "").details if "folder" in d]
    assert kept == [f"  [stays] kept folder {tmp_path}/logs/r3 (its RF logs already written)"], kept


def test_after_a_remove_the_boot_planner_skips_its_item_naming_the_evidence(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    assert radio_start.remove_radio(svc, "r3", "remove-r3").ok
    item = boot_restore.new_item("i1", "stack", target="kiss", band="868", evidence_ids=["L1"], rid="r3")
    plan = boot_restore.Plan(items=[item])
    boot_restore.radio_filter(plan, radios.load(svc._paths).radios)
    assert plan.items == []
    assert plan.skipped == [{"stack": "kiss", "evidence_ids": ["L1"], "reason": "radio r3 no longer exists"}]


def test_the_cli_dry_run_and_the_remove(tmp_path, monkeypatch, capsys):
    from lhpc.adapters.cli.main import main
    _svc(tmp_path, monkeypatch)
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    before = _file(tmp_path)
    assert main(["hardware", "remove", "r3"]) != 0
    assert _file(tmp_path) == before and "remove-r3" in capsys.readouterr().out
    assert main(["hardware", "remove", "r3", "--confirm-phrase", "remove-r3"]) == 0, capsys.readouterr().out
    assert radios.load(Paths(runtime_root=tmp_path)).retired == ("r3",)
