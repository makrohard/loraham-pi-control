"""A Settings save, stage by stage: NORMALIZE, the store plan and DECIDE from explicit inputs (no
controller), COMMIT on a temporary runtime root, and two saves that meet at the config lock."""
from __future__ import annotations

import contextlib
import threading
from types import SimpleNamespace

import pytest

from lhpc.core import config as cfgmod
from lhpc.core import config_save
from lhpc.core.config_save import Change
from lhpc.core.model import (Component, ComponentKind, FileConfig, FileParam, RunParam,
                             SourceSpec, Stack)
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_params import _BANDLESS_STACK_PARAMS, _STACK_SWITCHES, _commit_config
from lhpc.core.services import ControllerService

FREQ = FileParam("tx_freq", key="freq", kind="float", default="433.775")
MODE = RunParam("mode", kind="enum", choices=("a", "b"), default="a")
GPS = RunParam("use_gps", kind="enum", choices=("on", "off"), default="on")
PW = RunParam("password_file", kind="str")
APP = Component("app", "App", ComponentKind.SERVICE, run_params=(MODE, GPS, PW),
                config_file=FileConfig(path="app.conf", params=(FREQ,)))
DEP = Component("dep", "Dep", ComponentKind.SERVICE, run_params=(RunParam("level", kind="int"),))
REFS = {("file", "tx_freq"): (APP, FREQ), ("run", "mode"): (APP, MODE),
        ("run", "use_gps"): (APP, GPS), ("run", "password_file"): (APP, PW),
        ("run", "level"): (DEP, DEP.run_params[0])}


def _resolve(kind, key):
    if key == "twice":
        return None, None, f"{kind} parameter 'twice' is declared by multiple components"
    c, p = REFS.get((kind, key), (None, None))
    return (c, p, None) if c else (None, None, f"unknown {kind} parameter {key!r}")


def _normalize(values):
    return config_save.normalize_values(
        values, _resolve, own_ids={"app"}, optional_ids={"opt"},
        stack_of={"dep": "depstack"}.get,
        refused=lambda c, p: "managed elsewhere" if p is PW else "")


def test_normalize_identifies_each_value_and_keeps_blanks_unvalidated():
    changes, auto, errors = _normalize({"file_tx_freq": "434.5", "mode": "", "autostart_opt": "1",
                                        "use_gps": "off"})
    assert errors == []
    assert changes == [Change("f", APP, FREQ, "434.5"), Change("r", APP, MODE, ""),
                       Change("r", APP, GPS, "off")]
    assert auto == {"autostart_opt": "on"}


def test_normalize_refuses_in_submission_order_and_collects_nothing_refused():
    changes, auto, errors = _normalize({
        "autostart_other": "on", "file_nope": "1", "twice": "1", "level": "3",
        "password_file": "", "mode": "c", "file_tx_freq": "fast"})
    assert errors == [
        "unknown config field: 'autostart_other'",
        "unknown config field: 'file_nope'",
        "run parameter 'twice' is declared by multiple components",
        "'level' belongs to dependency component 'dep' — save it on its own stack ('depstack')",
        "managed elsewhere",
        "mode: 'c' not in ('a', 'b')",
        "tx_freq: not a number ('fast')",
    ]
    assert changes == [] and auto == {}


def _stacks(*comps):
    return [Stack("s", "S", components=comps)]


def test_normalize_remotes_expands_a_shared_checkout_and_names_the_others():
    a = Component("a", "A", ComponentKind.LIBRARY, source=SourceSpec("src/x", remote="https://h/x"))
    b = Component("b", "B", ComponentKind.LIBRARY, source=SourceSpec("src/x", remote="https://h/x"))
    patch, notes, errors = config_save.normalize_remotes(
        {"a": "https://example.invalid/x.git"}, target="s", allowed={"a"},
        stacks=lambda: _stacks(a, b), declarers=lambda pth: ["a", "b"])
    assert errors == []
    assert patch == {"a": "https://example.invalid/x.git", "b": "https://example.invalid/x.git"}
    assert notes == ["shared checkout src/x: the same remote was applied to b"]


def test_normalize_remotes_refuses_foreign_invalid_and_diverging_remotes():
    a = Component("a", "A", ComponentKind.LIBRARY, source=SourceSpec("src/x", remote="https://h/x"))
    b = Component("b", "B", ComponentKind.LIBRARY, source=SourceSpec("src/x", remote="https://h/x"))
    patch, notes, errors = config_save.normalize_remotes(
        {"../a": "", "zz": "", "a": "https://one.invalid/x", "b": "https://two.invalid/x",
         "c": "file:///x"},
        target="s", allowed={"a", "b", "c"}, stacks=lambda: _stacks(a, b),
        declarers=lambda pth: ["a", "b"])
    assert errors == [
        "component id: path separator not allowed in '../a'",
        "remote override not allowed for 'zz' — not a source component of 's'",
        "remote: only https:// or git@host:path remotes are allowed",
        "conflicting remotes submitted for shared source 'src/x' (a, b) — one checkout has ONE "
        "remote",
    ]
    assert notes == []
    assert patch == {"a": "https://one.invalid/x", "b": "https://two.invalid/x"}


def _plan(changes, auto=None, banded=True, gps_default="on"):
    stack = Stack("s", "S", components=(Component(
        "app", "App", ComponentKind.SERVICE,
        run_params=(RunParam("use_gps", default=gps_default), RunParam("rf_log", default="on"))),))
    return SimpleNamespace(**dict(zip(
        ("stack_set", "stack_remove", "bandless_set", "bandless_remove", "switched"),
        config_save.plan_store(changes, auto or {}, key_of=lambda ch: ch.param.name,
                               default_of=lambda ch: ch.param.default, switches=_STACK_SWITCHES,
                               stacks=[stack], sid="s", banded=banded), strict=True)))


