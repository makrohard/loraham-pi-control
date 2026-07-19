"""Known-working compositions: operator-confirmed records (dedupe, keep newest 3), the
last-start candidate marker lifecycle, offer/confirm validation, and the 'Known working'
selector resolution from compositions."""

import json
import os
import time

from lhpc.core import known_working, source_registry
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _paths(tmp_path) -> Paths:
    return Paths(runtime_root=tmp_path)


def _entries(commit="a" * 40, comp="c1"):
    return {comp: {"commit": commit, "selector": "pinned", "remote": "r",
                   "source_rel": "src/x"}}


# --- store: record / dedupe / retention -----------------------------------------------------

def test_record_dedupe_and_keep_three(tmp_path):
    paths = _paths(tmp_path)
    for i, c in enumerate("abcd"):
        ok, _ = known_working.record(paths, "s", _entries(commit=c * 40),
                                     {"confirmed_at": float(i)})
        assert ok
    comps = known_working.load(paths, "s")
    assert len(comps) == known_working.KEEP == 3
    # newest first; the oldest ('a') was evicted
    got = [c["entries"]["c1"]["commit"][0] for c in comps]
    assert got == ["d", "c", "b"]
    # re-recording an existing composition DEDUPES (moves to front, no growth)
    ok, _ = known_working.record(paths, "s", _entries(commit="c" * 40), {"confirmed_at": 9.0})
    assert ok
    comps = known_working.load(paths, "s")
    assert [c["entries"]["c1"]["commit"][0] for c in comps] == ["c", "d", "b"]


def test_newest_commit_for_uses_one_coherent_composition(tmp_path):
    paths = _paths(tmp_path)
    known_working.record(paths, "s",
                         {"c1": {"commit": "1" * 40, "selector": "pinned", "remote": "",
                                 "source_rel": "src/x"},
                          "c2": {"commit": "2" * 40, "selector": "pinned", "remote": "",
                                 "source_rel": "src/y"}}, {"confirmed_at": 1.0})
    known_working.record(paths, "s",
                         {"c1": {"commit": "3" * 40, "selector": "dev", "remote": "",
                                 "source_rel": "src/x"},
                          "c2": {"commit": "4" * 40, "selector": "dev", "remote": "",
                                 "source_rel": "src/y"}}, {"confirmed_at": 2.0})
    assert known_working.newest_commit_for(paths, "s", "c1") == "3" * 40   # newest composition
    assert known_working.newest_commit_for(paths, "s", "c2") == "4" * 40   # SAME composition
    assert known_working.newest_commit_for(paths, "s", "nope") == ""


def test_store_malformed_or_symlinked_is_empty(tmp_path):
    paths = _paths(tmp_path)
    sp = known_working.store_path(paths, "s")
    sp.parent.mkdir(parents=True)
    sp.write_text("not json")
    assert known_working.load(paths, "s") == []
    sp.unlink()
    sp.write_text(json.dumps({"version": 99, "compositions": []}))
    assert known_working.load(paths, "s") == []
    sp.unlink()
    (sp.parent / "real.json").write_text(json.dumps({"version": 1, "compositions": []}))
    os.symlink("real.json", sp)
    assert known_working.load(paths, "s") == []                # symlink leaf refused


def test_candidate_roundtrip_and_clear(tmp_path):
    paths = _paths(tmp_path)
    assert known_working.read_candidate(paths, "s") is None
    assert known_working.write_candidate(paths, "s", _entries(), "433")
    cand = known_working.read_candidate(paths, "s")
    assert cand and cand["band"] == "433" and cand["hash"] == known_working.composition_hash(_entries())
    # a candidate claiming another stack is refused
    assert known_working.read_candidate(paths, "other") is None
    known_working.clear_candidate(paths, "s")
    assert known_working.read_candidate(paths, "s") is None


# --- service: offer / confirm ----------------------------------------------------------------

def _svc(tmp_path, cmdlines=None, commands=None):
    return ControllerService(system=FakeSystem(cmdlines_data=cmdlines or {},
                                               commands=commands or {}).system,
                             paths=Paths(runtime_root=tmp_path))


_CHAT_REMOTE = "https://github.com/makrohard/LoRaHAM_Daemon.git"


def _bind_chat_identity(svc, tmp_path, commit="a" * 40, remote=None):
    """Answer the identity git queries by REALPATH — the handle-bound verifier runs them
    against the captured leaf's fd-pinned /proc path."""
    import os as _os
    from lhpc.core.probes.backends import CommandResult
    remote = _CHAT_REMOTE if remote is None else remote
    real_run = svc._system.runner.run
    dest_real = _os.path.realpath(str(tmp_path / "src" / "LoRaHAM_Daemon"))
    def run(argv, timeout, *a, **k):
        argv = list(argv)
        if (len(argv) >= 4 and argv[:2] == ["git", "-C"]
                and _os.path.realpath(argv[2]) == dest_real):
            if argv[3:] == ["config", "--get", "remote.origin.url"]:
                return CommandResult(0, remote + "\n", "")
            if argv[3:] == ["rev-parse", "HEAD"]:
                return CommandResult(0, commit + "\n", "")
        return real_run(argv, timeout, *a, **k)
    svc._system.runner.run = run
    return svc


def _seed_running_chat(tmp_path, commit="a" * 40):
    """chat stack: candidate + matching registry record + a present source dir; cmdlines make
    loraham-chat RUNNING; `_chat_identity_cmds` makes the identity verifier pass."""
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True, exist_ok=True)
    entries = {"loraham-chat": {"commit": commit, "selector": "dev", "remote": "",
                                "source_rel": "src/LoRaHAM_Daemon"}}
    assert known_working.write_candidate(paths, "chat", entries, "433")
    assert source_registry.write_record(paths, source_registry.RegistryRecord(
        "src/LoRaHAM_Daemon", "", "dev", commit, time.time(), "", "",
        ("loraham-chat", "loraham-igate")))
    return paths, entries


def test_offer_visible_when_running_and_unrecorded(tmp_path):
    _seed_running_chat(tmp_path)
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    offer = svc.known_working_offer("chat")
    assert offer and offer["components"] == ["loraham-chat"]


def test_offer_hidden_when_stopped_recorded_or_changed(tmp_path):
    paths, entries = _seed_running_chat(tmp_path)
    # stopped -> no offer
    assert _svc(tmp_path).known_working_offer("chat") is None
    # recorded -> no offer
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    known_working.record(paths, "chat", entries, {"confirmed_at": 1.0})
    assert svc.known_working_offer("chat") is None
    # sources changed since the start (registry commit differs) -> no offer
    known_working.clear_candidate(paths, "chat")
    _seed_running_chat(tmp_path)                                  # fresh candidate (commit a…)
    assert source_registry.write_record(paths, source_registry.RegistryRecord(
        "src/LoRaHAM_Daemon", "", "dev", "b" * 40, time.time(), "", "",
        ("loraham-chat", "loraham-igate")))
    svc2 = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    assert svc2.known_working_offer("chat") is None


def test_confirm_records_and_second_confirm_is_noop(tmp_path):
    paths, entries = _seed_running_chat(tmp_path)
    svc = _bind_chat_identity(_svc(tmp_path, cmdlines={555: ["loraham_chat"]}), tmp_path)
    res = svc.confirm_known_working("chat")
    assert res.ok and "Recorded" in res.summary
    assert known_working.newest_commit_for(paths, "chat", "loraham-chat") == "a" * 40
    res2 = svc.confirm_known_working("chat")
    assert res2.ok and "already recorded" in res2.summary