def test_plan_stores_overrides_only():
    plan = _plan([Change("f", APP, FREQ, "434.5"), Change("r", APP, MODE, "a")],
                 {"autostart_opt": "on", "autostart_two": ""})
    assert (plan.stack_set, plan.stack_remove) == ({"tx_freq": "434.5"}, {"mode"})
    assert (plan.bandless_set, plan.bandless_remove) == ({"autostart_opt": "on"}, {"autostart_two"})
    assert plan.switched == {}


@pytest.mark.parametrize("submitted, stored, cleared, wanted", [
    ("off", {"use_gps": "off"}, set(), "off"),       # a deviation from the default is stored
    ("", {"use_gps": "off"}, set(), "off"),          # blank is NOT "on" (from the value, not bucket)
    (" ON ", {}, {"use_gps"}, "on"),                 # the default itself is cleared
])
def test_plan_routes_the_gps_switch_to_the_bandless_key(submitted, stored, cleared, wanted):
    scoped = RunParam("__r__app__use_gps", default="on")       # a component-scoped key shape
    plan = _plan([Change("r", APP, scoped, submitted)])
    assert (plan.bandless_set, plan.bandless_remove) == (stored, cleared)
    assert plan.stack_set == {} and plan.stack_remove == set()
    assert plan.switched == {"use_gps": (wanted, "on")}


def test_plan_a_removed_switch_key_wins_and_rf_log_reads_anything_but_off_as_on():
    plan = _plan([Change("r", APP, RunParam("use_gps", default="off"), "on"),     # set ...
                  Change("r", APP, RunParam("__r__app__use_gps", default=""), ""),  # ... removed
                  Change("f", APP, RunParam("file_rf_log", default=""), "maybe")],
                 gps_default="off")
    assert plan.switched == {"use_gps": ("off", "off"), "rf_log": ("on", "on")}
    assert plan.bandless_set == {} and plan.bandless_remove == {"use_gps", "rf_log"}


def test_plan_folds_everything_into_one_file_when_unbanded():
    plan = _plan([Change("r", APP, RunParam("rf_log", default="on"), "OFF"),
                  Change("f", APP, FREQ, "434.5")], {"autostart_opt": "on"}, banded=False)
    assert plan.stack_set == {"tx_freq": "434.5", "autostart_opt": "on", "rf_log": "off"}
    assert plan.stack_remove == set()
    assert plan.bandless_set == {} and plan.bandless_remove == set()


def test_the_switch_table_is_the_bandless_set_the_readers_use():
    assert _BANDLESS_STACK_PARAMS == ("use_gps", "rf_log")


def test_overlay_keeps_other_keys_and_drops_the_cleared_ones():
    current = {"dp_433_POWER": "20", "tx_freq": "433.9", "dest": "ALL"}
    assert config_save.overlay(current, {"tx_freq": "434.5", "new": "x"}, {"dest", "absent"}) == {
        "dp_433_POWER": "20", "tx_freq": "434.5", "new": "x"}
    assert current == {"dp_433_POWER": "20", "tx_freq": "433.9", "dest": "ALL"}   # input untouched


@pytest.mark.parametrize("held", [False, True])
def test_commit_writes_through_the_journal_and_takes_the_lock_only_when_not_held(
        tmp_path, monkeypatch, held):
    paths = Paths(runtime_root=tmp_path)
    target = tmp_path / "config" / "stacks" / "s.toml"
    journal = tmp_path / "state" / "config-txn.json"
    real, entered, journalled = cfgmod.config_lock, [], []

    def render(_p):                               # rendered inside the transaction, before any write
        journalled.append(journal.exists())
        return "a = 1\n"

    @contextlib.contextmanager
    def recording(p, *a, **k):
        entered.append(p)
        with real(p, *a, **k):
            yield
    if held:
        with real(paths):                         # the caller's EXCLUSIVE hold
            monkeypatch.setattr(cfgmod, "config_lock", recording)
            _commit_config(paths, [("stack", target, render, 0o644)], held=True)
    else:
        monkeypatch.setattr(cfgmod, "config_lock", recording)
        _commit_config(paths, [("stack", target, render, 0o644)], held=False)
    assert entered == ([] if held else [paths])
    assert journalled == [True]                   # the journal was written before the target
    assert target.read_text() == "a = 1\n"
    assert not journal.exists()


def test_two_saves_meeting_at_the_lock_both_land(tmp_path, monkeypatch):
    """Both saves are normalized and planned BEFORE either takes the config lock; the second to
    get it re-reads the file the first wrote and merges, so neither update is lost."""
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.bootstrap(apply=True).ok
    real, barrier, first = cfgmod.config_lock, threading.Barrier(2, timeout=20), threading.local()

    @contextlib.contextmanager
    def meet_then_lock(paths, *a, **k):
        if not getattr(first, "done", False):
            first.done = True
            barrier.wait()                       # both saves are past NORMALIZE here
        with real(paths, *a, **k):
            yield
    monkeypatch.setattr(cfgmod, "config_lock", meet_then_lock)
    results: dict = {}

    def save(key, value):
        try:
            results[key] = svc.save_config_bundle("chat", values={key: value})
        except BaseException as exc:            # reported by the assertion below
            results[key] = exc
    threads = [threading.Thread(target=save, args=kv)
               for kv in (("file_tx_freq", "434.500"), ("file_dest", "CQ"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert {k: getattr(r, "ok", r) for k, r in results.items()} == {
        "file_tx_freq": True, "file_dest": True}
    assert cfgmod.load_stack_config(svc._paths, "chat", "") == {"file_tx_freq": "434.500",
                                                                "file_dest": "CQ"}