def test_confirm_refuses_stopped_or_missing_candidate(tmp_path):
    svc = _svc(tmp_path)
    res = svc.confirm_known_working("chat")
    # chat is manual-only: without a registry-proven source the probe-basis path refuses
    # (startable stacks keep the "No healthy start" refusal — covered below).
    assert not res.ok and "registry-proven" in res.summary
    _seed_running_chat(tmp_path)                                  # candidate present, but stopped
    svc2 = _svc(tmp_path)
    res2 = svc2.confirm_known_working("chat")
    assert not res2.ok and "not running" in res2.summary
    assert known_working.load(Paths(runtime_root=tmp_path), "chat") == []   # nothing recorded


# --- probe-basis offer/confirm for manual-only stacks (no lhpc start possible) ---------------

def _seed_registry_only_chat(tmp_path, commit="a" * 40):
    """Registry record + source dir but NO candidate — the manual-start situation: the
    operator ran chat themselves, LHPC never recorded a start."""
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True, exist_ok=True)
    assert source_registry.write_record(paths, source_registry.RegistryRecord(
        "src/LoRaHAM_Daemon", "", "dev", commit, time.time(), "", "",
        ("loraham-chat", "loraham-igate")))
    return paths


def test_manual_stack_offer_and_confirm_without_candidate(tmp_path):
    paths = _seed_registry_only_chat(tmp_path)
    svc = _bind_chat_identity(_svc(tmp_path, cmdlines={555: ["loraham_chat"]}), tmp_path)
    offer = svc.known_working_offer("chat")
    assert offer and offer["components"] == ["loraham-chat"] and offer["started_at"] == 0
    res = svc.confirm_known_working("chat")
    assert res.ok and "Recorded" in res.summary
    comps = known_working.load(paths, "chat")
    assert comps and "probe-verified" in comps[0]["validated"]["evidence"]
    assert known_working.newest_commit_for(paths, "chat", "loraham-chat") == "a" * 40


def test_manual_stack_confirm_refuses_stopped_or_unproven(tmp_path):
    # registry-proven but NOT running -> dashboard-card refusal, nothing recorded
    paths = _seed_registry_only_chat(tmp_path)
    res = _svc(tmp_path).confirm_known_working("chat")
    assert not res.ok and "dashboard card" in res.summary
    assert known_working.load(paths, "chat") == []
    # running but NO registry record -> registry-proven refusal, nothing recorded
    r2 = tmp_path / "r2"
    (r2 / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    svc2 = _svc(r2, cmdlines={555: ["loraham_chat"]})
    res2 = svc2.confirm_known_working("chat")
    assert not res2.ok and "registry-proven" in res2.summary
    assert known_working.load(Paths(runtime_root=r2), "chat") == []


def test_manual_stack_offer_hidden_when_stopped_or_recorded(tmp_path):
    _seed_registry_only_chat(tmp_path)
    assert _svc(tmp_path).known_working_offer("chat") is None     # stopped -> no offer
    svc = _bind_chat_identity(_svc(tmp_path, cmdlines={555: ["loraham_chat"]}), tmp_path)
    assert svc.confirm_known_working("chat").ok
    assert svc.known_working_offer("chat") is None                # recorded -> no offer


def test_startable_stack_still_requires_candidate(tmp_path):
    # kiss has lhpc-startable components: no candidate -> unchanged strict refusal
    res = _svc(tmp_path, cmdlines={7: ["loraham-kiss-tnc"]}).confirm_known_working("kiss")
    assert not res.ok and "No healthy start" in res.summary


def test_verified_stack_stop_clears_candidate(tmp_path):
    paths, _ = _seed_running_chat(tmp_path)
    svc = _svc(tmp_path)                                          # nothing actually running
    res = svc.stop("chat", apply=True)
    assert res.ok
    assert known_working.read_candidate(paths, "chat") is None    # candidate retired


# --- 'Known working' (pinned) selector resolution from compositions --------------------------

def test_pinned_selector_resolves_composition_commit(tmp_path):
    import subprocess
    from lhpc.core.install import Installer
    from lhpc.core.config import Config
    from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
    from lhpc.core.probes import RealSystem

    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True, env=env).stdout.strip()

    local = tmp_path / "rt" / "local" / "app"
    local.mkdir(parents=True)
    git(local, "init", "-q")
    (local / "f").write_text("1\n")
    git(local, "add", "-A"); git(local, "commit", "-qm", "1")
    first = git(local, "rev-parse", "HEAD")
    (local / "f").write_text("2\n")
    git(local, "add", "-A"); git(local, "commit", "-qm", "2")

    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app",
                                       pin_commit="0" * 40))     # manifest pin is WRONG on purpose
    stacks = (Stack(id="s", name="s", main="app", components=(comp,)),)
    paths = Paths(runtime_root=tmp_path / "rt")
    # a confirmed composition pins app -> FIRST commit
    known_working.record(paths, "s",
                         {"app": {"commit": first, "selector": "dev", "remote": "",
                                  "source_rel": "src/app", "strategy": ""}},
                         {"confirmed_at": 1.0})
    Installer(paths, stacks,
              Config(values={"install": {"adopt_search_root": str(tmp_path / "rt" / "nope")}}),
              RealSystem())
    # local fallback tree is at HEAD (2nd commit) != composition commit -> pinned FAILS closed
    inst2 = Installer(paths, stacks,
                      Config(values={"install": {"adopt_search_root": str(tmp_path / "rt" / "local")}}),
                      RealSystem())
    action = inst2.adopt_source(comp, source="pinned")
    assert action.status == "failed"                              # cannot prove composition commit
    # move the local tree to the composition commit -> pinned resolves + labels known-working
    git(local, "checkout", "-q", first)
    action2 = inst2.adopt_source(comp, source="pinned")
    assert action2.status == "done", action2.detail
    assert "known working (operator-confirmed" in action2.detail
    rec = source_registry.read_record(paths, "src/app")
    assert rec.resolved_commit == first


def test_pinned_selector_fallback_is_labelled(tmp_path):
    import subprocess
    from lhpc.core.install import Installer
    from lhpc.core.config import Config
    from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
    from lhpc.core.probes import RealSystem

    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    local = tmp_path / "rt" / "local" / "app"
    local.mkdir(parents=True)
    subprocess.run(["git", "-C", str(local), "init", "-q"], check=True, env=env)
    (local / "f").write_text("1\n")
    subprocess.run(["git", "-C", str(local), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(local), "commit", "-qm", "1"], check=True,
                   capture_output=True, env=env)
    head = subprocess.run(["git", "-C", str(local), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True, env=env).stdout.strip()

    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app", pin_commit=head))
    stacks = (Stack(id="s", name="s", main="app", components=(comp,)),)
    inst = Installer(Paths(runtime_root=tmp_path / "rt"), stacks,
                     Config(values={"install": {"adopt_search_root": str(tmp_path / "rt" / "local")}}),
                     RealSystem())
    action = inst.adopt_source(comp, source="pinned")             # NO composition exists
    assert action.status == "done", action.detail
    assert "fallback: manifest pin" in action.detail              # truthful fallback label


def test_confirm_refuses_manually_changed_source(tmp_path):
    # The tree's ACTUAL HEAD moved to a different clean commit since the healthy start:
    # the source-locked identity revalidation refuses — never a fabricated record.
    _seed_running_chat(tmp_path)
    svc = _bind_chat_identity(_svc(tmp_path, cmdlines={555: ["loraham_chat"]}), tmp_path,
                              commit="b" * 40)             # HEAD drifted a… -> b…
    res = svc.confirm_known_working("chat")
    assert not res.ok
    assert known_working.load(Paths(runtime_root=tmp_path), "chat") == []   # nothing recorded


def test_confirm_refuses_changed_remote(tmp_path):
    # The tree's origin no longer matches the configured remote: refused.
    _seed_running_chat(tmp_path)
    svc = _bind_chat_identity(_svc(tmp_path, cmdlines={555: ["loraham_chat"]}), tmp_path,
                              remote="https://github.com/EVIL/other.git")
    res = svc.confirm_known_working("chat")
    assert not res.ok
    assert known_working.load(Paths(runtime_root=tmp_path), "chat") == []


# --- stack-level compatible-composition resolution (manifest evolution) ----------------------

def _mk_stack(*comps):
    from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
    out = []
    for cid, path, remote, strategy in comps:
        out.append(Component(id=cid, name=cid, kind=ComponentKind.SERVICE,
                             source=SourceSpec(path=path, remote=remote, strategy=strategy)))
    return Stack(id="s", name="s", main=out[0].id, components=tuple(out))


def _entry(commit, path, remote="", strategy=""):
    return {"commit": commit, "selector": "dev", "remote": remote,
            "source_rel": path, "strategy": strategy}


_EFF = lambda c: (c.source.remote if c.source else "") or ""


def test_resolver_skips_newest_incomplete_uses_older_complete(tmp_path):
    # NEWEST record lacks a currently required component; an OLDER record covers the exact
    # current set -> the older complete one is selected (never a per-component mix).
    paths = Paths(runtime_root=tmp_path)
    stack = _mk_stack(("a", "src/a", "https://github.com/x/a.git", ""),
                      ("b", "src/b", "https://github.com/x/b.git", ""))
    known_working.record(paths, "s",
                         {"a": _entry("1" * 40, "src/a", "https://github.com/x/a.git"),
                          "b": _entry("2" * 40, "src/b", "https://github.com/x/b.git")},
                         {"confirmed_at": 1.0})
    known_working.record(paths, "s",
                         {"a": _entry("3" * 40, "src/a", "https://github.com/x/a.git")},
                         {"confirmed_at": 2.0})                        # newest: b missing
    got = known_working.compatible_composition(paths, stack, _EFF)
    assert got is not None
    assert got["a"]["commit"] == "1" * 40 and got["b"]["commit"] == "2" * 40


def test_resolver_component_removed_then_readded(tmp_path):
    # History: complete(a,b) -> a-only (b was removed from the manifest for a while).
    # With b re-added to the manifest, the a-only record is ineligible and the older
    # complete record resolves BOTH components.
    paths = Paths(runtime_root=tmp_path)
    known_working.record(paths, "s",
                         {"a": _entry("1" * 40, "src/a"), "b": _entry("2" * 40, "src/b")},
                         {"confirmed_at": 1.0})
    known_working.record(paths, "s", {"a": _entry("3" * 40, "src/a")},
                         {"confirmed_at": 2.0})
    stack_b_readded = _mk_stack(("a", "src/a", "", ""), ("b", "src/b", "", ""))
    got = known_working.compatible_composition(paths, stack_b_readded, _EFF)
    assert got and got["a"]["commit"] == "1" * 40 and got["b"]["commit"] == "2" * 40
    # while b was absent from the manifest, the a-only record was the compatible one
    stack_a_only = _mk_stack(("a", "src/a", "", ""))
    got2 = known_working.compatible_composition(paths, stack_a_only, _EFF)
    assert got2 and got2["a"]["commit"] == "3" * 40


def test_resolver_identity_mismatches_are_ineligible(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    known_working.record(paths, "s",
                         {"a": _entry("1" * 40, "src/a", "https://github.com/x/a.git")},
                         {"confirmed_at": 1.0})
    # remote mismatch
    st = _mk_stack(("a", "src/a", "https://github.com/OTHER/a.git", ""))
    assert known_working.compatible_composition(paths, st, _EFF) is None
    # source-path mismatch
    st = _mk_stack(("a", "src/moved-a", "https://github.com/x/a.git", ""))
    assert known_working.compatible_composition(paths, st, _EFF) is None
    # strategy mismatch
    st = _mk_stack(("a", "src/a", "https://github.com/x/a.git", "link"))
    assert known_working.compatible_composition(paths, st, _EFF) is None
    # matching identity -> eligible
    st = _mk_stack(("a", "src/a", "https://github.com/x/a.git", ""))
    got = known_working.compatible_composition(paths, st, _EFF)
    assert got and got["a"]["commit"] == "1" * 40


def test_resolver_pre_identity_record_is_history_only(tmp_path):
    # An OLDER record without the full identity fields (no `strategy`) stays loadable as
    # history but is INELIGIBLE for source selection until re-confirmed.
    paths = Paths(runtime_root=tmp_path)
    known_working.record(paths, "s",
                         {"a": {"commit": "1" * 40, "selector": "dev", "remote": "",
                                "source_rel": "src/a"}},          # legacy shape, no strategy
                         {"confirmed_at": 1.0})
    st = _mk_stack(("a", "src/a", "", ""))
    assert known_working.compatible_composition(paths, st, _EFF) is None   # ineligible
    assert known_working.load(paths, "s")                                  # still visible
    assert known_working.newest_commit_for(paths, "s", "a") == "1" * 40    # history intact


def test_no_complete_record_means_whole_stack_fallback_never_mixed(tmp_path):
    # Installer-level: with NO complete compatible composition, EVERY component of the stack
    # resolves to the manifest-pin fallback — a partial record never contributes commits.
    from lhpc.core.config import Config
    from lhpc.core.install import Installer
    from lhpc.core.probes import RealSystem
    paths = Paths(runtime_root=tmp_path / "rt")
    known_working.record(paths, "s", {"a": _entry("1" * 40, "src/a")},
                         {"confirmed_at": 1.0})                        # covers only a
    stack = _mk_stack(("a", "src/a", "", ""), ("b", "src/b", "", ""))
    inst = Installer(paths, (stack,), Config(values={}), RealSystem())
    for comp in stack.components:
        commit, label = inst._pinned_expected(comp)
        assert commit == "" and "fallback" in label                    # BOTH fall back
    # a COMPLETE compatible record resolves BOTH from the same composition
    known_working.record(paths, "s",
                         {"a": _entry("5" * 40, "src/a"), "b": _entry("6" * 40, "src/b")},
                         {"confirmed_at": 2.0})
    got = {c.id: inst._pinned_expected(c)[0] for c in stack.components}
    assert got == {"a": "5" * 40, "b": "6" * 40}


def test_dedup_uses_complete_source_identity(tmp_path):
    # Same comp=commit but a DIFFERENT remote is a DIFFERENT composition (no false dedup).
    paths = Paths(runtime_root=tmp_path)
    e1 = {"a": _entry("1" * 40, "src/a", "https://github.com/x/a.git")}
    e2 = {"a": _entry("1" * 40, "src/a", "https://github.com/y/a.git")}
    known_working.record(paths, "s", e1, {"confirmed_at": 1.0})
    known_working.record(paths, "s", e2, {"confirmed_at": 2.0})
    assert len(known_working.load(paths, "s")) == 2                    # distinct identities
    known_working.record(paths, "s", e2, {"confirmed_at": 3.0})
    assert len(known_working.load(paths, "s")) == 2                    # true dedup still works


# --- FINAL: frozen operation plans, confirm handle-binding, candidate retirement --------------

def test_update_plan_is_frozen_against_concurrent_confirmation(tmp_path, monkeypatch):
    # A confirmation landing BETWEEN components of a multi-component update must not alter
    # the already-planned resolution: both adoptions use the ORIGINAL frozen composition.
    from lhpc.core.install import Installer, PlanAction
    paths = Paths(runtime_root=tmp_path)
    # daemon stack: loraham-daemon + radiolib (two distinct source paths)
    known_working.record(paths, "daemon", {
        "loraham-daemon": {"commit": "1" * 40, "selector": "pinned", "remote":
                           "https://github.com/makrohard/LoRaHAM_Daemon.git",
                           "source_rel": "src/loraham-daemon", "strategy": ""},
        "radiolib": {"commit": "2" * 40, "selector": "pinned",
                     "remote": "https://github.com/jgromes/RadioLib",
                     "source_rel": "src/RadioLib", "strategy": ""},
    }, {"confirmed_at": 1.0})
    seen = []
    def fake_adopt(self, comp, force=False, source="pinned", pinned_expected=None,
                   locked=False):
        seen.append((comp.id, pinned_expected))
        # CONCURRENT CONFIRMATION between components: rewrite the store with new commits
        known_working.record(paths, "daemon", {
            "loraham-daemon": {"commit": "8" * 40, "selector": "pinned", "remote":
                               "https://github.com/makrohard/LoRaHAM_Daemon.git",
                               "source_rel": "src/loraham-daemon", "strategy": ""},
            "radiolib": {"commit": "9" * 40, "selector": "pinned",
                         "remote": "https://github.com/jgromes/RadioLib",
                         "source_rel": "src/RadioLib", "strategy": ""},
        }, {"confirmed_at": 2.0})
        return PlanAction("adopt", "", f"adopt {comp.id}", status="done", detail="(fake)")
    monkeypatch.setattr(Installer, "adopt_source", fake_adopt)
    svc = _svc(tmp_path)
    svc.update("daemon", apply=True, source="pinned")
    assert len(seen) == 2
    commits = {cid: exp[0] for cid, exp in seen}
    assert commits["loraham-daemon"] == "1" * 40          # ORIGINAL plan, not 8…
    assert commits["radiolib"] == "2" * 40                # ORIGINAL plan, not 9…


def test_incompatible_known_working_across_shared_consumers_blocks(tmp_path):
    # Two NON-artifact stacks sharing one checkout, with compositions demanding DIFFERENT
    # commits for it: the frozen operation plan blocks — typed conflict, zero mutation
    # (the planner runs BEFORE any candidate/source/registry/config change).
    from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
    paths = Paths(runtime_root=tmp_path)
    remote = "https://github.com/x/shared.git"
    spec = dict(path="src/shared", remote=remote)
    c1 = Component(id="c1", name="c1", kind=ComponentKind.SERVICE,
                   source=SourceSpec(**spec))
    c2 = Component(id="c2", name="c2", kind=ComponentKind.SERVICE,
                   source=SourceSpec(**spec))
    st1 = Stack(id="s1", name="s1", main="c1", components=(c1,))
    st2 = Stack(id="s2", name="s2", main="c2", components=(c2,))
    known_working.record(paths, "s1", {
        "c1": {"commit": "a" * 40, "selector": "pinned", "remote": remote,
               "source_rel": "src/shared", "strategy": ""}}, {"confirmed_at": 1.0})
    known_working.record(paths, "s2", {
        "c2": {"commit": "b" * 40, "selector": "pinned", "remote": remote,
               "source_rel": "src/shared", "strategy": ""}}, {"confirmed_at": 1.0})
    svc = _svc(tmp_path)
    groups, conflicts = svc._plan_source_groups([(st1, c1), (st2, c2)], "pinned")
    assert groups is None and conflicts
    assert any("incompatible" in c for c in conflicts)
    # AGREEING compositions plan cleanly: one group, the shared frozen commit
    known_working.record(paths, "s2", {
        "c2": {"commit": "a" * 40, "selector": "pinned", "remote": remote,
               "source_rel": "src/shared", "strategy": ""}}, {"confirmed_at": 2.0})
    groups2, conflicts2 = svc._plan_source_groups([(st1, c1), (st2, c2)], "pinned")
    assert conflicts2 is None and len(groups2) == 1
    assert groups2[0][2] == "pinned"                      # carried selector
    assert groups2[0][3][0] == "a" * 40                   # one frozen resolution


def test_update_adopts_shared_group_once(tmp_path, monkeypatch):
    # update over BOTH consumers of one shared checkout performs exactly ONE adoption.
    from lhpc.core.install import Installer, PlanAction
    calls = []
    def fake_adopt(self, comp, force=False, source="pinned", pinned_expected=None,
                   locked=False):
        calls.append(comp.source.path)
        return PlanAction("adopt", "", f"adopt {comp.id}", status="done", detail="(fake)")
    monkeypatch.setattr(Installer, "adopt_source", fake_adopt)
    svc = _svc(tmp_path)
    svc.update("kiss", apply=True, source="dev")
    assert calls.count("src/loraham-kiss-tnc") == 1


def test_confirm_refuses_replacement_at_record_seam(tmp_path, monkeypatch):
    # The source is replaced between the handle-bound verification and the composition
    # write: the pre-record re-proof refuses; NOTHING is stored.
    import shutil
    from lhpc.core import source_fs
    paths, entries = _seed_running_chat(tmp_path)
    dest = tmp_path / "src" / "LoRaHAM_Daemon"
    fired = {"done": False}
    def hook(point, path=""):
        if point == "pre-confirm-record" and not fired["done"]:
            fired["done"] = True
            shutil.rmtree(dest)
            dest.mkdir()
            (dest / "foreign").write_text("substitute")
    monkeypatch.setattr(source_fs, "race_seam", hook)
    svc = _bind_chat_identity(_svc(tmp_path, cmdlines={555: ["loraham_chat"]}), tmp_path)
    res = svc.confirm_known_working("chat")
    assert fired["done"]
    assert not res.ok and "concurrently replaced" in res.summary
    assert known_working.load(paths, "chat") == []        # NOTHING stored
    assert (dest / "foreign").exists()                    # substitute untouched


def test_update_clears_affected_candidate_markers(tmp_path, monkeypatch):
    # a successful update retires the affected stacks' last-start candidates
    from lhpc.core.install import Installer, PlanAction
    paths = Paths(runtime_root=tmp_path)
    assert known_working.write_candidate(paths, "kiss", _entries(), "433")
    monkeypatch.setattr(Installer, "adopt_source",
                        lambda self, comp, force=False, source="pinned",
                        pinned_expected=None, locked=False:
                        PlanAction("adopt", "", "x", status="done", detail="(fake)"))
    svc = _svc(tmp_path)
    res = svc.update("kiss", apply=True, source="dev")
    assert res.ok, res.details
    assert known_working.read_candidate(paths, "kiss") is None   # candidate retired


def test_candidate_clear_failure_is_truthful_incomplete(tmp_path, monkeypatch):
    # an UNCLEARABLE candidate marker (symlink leaf) makes the update INCOMPLETE — never
    # a fully successful source operation with a stale eligible candidate left behind.
    import os as _os
    from lhpc.core.install import Installer, PlanAction
    Paths(runtime_root=tmp_path)
    d = tmp_path / "state" / "last-start"
    d.mkdir(parents=True)
    (d / "real.json").write_text("{}")
    _os.symlink("real.json", d / "kiss.json")             # unlink refuses symlink leaves
    monkeypatch.setattr(Installer, "adopt_source",
                        lambda self, comp, force=False, source="pinned",
                        pinned_expected=None, locked=False:
                        PlanAction("adopt", "", "x", status="done", detail="(fake)"))
    svc = _svc(tmp_path)
    res = svc.update("kiss", apply=True, source="dev")
    assert not res.ok                                     # truthful INCOMPLETE
    assert any("could not be cleared" in dd for dd in res.details)
    assert (d / "kiss.json").is_symlink()                 # evidence retained
