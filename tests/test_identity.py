"""Coherent global and per-stack identities — the acceptance contract.

The two validation tables from the feature brief are encoded VERBATIM as parametrized
cases; precedence (local > inherited global > refuse) is proven against the one shared
resolution path (`identity_resolution`) that every consumer uses."""

from __future__ import annotations

import pytest
from lhpc.core import validators as V
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.validators import ValidationError


def _svc(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


# ===== Licensed-stack validation examples (brief table, verbatim) =====
# columns: value, global, chat/igate/graywolf (APRS), voice, meshcom
@pytest.mark.parametrize("value,g,aprs,voice,mc", [
    ("XX0XXA",        True,  True,  True,  True),
    ("XX0XXA-10",     False, True,  True,  True),
    ("XX0XXA-15",     False, True,  True,  True),
    ("XX0XXA-16",     False, False, True,  True),
    ("XX0XXA-99",     False, False, True,  True),
    # verified against the MeshCom firmware regex: it would accept both, we refuse them —
    # "-0" is indistinguishable from no SSID (its own docs require -1..-99) and a bare
    # trailing dash is an identity with no SSID at all
    # AUDIT-FOUND: a generic 4-letter suffix is NOT accepted by the pinned MeshCom firmware
    # (^…[A-Z][A-Z]?[A-Z]?…$ — at most three), so it must not be settable as a global either:
    # the global is only inheritable if EVERY licensed stack can carry it. OE2YOTA-1 is the
    # firmware's one whitelisted exception and stays accepted (row below).
    ("X1ABCD",        False, True,  True,  False),
    ("XX0XXA-0",      False, False, True,  False),
    ("XX0XXA-",       False, False, True,  False),
    ("XX1/XX0XXA",    False, False, True,  False),
    ("XX0XXA/P",      False, False, True,  False),
    ("XX0XXA-P",      False, False, True,  False),
    ("XX1/XX0XXA/P",  False, False, False, False),   # 12 characters
    ("XX1/XX0XXA-P",  False, False, False, False),   # 12 characters
    ("N0CALL",        False, False, False, False),
    # global-negative rows (audit): the global is the INTERSECTION — digit-bearing
    # amateur structure — so a value MeshCom cannot carry can never be saved globally.
    ("ABCDEF",        False, True,  True,  False),
    ("ABC123",        False, True,  True,  False),
    ("XX1X",          True,  True,  True,  True),
    ("0X1AB",         True,  True,  True,  True),
    # the pinned firmware's one whitelisted real station callsign (4-letter suffix)
    ("OE2YOTA-1",     False, False, True,  True),
])
def test_licensed_validation_table(value, g, aprs, voice, mc):
    for fn, want in ((V.callsign_base, g), (V.callsign, aprs),
                     (V.callsign_voice, voice), (V.callsign_meshcom, mc)):
        try:
            fn(value)
            got = True
        except ValidationError:
            got = False
        assert got is want, (fn.__name__, value)


def test_empty_is_allowed_as_unset_everywhere():
    for fn in (V.callsign_base, V.callsign, V.callsign_voice, V.callsign_meshcom):
        assert fn("") == ""


# ===== Non-licensed validation examples (brief table) =====
@pytest.mark.parametrize("fn,value,ok", [
    (V.node_long, "Johannes", True), (V.node_long, "Field Node", True),
    (V.node_long, "Madrid Gateway", True), (V.node_long, "", False),
    (V.node_long, "a\x01b", False), (V.node_long, "x" * 40, False), (V.node_long, "x" * 39, True),
    (V.node_short_name, "DJ0C", True), (V.node_short_name, "FN1", True),
    (V.node_short_name, "GW", True), (V.node_short_name, "", False),
    (V.node_short_name, "ABCDE", False), (V.node_short_name, "ééé", False),  # 6 UTF-8 bytes
    (V.node_name, "Johannes", True), (V.node_name, "XX0XXA-12", True),
    (V.node_name, "Field Node", True), (V.node_name, "", False),
    (V.node_name, "a\x02b", False), (V.node_name, "x" * 32, False), (V.node_name, "x" * 31, True),
])
def test_unlicensed_validation_table(fn, value, ok):
    try:
        fn(value)
        got = True
    except ValidationError:
        got = False
    assert got is ok, (fn.__name__, value)


# ===== Effective-identity precedence (the shared resolution path) =====

LICENSED = ("chat", "igate", "voice", "graywolf", "meshcom")


def test_local_overrides_global_and_inheritance_is_never_persisted(tmp_path):
    svc = _svc(tmp_path)
    assert svc.set_operator_identity(callsign="XX0XXA").ok
    # inherit: local field EMPTY -> effective = global, source = global, nothing persisted
    for sid in LICENSED:
        row = svc.identity_resolution(sid)[0]
        assert (row["source"], row["effective"]) == ("global", "XX0XXA"), sid
        assert row["explicit"] == "", sid                       # local field stays empty
        assert svc.enforce_identity(sid)[0] is True, sid
    # local override wins, and only for that stack
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA-10"}).ok
    row = svc.identity_resolution("chat")[0]
    assert (row["source"], row["effective"]) == ("local", "XX0XXA-10")
    assert svc.identity_resolution("igate")[0]["source"] == "global"
    # clearing the local value returns to inheritance
    assert svc.save_config_bundle("chat", values={"file_call": ""}).ok
    assert svc.identity_resolution("chat")[0]["source"] == "global"


def test_licensed_with_neither_value_is_refused(tmp_path):
    svc = _svc(tmp_path)
    for sid in LICENSED:
        ok, fields, msg = svc.enforce_identity(sid)
        assert ok is False and fields, sid
        assert "callsign" in msg.lower(), sid


def test_licensed_local_only_works_without_global(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/P"}).ok
    row = svc.identity_resolution("voice")[0]
    assert (row["source"], row["effective"]) == ("local", "XX0XXA/P")
    assert svc.enforce_identity("voice")[0] is True


def test_legacy_ssid_bearing_global_is_refused_with_the_correction(tmp_path):
    # OPERATOR RULING: the global is a BASE callsign only. A pre-upgrade stored value
    # like "XX0XXA-12" must NOT inherit — it would stamp the SAME SSID onto every
    # licensed stack (the exact problem per-stack identities solve). It is reported
    # with the actionable correction and never silently used or rewritten.
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_operator_config(Paths(runtime_root=tmp_path), "XX0XXA-12")   # legacy shape
    svc._invalidate_config()
    assert svc.operator_callsign_legacy() is True
    for sid in LICENSED:
        ok, _f, msg = svc.enforce_identity(sid)
        assert ok is False, sid
        assert "global operator callsign" in msg and "--callsign XX0XXA" in msg, (sid, msg)
    # the stored value itself is untouched (no silent rewrite)
    assert svc.config().operator.callsign == "XX0XXA-12"
    # a per-stack local value unblocks that stack without touching the global
    assert svc.save_config_bundle("graywolf", values={"call": "XX0XXA-12"}).ok
    assert svc.enforce_identity("graywolf")[0] is True
    # a base global no licensed shape accepts at all is equally refused, named
    cfgmod.save_operator_config(Paths(runtime_root=tmp_path), "TOOLONGCALL99")
    svc._invalidate_config()
    ok, _f, msg = svc.enforce_identity("chat")
    assert ok is False and "global operator callsign" in msg


def test_unlicensed_never_inherits_and_requires_both_meshtastic_names(tmp_path):
    svc = _svc(tmp_path)
    assert svc.set_operator_identity(callsign="XX0XXA").ok
    ok, fields, msg = svc.enforce_identity("meshtastic")
    assert ok is False and len(fields) == 2                     # long AND short marked
    assert "operator callsign is never used" in msg
    ok, _f, _m = svc.enforce_identity("meshcore")
    assert ok is False
    # deliberate local names satisfy it
    assert svc.save_config_bundle(
        "meshtastic", values={"node_name": "Field Node", "node_short": "FN1"}).ok
    assert svc.enforce_identity("meshtastic")[0] is True
    row = svc.identity_resolution("meshtastic")[0]
    assert row["source"] == "local" and row["effective"] == "Field Node"


@pytest.mark.parametrize("stack,field,value", [
    ("meshtastic", "node_name", "LoRaHAM Pi"),
    ("meshtastic", "node_name", "LHPi"),
    ("meshcore", "file_node_name", "pyMC"),
])
def test_retired_generic_defaults_do_not_count_as_configured(tmp_path, stack, field, value):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle(stack, values={field: value}).ok
    assert svc.enforce_identity(stack)[0] is False


def test_stack_reset_returns_licensed_to_inheritance_and_unlicensed_to_required(tmp_path):
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    svc.save_config_bundle("chat", values={"file_call": "XX0XXA-10"})
    svc.save_config_bundle("meshcore", values={"file_node_name": "MyNode"})
    assert svc.reset_config("chat").ok
    assert svc.identity_resolution("chat")[0]["source"] == "global"      # inherits again
    assert svc.reset_config("meshcore").ok
    assert svc.enforce_identity("meshcore")[0] is False                  # required again


# ===== Surfaces =====

def test_cli_hint_prints_the_exact_local_command(tmp_path):
    svc = _svc(tmp_path)
    assert svc._identity_config_hints("meshcom")[0].startswith(
        "lhpc config meshcom mc_callsign YOURCALL-99")
    assert svc._identity_config_hints("meshtastic")[0].startswith(
        "lhpc config meshtastic node_name ")
    assert svc._identity_config_hints("meshcore")[0].startswith(
        "lhpc config meshcore node_name ")


def test_global_card_renders_and_never_marked_required(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    app = create_app(lambda: svc)
    html = app.test_client().get("/stacks").get_data(as_text=True)
    assert "Global operator callsign" in html
    assert "Licensed stacks (chat, iGate," in html
    card = html.split("Global operator callsign", 1)[1].split("</details>", 1)[0]
    assert "field-bad" not in card and 'class="req"' not in card


# ===== audit-found launch/materialization regressions =====

def test_tx_identity_resolves_per_band(tmp_path):
    # P2 (audit): a band-scoped local identity must identify the TX test on ITS band.
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    assert svc.stack_bands("voice") == ("433", "868")         # genuinely band-switchable
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/P"}, band="433").ok
    assert svc.effective_identity("voice", "433") == "XX0XXA/P"
    assert svc.effective_identity("voice", "868") == "XX0XXA"  # other band inherits global


def test_deliberate_local_pin_equal_to_global_survives_migration_snapshot(tmp_path):
    # AUDIT-FOUND (P2): the self-update stale-default migration compared identity params
    # against the substituted {callsign} default, so a deliberate local pin EQUAL to the
    # global was snapshotted and later removed — silently reverting the stack to inherit.
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA"}).ok
    assert svc.identity_resolution("chat")[0]["source"] == "local"       # a real pin
    cands = svc._migration_candidates()
    assert not [c for c in cands if c["stack"] == "chat" and "call" in c["key"]], cands


# ===== external-audit regressions =====

def test_start_refuses_identity_before_boot_hook_and_feed_clear(tmp_path, monkeypatch):
    # P1 (audit): a CLI/API/boot-restore start with a missing identity previously ran the
    # boot-restore claim hook and cleared the daemon feed before the inner check refused.
    from lhpc.core.services import ActionResult
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "is_installed", lambda self, t: True)
    monkeypatch.setattr(type(svc), "is_built", lambda self, c: True)
    touched = []
    monkeypatch.setattr(type(svc), "clear_daemon_feed",
                        lambda self, *a, **kw: touched.append("feed") or 0, raising=False)
    hook = lambda: touched.append("hook") or None                       # noqa: E731
    for target in ("meshtastic", "graywolf"):                           # unlicensed + licensed
        res = svc.start(target, apply=True, _before_start_locked=hook)
        assert res.ok is False, target
        assert res.data.get("enforce_fields"), target
    assert touched == []                                                # NOTHING mutated
    # and the refusal is gone once the identity resolves
    svc.set_operator_identity(callsign="XX0XXA")
    monkeypatch.setattr(type(svc), "_start_impl",
                        lambda self, *a, **kw: ActionResult(True, "reached impl"))
    res = svc.start("graywolf", apply=True, _before_start_locked=hook)
    assert res.summary == "reached impl" and "hook" in touched


def test_settings_page_never_prefills_the_inherited_global(tmp_path):
    # P1 (audit): config_param_groups fed the substituted global into the Settings input,
    # so saving ANY other setting persisted it as a local override.
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    found = []

    def walk(o):                                  # structure-agnostic row search
        if isinstance(o, dict):
            if o.get("is_identity"):
                found.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
    walk(svc.config_param_groups("chat"))
    assert found, "identity row not found on the Settings surface"
    r = found[0]
    assert r["value"] == ""                                   # NEVER the substituted global
    assert r["default"] == ""                                 # reset = inherit
    assert "inherits global XX0XXA" in r["identity_hint"]
    # the audit's end-to-end scenario: save another setting, identity stays inherited
    assert svc.save_config_bundle("chat", values={"file_tx_freq": "434.100"}).ok
    assert svc._stored_param_value("chat", "file", "loraham-chat", "call") == ""
    svc.set_operator_identity(callsign="XX0XXB")
    assert svc.identity_resolution("chat")[0]["effective"] == "XX0XXB"   # still follows global


def test_fresh_meshtastic_refusal_prints_both_commands(tmp_path):
    # P2 (audit): one copyable command per missing identity field, same attempt.
    svc = _svc(tmp_path)
    hints = svc._identity_config_hints("meshtastic")
    assert len(hints) == 2, hints
    assert hints[0].startswith("lhpc config meshtastic node_name ")
    assert hints[1].startswith("lhpc config meshtastic node_short ")
    res = svc.start("meshtastic", apply=True)
    assert [c for c in res.next_commands if "node_name" in c]
    assert [c for c in res.next_commands if "node_short" in c]


@pytest.mark.parametrize("legacy,expect_local,expect_base", [
    ("XX0XXA-12", True, "XX0XXA"),     # both remedies valid -> both printed
    ("N0CALL", False, None),           # neither valid -> generic example only
    ("ABCDEFGH-12", False, None),      # derived base too long -> generic example
])
def test_legacy_corrections_are_only_printed_when_valid(tmp_path, legacy, expect_local, expect_base):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_operator_config(Paths(runtime_root=tmp_path), legacy)
    svc._invalidate_config()
    ok, _f, msg = svc.enforce_identity("chat")
    assert ok is False
    if expect_local:
        assert f"lhpc config chat call {legacy}" in msg
    else:
        assert f"call {legacy})" not in msg                    # invalid remedy never printed
    if expect_base:
        assert f"--callsign {expect_base}" in msg
    else:
        assert "--callsign YOURCALL" in msg                    # shell-safe fill-in token
    # the web card suggestion is validated the same way
    assert svc.operator_callsign_correction() == (expect_base or "")


def test_global_change_marks_running_inherited_stacks_restart_required(tmp_path, monkeypatch):
    # P2 (audit): live drift — a running licensed stack inheriting the global keeps the old
    # callsign on air; the change must mark it restart-required and say so.
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    svc.save_config_bundle("graywolf", values={"call": "XX0XXA-7"})      # local -> unaffected
    monkeypatch.setattr(type(svc), "stack_running",
                        lambda self, sid: sid in ("chat", "graywolf"))
    r = svc.set_operator_identity(callsign="XX0XXB")
    assert r.ok
    assert any("chat" in d and "restart" in d for d in r.details), r.details
    assert not any("graywolf" in d for d in r.details), r.details        # override -> untouched
    assert svc.restart_required("chat") is not None
    assert svc.restart_required("graywolf") is None
    assert "lhpc stack restart chat" in (r.next_commands or [])


# ===== external re-audit regressions (@959618e findings) =====

def test_start_identity_follows_the_implicit_running_band(tmp_path, monkeypatch):
    # P1 (audit): enforcement read the raw band argument while the launch resolved the
    # running-band marker — voice with per-band identities was judged on the wrong band.
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "running_band",
                        lambda self, sid, default="": "868" if sid == "voice" else default)
    # identity only on 433, running band 868, no global -> the 868 launch must REFUSE
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/P"},
                                  band="433").ok
    res = svc.start("voice", apply=True)
    assert not res.ok and res.data.get("enforce_fields"), res.summary
    # reverse: identity on the RUNNING band -> enforcement passes (any later refusal is
    # not an identity refusal)
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/M"},
                                  band="868").ok
    res = svc.start("voice", apply=True)
    assert "callsign is required" not in res.summary
    assert not res.data.get("enforce_fields")


def test_global_change_marking_is_band_aware(tmp_path, monkeypatch):
    # P1 (audit): affected stacks are judged on their ACTUAL running band.
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    # voice runs on 868; its 868 identity is a LOCAL override -> NOT marked
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/P"},
                                  band="868").ok
    monkeypatch.setattr(type(svc), "stack_running",
                        lambda self, sid: sid in ("voice", "chat"))
    monkeypatch.setattr(type(svc), "_effective_band",
                        lambda self, sid, fallback="": "868" if sid == "voice" else fallback)
    r = svc.set_operator_identity(callsign="XX0XXB")
    assert r.ok
    assert svc.restart_required("chat") is not None          # inheriting -> marked
    assert svc.restart_required("voice") is None             # 868 local override -> untouched
    assert "lhpc stack restart chat" in (r.next_commands or [])


def test_global_change_is_atomic_and_lock_serialized(tmp_path, monkeypatch):
    # P1 (audit): the [operator] patch and the restart markers are ONE all-or-recoverable
    # transaction inside ONE config-lock critical section.
    from lhpc.core import config as cfgmod2
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")             # seed with nothing running
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: sid == "chat")
    real = cfgmod2._atomic_write
    calls = {"n": 0}

    def flaky(paths, path, text, mode):
        calls["n"] += 1
        if calls["n"] == 3:                                  # fail the marker write
            raise OSError("simulated mid-transaction failure")
        return real(paths, path, text, mode)
    monkeypatch.setattr(cfgmod2, "_atomic_write", flaky)
    r = svc.set_operator_identity(callsign="XX0XXB")
    monkeypatch.setattr(cfgmod2, "_atomic_write", real)
    assert not r.ok
    svc._invalidate_config()
    assert svc.config().operator.callsign == "XX0XXA"        # old value restored
    assert svc.restart_required("chat") is None              # no orphaned marker
    # lock serialization: a held config lock refuses typed, mutating nothing
    with cfgmod2.config_lock(Paths(runtime_root=tmp_path)):
        r3 = svc.set_operator_identity(callsign="XX0XXB")
    assert not r3.ok and "busy" in r3.summary
    svc._invalidate_config()
    assert svc.config().operator.callsign == "XX0XXA"
    # the alternate public mutation entry is GONE
    with pytest.raises(TypeError):
        svc.save_config_bundle("chat", callsign="XX0XXA")


def test_poststart_refuses_bad_identity_before_any_runner_cancellation(tmp_path, monkeypatch):
    # P1 (audit): poststart bypassed identity enforcement and fed raw {callsign}
    # substitution to identity-bearing post steps.
    from lhpc.core import config as cfgmod2
    from lhpc.core.lifecycle import Lifecycle
    svc = _svc(tmp_path)
    cancelled = []
    monkeypatch.setattr(Lifecycle, "_cancel_post_runners",
                        lambda self, comp, band=None: cancelled.append(comp.id) or ([], []),
                        raising=False)
    # legacy licensed global that a normal start refuses
    cfgmod2.save_operator_config(Paths(runtime_root=tmp_path), "XX0XXA-12")
    svc._invalidate_config()
    r = svc.poststart("meshcom", apply=True)
    assert not r.ok and "global operator callsign" in r.summary
    # missing unlicensed identity
    r2 = svc.poststart("meshtastic", apply=True)
    assert not r2.ok and r2.data.get("enforce_fields")
    assert cancelled == []                                   # refused BEFORE cancellation


def test_refusal_remedy_targets_the_refusing_band(tmp_path, monkeypatch):
    # P2 (audit): the refusal is judged on the running band, so the printed remedy must
    # carry --band for that band — a band-less hint saved into the primary store and the
    # same refusal repeated.
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "running_band",
                        lambda self, sid, default="": "868" if sid == "voice" else default)
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/P"},
                                  band="433").ok
    res = svc.start("voice", apply=True)
    assert not res.ok and res.data.get("enforce_fields")
    assert any(c.startswith("lhpc config voice callsign") and "--band 868" in c
               for c in res.next_commands), res.next_commands
    # a bandless stack's hint stays without --band
    assert not any("--band" in c for c in svc._identity_config_hints("meshtastic"))


# ===== external CLOSURE-audit regressions (@ad9e6a8 findings) =====

def _lite_voice_marker(svc, band="868"):
    svc.mark_interactive("voice", band)
    return svc


def test_lite_interactive_marker_keeps_a_bandless_start_on_its_band(tmp_path, monkeypatch):
    # P1 (audit): the interactive marker is Voice-on-Lite's only band record; a bandless
    # second start must judge identity AND plan daemon/locks/feed on that band — and a
    # stale marker must never override a usable desktop GTK main.
    svc = _svc(tmp_path)                                        # no GTK -> fallback active
    _lite_voice_marker(svc, "868")
    assert svc._launch_band_hint("voice", "") == "868"          # marker drives the hint
    assert svc._launch_band_hint("voice", "433") == "433"       # explicit still wins
    # identity judged on 868: a 433-only local value refuses; an 868 value passes
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/P"},
                                  band="433").ok
    cleared = []
    monkeypatch.setattr(type(svc), "clear_daemon_feed",
                        lambda self, b, *a, **kw: cleared.append(b) or 0, raising=False)
    res = svc.start("voice", apply=True)
    assert not res.ok and res.data.get("enforce_fields")        # refused ON 868
    assert any("--band 868" in c for c in res.next_commands), res.next_commands
    assert cleared == []                                        # refusal before feed work
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/M"},
                                  band="868").ok
    res = svc.start("voice", apply=True)
    assert "callsign is required" not in res.summary
    assert "433" not in cleared                                 # feed/locks scoped to 868
    # stale-marker guard: with a USABLE desktop GTK main the marker is ignored
    svc_d = _svc(tmp_path / "desk")
    monkeypatch.setattr(type(svc_d), "gui_fallback_active", lambda self, st: False)
    _lite_voice_marker(svc_d, "868")
    assert svc_d._launch_band_hint("voice", "") == ""           # falls back to primary


def test_pending_journal_is_recovered_before_global_mutation_reads(tmp_path):
    # P1 (audit): the setter previously read/patched a partially-written local.toml BEFORE
    # transaction recovery — resurrecting rolled-back data. Recovery must come first.
    import json
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    local = tmp_path / "config" / "local.toml"
    good = local.read_text()                                    # the committed pre-image
    local.write_text('[operator]\ncallsign = "TORNVAL"\n[half]\n')   # simulated partial write
    jp = tmp_path / "state" / "config-txn.json"
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps({"version": 1, "targets": [
        {"kind": "local", "rel": "config/local.toml", "pre": good,
         "existed": True, "mode": 0o600}]}))
    svc._invalidate_config()
    r = svc.set_operator_identity(callsign="XX0XXB")
    assert r.ok, r.summary
    text = local.read_text()
    assert "TORNVAL" not in text and "[half]" not in text       # partial write NEVER read
    assert 'callsign = "XX0XXB"' in text                        # new global on recovered state
    assert not jp.exists()
    # an UNRECOVERABLE journal refuses typed, touching nothing
    local2 = tmp_path / "b"; svc2 = _svc(local2)
    svc2.set_operator_identity(callsign="XX0XXA")
    before = (local2 / "config" / "local.toml").read_text()
    j2 = local2 / "state" / "config-txn.json"
    j2.parent.mkdir(parents=True, exist_ok=True)
    j2.write_text("{ not json")
    r2 = svc2.set_operator_identity(callsign="XX0XXB")
    assert not r2.ok and "pending configuration transaction" in r2.summary
    assert (local2 / "config" / "local.toml").read_text() == before
    assert j2.exists()                                          # journal retained


def test_global_change_sees_the_lite_fallback_and_preserves_build_markers(tmp_path, monkeypatch):
    # P1 (audit): the active Voice Lite fallback (main gui-skipped, marker presented) was
    # invisible to stack_running(); and a blind marker replace destroyed a stronger
    # build-required warning.
    import json
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    _lite_voice_marker(svc, "868")                              # voice inheriting, marker 868
    # chat: running with a PRE-EXISTING build marker
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: sid == "chat")
    mp = svc._restart_marker_path("chat")
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps({"version": 1, "stack": "chat", "mode": "build",
                              "params": ["firmware env"], "band": "433",
                              "created_at": 1.0}))
    r = svc.set_operator_identity(callsign="XX0XXB")
    assert r.ok
    # voice fallback detected, band preserved in the warning
    assert any("voice" in d and "868" in d for d in r.details), r.details
    assert svc.restart_required("voice") is not None
    assert svc.restart_required("voice")["band"] == "868"
    # chat's build marker MERGED, not replaced: build > restart, params unioned, band kept
    m = svc.restart_required("chat")
    assert m["mode"] == "build" and m["band"] == "433"
    assert "firmware env" in m["params"] and "callsign (inherited global)" in m["params"]
    # an UNSAFE marker is left untouched and disclosed
    svc2 = _svc(tmp_path / "b")
    svc2.set_operator_identity(callsign="XX0XXA")
    monkeypatch.setattr(type(svc2), "stack_running", lambda self, sid: sid == "chat")
    mp2 = svc2._restart_marker_path("chat")
    mp2.parent.mkdir(parents=True, exist_ok=True)
    mp2.write_text("{ garbage")
    r2 = svc2.set_operator_identity(callsign="XX0XXB")
    assert r2.ok and any("left untouched" in d for d in r2.details), r2.details
    assert mp2.read_text() == "{ garbage"


def test_poststart_config_change_after_preflight_refuses_before_cancellation(tmp_path, monkeypatch):
    # P1 (audit): the locked backstop previously cancelled the live runner BEFORE
    # re-resolving identity — a config change between the public preflight and the locked
    # impl must refuse with ZERO mutation.
    from lhpc.core import config as cfgmod2
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.model import RunState
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")               # preflight will PASS
    cancelled = []
    monkeypatch.setattr(Lifecycle, "_cancel_post_runners",
                        lambda self, comp, band=None: cancelled.append(comp.id) or ([], []),
                        raising=False)
    # every component reports RUNNING so the pre-pass actually judges it
    class _St:
        run_state = RunState.RUNNING
    real_snap = type(svc).build_snapshot

    def snap_running(self):
        s = real_snap(self)
        for ss in s.stacks:
            for cid in list(ss.components):
                ss.components[cid] = _St()
        return s
    monkeypatch.setattr(type(svc), "build_snapshot", snap_running)
    # the config change lands BETWEEN the public preflight and the locked impl
    real_stable = type(svc)._config_stable

    def stable_and_break_config(self, *a, **kw):
        cfgmod2.save_operator_config(self._paths, "")           # global gone mid-window
        self._invalidate_config()
        return real_stable(self, *a, **kw)
    monkeypatch.setattr(type(svc), "_config_stable", stable_and_break_config)
    r = svc.poststart("meshcom", apply=True)
    assert not r.ok and r.data.get("enforce_fields"), r.summary
    assert cancelled == []                                      # refused BEFORE any mutation


def test_legacy_correction_executes_through_the_real_cli_on_the_refusing_band(tmp_path, monkeypatch):
    # P2 (audit): the displayed legacy correction must target the judged band and actually
    # clear the refusal when executed through the real CLI.
    import re
    from lhpc.core import config as cfgmod2
    from lhpc.adapters.cli.main import main
    svc = _svc(tmp_path)
    cfgmod2.save_operator_config(Paths(runtime_root=tmp_path), "XX0XXA-12")   # legacy global
    svc._invalidate_config()
    monkeypatch.setattr(type(svc), "running_band",
                        lambda self, sid, default="": "868" if sid == "voice" else default)
    ok, _f, msg = svc.enforce_identity("voice", svc._launch_band_hint("voice", ""))
    assert ok is False
    m = re.search(r"\((lhpc config voice \S+ \S+ --band 868)\)", msg)
    assert m, msg                                               # band-aware remedy IN the text
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    assert main(m.group(1).split()[1:]) == 0                    # executed through the real CLI
    svc._invalidate_config()
    assert svc.enforce_identity("voice", "868")[0] is True      # the refusal clears


# ===== closure RE-audit regressions (@b3f42bb findings) =====

def test_marker_dismissed_mid_start_cannot_move_the_operation_band(tmp_path, monkeypatch):
    # P1 (audit): the inner path re-resolved the band hint, and the interactive marker is
    # mutable outside the guards — a dismiss in the window moved the applied operation off
    # the band everything was planned/locked/cleared for. The resolved CONCRETE band now
    # travels into _start_impl.
    svc = _svc(tmp_path)
    _lite_voice_marker(svc, "868")
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/P"},
                                  band="868").ok                # identity ONLY on 868
    seen = {}
    real_impl = type(svc)._start_impl

    def impl_after_dismiss(self, target, apply=False, **kw):
        # the marker vanishes between outer planning and the inner applied path
        try:
            self._interactive_marker("voice").unlink()
        except OSError:
            pass
        seen["band"] = kw.get("band")
        return real_impl(self, target, apply=apply, **kw)
    monkeypatch.setattr(type(svc), "_start_impl", impl_after_dismiss)
    res = svc.start("voice", apply=True)
    assert seen.get("band") == "868"                            # immutable resolved band
    # identity was approved on 868 and MUST NOT be re-refused on the primary after the
    # marker vanished (any later failure is not an identity refusal)
    assert "callsign is required" not in res.summary
    assert not res.data.get("enforce_fields"), res.summary


def test_fresh_identity_hints_are_shell_safe_templates(tmp_path):
    # P1 (audit): <angle-bracket> placeholders are shell redirection; quoted ones reach
    # validation literally. Fresh refusal hints must be shell-safe, marked-for-replacement
    # templates naming every required parameter.
    import shlex
    svc = _svc(tmp_path)
    for target, needles in (("chat", ["call"]),
                            ("meshtastic", ["node_name", "node_short"]),
                            ("meshcom", ["mc_callsign"])):
        hints = svc._identity_config_hints(target)
        assert len(hints) == len(needles), (target, hints)
        for hint, needle in zip(hints, needles):
            assert "<" not in hint and ">" not in hint, hint
            cmdpart = hint.split("   #", 1)[0]
            toks = shlex.split(cmdpart)                        # shell-parseable
            assert toks[:2] == ["lhpc", "config"] and needle in toks, (hint, needle)
            assert "   # " in hint and "=" in hint             # replace-me marking


@pytest.mark.parametrize("bad", [
    {"version": 1, "stack": "chat", "mode": "unexpected", "params": [], "band": "",
     "created_at": 1.0},
    {"version": 1, "stack": "chat", "mode": "build", "params": "firmware env",
     "band": "", "created_at": 1.0},
    {"version": 1, "stack": "chat", "mode": "build", "params": 42, "band": "",
     "created_at": 1.0},
    {"version": 1, "stack": "chat", "mode": "build", "params": ["x"], "band": "9zz",
     "created_at": 1.0},
])
def test_structurally_invalid_markers_are_unsafe_and_never_rewritten(tmp_path, monkeypatch, bad):
    # P2 (audit): parseable-but-invalid markers were trusted — unknown mode downgraded,
    # string params iterated char-by-char, integer params raised uncaught.
    import json
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: sid == "chat")
    mp = svc._restart_marker_path("chat")
    mp.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(bad)
    mp.write_text(raw)
    m = svc.restart_required("chat")
    assert m is not None and m.get("unsafe"), m                # safe-side, typed
    r = svc.set_operator_identity(callsign="XX0XXB")
    assert r.ok and any("left untouched" in d for d in r.details), r.details
    assert mp.read_text() == raw                               # byte-identical


def test_pasting_a_hint_template_verbatim_never_yields_a_transmitting_identity(tmp_path):
    # REVIEW-FOUND: bare YOURCALL passed the voice shape and NODENAME passed the node
    # validators — a verbatim paste of the printed remedy must never produce a startable
    # placeholder identity.
    svc = _svc(tmp_path)
    r = svc.save_config_bundle("voice", values={"file_callsign": "YOURCALL"})
    assert not r.ok and "placeholder" in " ".join(r.details)     # refused at save
    assert svc.save_config_bundle("meshcore", values={"file_node_name": "NODENAME"}).ok
    assert svc.enforce_identity("meshcore")[0] is False          # never satisfies a start
    assert svc.save_config_bundle(
        "meshtastic", values={"node_name": "NODENAME", "node_short": "FN1"}).ok
    assert svc.enforce_identity("meshtastic")[0] is False


def test_no_angle_bracket_placeholder_in_any_printed_remedy(tmp_path):
    # REVIEW-FOUND: one <YOURCALL> survived in the legacy-global fallback remedy.
    from lhpc.core import config as cfgmod2
    svc = _svc(tmp_path)
    for legacy in ("N0CALL", "ABCDEFGH-12"):
        cfgmod2.save_operator_config(Paths(runtime_root=tmp_path), legacy)
        svc._invalidate_config()
        ok, _f, msg = svc.enforce_identity("chat")
        assert ok is False
        assert "<" not in msg and ">" not in msg, msg
        assert "--callsign YOURCALL" in msg


def _config_bytes(svc):
    """Every persisted config file, content-addressed — proves a refused operation wrote NOTHING."""
    root = svc._paths.runtime_root / "config"
    return {str(f.relative_to(root)): f.read_bytes()
            for f in sorted(root.rglob("*")) if f.is_file()}


def test_identity_is_not_saved_when_admission_refuses(tmp_path, monkeypatch):
    # AUDIT-FOUND (0.2.8): the identity was written BEFORE task admission. 0.2.9 removed the save
    # from the start, but the contract stays: a start/restart refused by a pending self-update/
    # uninstall/power operation changes NOTHING — configuration included.
    import json
    from lhpc.core import lifecycle as lcmod
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    assert svc.save_config_bundle("igate", values={"call": "XX0XXA-5"}).ok
    before = _config_bytes(svc)
    (svc._paths.runtime_root / "state").mkdir(exist_ok=True)
    svc._power_pending_path().write_text(json.dumps(
        {"kind": "poweroff", "boot_id": "boot-1", "requested_uptime": 100.0}))
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    for op in (svc.start, svc.restart):
        r = op("igate", apply=True)
        assert not r.ok and r.data.get("admission_blocked")
        assert _config_bytes(svc) == before                  # byte-identical, nothing written
    assert svc._stored_param_value("igate", "run", "igate", "call") == "XX0XXA-5"


def test_restart_panel_and_restart_agree_on_the_band_with_a_stale_marker(tmp_path, monkeypatch):
    # AUDIT-FOUND: restart resolved its band with _effective_band, which takes an interactive marker
    # UNCONDITIONALLY, while the panel and start use _launch_band_hint, which ignores a marker whose
    # GUI fallback is no longer active. A stopped Voice with a stale 868 marker on a box that has
    # regained a usable GTK main therefore showed the 433 identity and wrote it to the 868 store —
    # and a blank 433 field CLEARED a deliberate 868 override. Both now use the one resolver.
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/868"},
                                  band="868").ok        # deliberate local identity on 868
    monkeypatch.setattr(type(svc), "interactive_band", lambda self, sid: "868")   # STALE marker
    monkeypatch.setattr(type(svc), "gui_fallback_active", lambda self, sid: False)  # GTK is back
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: False)
    seen = []
    # Spy on the LAUNCH — the deepest frame that uniquely determines the behaviour under test, since
    # `_op_band` is what config generation, feed clearing and the launch all consume (review-found:
    # spying on _restart_impl watched the value the public entry passes DOWN, one frame above the
    # inner resolver, and accepted the "" passed in every case — so it never observed the band the
    # operation actually ran on and missed a surviving unconditional-marker resolution in
    # _restart_impl_inner). The dry run reaches _start_impl too, so ONE spy pins plan and apply.
    real = type(svc)._start_impl
    def spy(self, target, **kw):
        seen.append(kw.get("band"))
        return real(self, target, **kw)
    monkeypatch.setattr(type(svc), "_start_impl", spy)
    # the whole operation — plan and launch — must stay on the PRIMARY band (433), because the
    # stale marker is not active...
    plan = svc.restart("voice")                                   # dry run
    svc.restart("voice", apply=True)
    # NEITHER frame may carry the stale marker's band. The plan frame legitimately receives an
    # unresolved "" — public start()'s dry-run branch returns before the operation band is resolved,
    # and _start_impl resolves it itself — so the plan is anchored on its rendered band as well.
    assert "868" not in seen, seen
    assert seen[-1] == "433", seen                                 # the LAUNCH band, resolved once
    assert "868" not in " ".join(plan.details), plan.details
    assert svc._stored_param_value("voice", "file", "loraham-voice",
                                   "callsign", "868") == "XX0XXA/868"   # NOT cleared


def test_restart_band_is_resolved_under_admission_not_before(tmp_path, monkeypatch):
    # AUDIT-FOUND: the applied restart resolved its band BEFORE task admission. If another admitted
    # operation started the stack on 868 in that window, the identity was saved on the primary 433
    # while the inner restart re-resolved 868 and stopped/preflighted/launched there — store,
    # operator request and on-air identity all disagreeing. The band is now resolved ONCE, under
    # admission, and carried concretely.
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    b433 = svc._paths.runtime_root / "config" / "stacks" / "voice@433.toml"
    seen = {}
    real_guard = type(svc)._admission_guard
    def guard(self, op, target=""):
        # the real running state flips to 868 exactly as admission is entered — after the old
        # pre-admission resolution point, before the new one
        monkeypatch.setattr(type(svc), "running_band", lambda s, sid, d="": "868")
        return real_guard(self, op, target)
    monkeypatch.setattr(type(svc), "_admission_guard", guard)
    real_stop = type(svc).stop
    monkeypatch.setattr(type(svc), "stop",
                        lambda self, t, **kw: (seen.__setitem__("stop", kw.get("band")),
                                               real_stop(self, t, **kw))[1])
    real_impl = type(svc)._start_impl
    def impl(self, target, **kw):
        seen["launch"] = kw.get("band")
        return real_impl(self, target, **kw)
    monkeypatch.setattr(type(svc), "_start_impl", impl)
    before = b433.read_bytes() if b433.exists() else None
    svc.restart("voice", apply=True)
    assert seen["stop"] == "868", seen        # the band the operation runs on is the one it stops
    assert seen["launch"] == "868", seen      # ...and the same band it launches
    assert (b433.read_bytes() if b433.exists() else None) == before   # 433 byte-identical


def test_a_fixed_band_client_never_scopes_the_daemon_to_another_band(tmp_path):
    # REVIEW-FOUND, introduced by the daemon fix in this same round: `or hint` rescued the DAEMON's
    # explicit band, but a FIXED-band client has no per-band store either — so `--band 433` on
    # meshcore (868-only) would have ensured, locked and cleared the daemon on 433, i.e. RF on the
    # wrong radio from one CLI flag. Only the daemon keeps a raw band.
    svc = _svc(tmp_path)
    assert svc.operation_band("meshcore", "433") == ""       # flattened, as it always was
    assert svc.operation_band("daemon", "433") == "433"      # the daemon still honours it
    order = svc._run_order("meshcore")
    assert svc._daemon_needs(order, svc.operation_band("meshcore", "433"))[0] == "868"


def test_identity_policy_reads_component_qualified_bundle_keys(tmp_path):
    # REVIEW-FOUND: a DEPENDENCY bundle addresses params component-qualified, while the policy
    # looked up only the bare name — so the dependency identity guard was a silent no-op. Latent
    # today (no dependency declares an identity), but a guard must do what its comment claims.
    svc = _svc(tmp_path)
    bare = svc.identity_refusal_for_values("meshtastic", "", {"node_name": ""})
    qual = svc.identity_refusal_for_values("meshtastic", "", {"meshtastic.node_name": ""})
    assert bare is not None and qual is not None            # BOTH shapes are seen
    assert qual.data.get("enforce_fields") == ["c_node_name"]      # the Settings field
    fq = svc.identity_refusal_for_values("meshcore", "", {"file_meshcore-node.node_name": ""})
    assert fq is not None                                   # ...including the file-kind shape


def test_daemon_operation_band_reaches_the_radio_sink(tmp_path, monkeypatch):
    # AUDIT-FOUND: `operation_band("daemon","433")` was correct, but `_start_impl_inner` flattened
    # it again through `_config_band` — and the daemon has no per-band store, so the terminal sink
    # got "" and a start planned, locked and feed-cleared for 433 asked for ALL active bands.
    svc = _svc(tmp_path)
    plan = svc.start("daemon", apply=False, band="433")
    assert any("--radio 433" in d for d in plan.details), plan.details
    assert not any("all bands" in d or "868" in d for d in plan.details), plan.details
    seen = {}
    def spy(self, lc, stk, comp, running, radio, start_sid, **kw):
        seen["radio"] = radio
        raise _Stop
    monkeypatch.setattr(type(svc), "_ensure_daemon", spy)
    with pytest.raises(_Stop):
        svc.start("daemon", apply=True, band="433")          # no duplicate p_radio submitted
    assert seen["radio"] == "433", seen


class _Stop(Exception):
    """Seam: stop the applied start at the terminal daemon sink."""


def test_local_identity_tracks_an_active_lite_fallback(tmp_path, monkeypatch):
    # AUDIT-FOUND: the global setter treats a presented Voice-Lite fallback as a live identity
    # consumer; the local save path checked only `stack_running`, so the TUI kept running on the
    # old callsign while the store said something else, with no warning.
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "gui_fallback_active", lambda self, st: True)
    monkeypatch.setattr(type(svc), "interactive_band", lambda self, sid: "868")
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: False)
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/P"}, band="868").ok
    marker = svc.restart_required("voice")
    assert marker is not None, "a presented Lite fallback is a live consumer"
    assert marker["band"] == "868" and "callsign" in marker["params"], marker


def test_local_identity_save_merges_instead_of_destroying_a_build_marker(tmp_path, monkeypatch):
    # AUDIT-FOUND: the global setter merges markers; the local save path replaced blindly, so a
    # stronger BUILD requirement, its reason and its band were destroyed by an ordinary identity
    # save. One merge implementation now serves both writers.
    import json, time
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: True)
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA-7"}).ok
    path = svc._restart_marker_path("chat")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "stack": "chat", "mode": "build",
                                "params": ["firmware env"], "band": "433",
                                "created_at": time.time()}))
    svc.set_operator_identity(callsign="XX0XXA")
    assert svc.save_config_bundle("chat", values={"file_call": ""}).ok
    m = svc.restart_required("chat")
    assert m["mode"] == "build", m                     # build outranks restart
    assert m["params"] == ["firmware env", "call"], m  # reasons unioned, no duplication
    assert m["band"] == "433", m                       # the concrete band is retained


def test_marker_merge_survives_a_concurrent_writer(tmp_path, monkeypatch):
    # REVIEW-FOUND: the merge read the existing marker while BUILDING the transaction, outside the
    # config lock, so a marker committed in that window was overwritten and its reason lost. The
    # payload is now rendered INSIDE the transaction, the same merge-in-transaction contract the
    # stack/local renderers use.
    import json, time
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: True)
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA-7"}).ok
    path = svc._restart_marker_path("chat")
    # another writer commits a marker; the payload is built AFTERWARDS, inside the transaction
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "stack": "chat", "mode": "build",
                                "params": ["callsign (inherited global)"], "band": "868",
                                "created_at": time.time()}))
    merged = json.loads(svc.restart_marker_payload("chat", ["call"], "433"))
    assert merged["mode"] == "build", merged                       # the concurrent build survives
    assert merged["params"] == ["callsign (inherited global)", "call"], merged
    assert merged["band"] == "868", merged


def test_a_save_that_cannot_mark_does_not_probe_the_box(tmp_path, monkeypatch):
    # REVIEW-FOUND regression from this round's first draft: the live-consumer probe ran on EVERY
    # save. It rebuilds the process snapshot, so an ordinary save paid a full probe sweep — real
    # cost on a Zero 2 W. It is now probed only when a marker could actually be written.
    svc = _svc(tmp_path)
    calls = []
    real = type(svc).active_config_consumer
    monkeypatch.setattr(type(svc), "active_config_consumer",
                        lambda self, sid: (calls.append(sid), real(self, sid))[1])
    assert svc.save_config_bundle("chat", values={}).ok        # nothing marker-relevant
    assert calls == [], calls


def test_marker_is_written_when_the_stack_goes_live_while_the_save_waits(tmp_path, monkeypatch):
    # AUDIT-FOUND RACE: `save_config_bundle` decided "is anything live?" BEFORE taking the exclusive
    # config lock. A stack that became live while the save queued for that lock committed its
    # changed callsign with NO restart marker — the running instance kept the old identity silently.
    #
    # CAUSAL, not timed (review-found: a sleep could land on the wrong side of the pre-lock build on
    # a slow box and pass against the defect). The stack goes live exactly at the transaction
    # boundary — after the pre-lock build, before anything is rendered under the lock — so the two
    # implementations are forced apart: a decision taken during the build sees a dead stack and
    # writes no marker; a decision taken inside the transaction sees the live one and marks.
    from lhpc.core import service_params as _sp
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA-7"}).ok
    live = {"v": False}
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: live["v"])
    real_txn = _sp.apply_config_transaction
    def go_live_then_commit(paths, targets):
        live["v"] = True
        return real_txn(paths, targets)
    monkeypatch.setattr(_sp, "apply_config_transaction", go_live_then_commit)
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA-8"}).ok
    assert svc._stored_param_value("chat", "file", "loraham-chat", "call") == "XX0XXA-8"
    assert svc.restart_required("chat") is not None, "a live consumer MUST be warned"


def test_a_change_on_another_band_does_not_mark_the_live_band(tmp_path, monkeypatch):
    # AUDIT-FOUND: Voice live on 868, the 433 store edited -> a marker was written for 433 although
    # the configuration the live instance reads did not change at all.
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/868"}, band="868").ok
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: sid == "voice")
    monkeypatch.setattr(type(svc), "running_band", lambda self, sid, d="": "868")
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/433"}, band="433").ok
    assert svc.restart_required("voice") is None, "the live 868 config did not change"
    # ...while a change on the LIVE band still marks
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/8"}, band="868").ok
    m = svc.restart_required("voice")
    assert m is not None and m["band"] == "868", m


def test_an_identity_no_op_writes_no_restart_marker(tmp_path, monkeypatch):
    # AUDIT-FOUND: the marker compared the pre-save EFFECTIVE value against the RAW submitted one,
    # so clearing a licensed callsign that was already inherited — or that equalled the global —
    # demanded a restart although the running process still matches the resulting configuration.
    import json, time
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: True)
    # A: already inheriting, save blank
    assert svc.save_config_bundle("chat", values={"file_call": ""}).ok
    assert svc.restart_required("chat") is None
    # B: explicit local EQUAL to the global, then switch to inheritance
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA"}).ok
    svc._restart_marker_path("chat").unlink(missing_ok=True)
    assert svc.save_config_bundle("chat", values={"file_call": ""}).ok
    assert svc.restart_required("chat") is None, svc.restart_required("chat")
    # C: the effective identity genuinely changes -> marker
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA-9"}).ok
    m = svc.restart_required("chat")
    assert m is not None and "call" in m["params"], m
    # D: an existing BUILD marker survives a genuine change, never downgraded
    svc._restart_marker_path("chat").write_text(json.dumps(
        {"version": 1, "stack": "chat", "mode": "build", "params": ["firmware env"],
         "band": "433", "created_at": time.time()}))
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA-4"}).ok
    m = svc.restart_required("chat")
    assert m["mode"] == "build" and m["params"] == ["firmware env", "call"], m
    assert m["band"] == "433", m
def test_save_only_rechecks_the_identity_inside_its_write(tmp_path, monkeypatch):
    # REVIEW-FOUND: Save-only is the one panel path that does NOT defer the identity to a launch,
    # so its `_identity_guard=True` in-transaction recheck is what stops a raced blank from
    # persisting there. That parameter exists because of an earlier finding in this chain and had
    # lost its only witness when the four-path test was removed.
    import re
    from lhpc.adapters.web.app import create_app
    from lhpc.core import service_params as _sp
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA-3"}).ok
    monkeypatch.setattr(type(svc), "is_installed", lambda self, t: True)
    monkeypatch.setattr(type(svc), "unbuilt_components", lambda self, t: [])
    monkeypatch.setattr(type(svc), "missing_system_deps", lambda self, t: [])
    client = create_app(lambda: svc).test_client()
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/stacks").get_data(as_text=True)).group(1)
    real_txn = _sp.apply_config_transaction
    def clear_global_then_commit(paths, targets):
        if svc.config().operator.callsign:
            svc.set_operator_identity(callsign="")     # lands at the transaction boundary
        return real_txn(paths, targets)
    monkeypatch.setattr(_sp, "apply_config_transaction", clear_global_then_commit)
    client.post("/action", data={"_csrf": tok, "op": "start", "target": "chat", "confirmed": "yes",
                                 "_params": "1", "_save": "stack", "pf_call": ""})   # SAVE-ONLY
    assert svc._stored_param_value("chat", "file", "loraham-chat", "call") == "XX0XXA-3", \
        "Save-only must not persist a blank that can no longer inherit"


def test_settings_and_cli_may_clear_an_identity(tmp_path, monkeypatch):
    # OPERATOR RULING (frozen as policy): plain config editing MAY clear an identity — the stack
    # then cannot start until one is set, which the launch gate enforces. Only the launch paths
    # refuse. Without this witness a future tightening would pass the suite silently.
    import re
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("meshtastic",
                                  values={"node_name": "Shack", "node_short": "SHCK"}).ok
    # the CLI's service path
    assert svc.save_config_bundle("meshtastic", values={"node_name": ""}).ok
    assert svc._stored_param_value("meshtastic", "run", "meshtastic", "node_name") == ""
    assert not svc.enforce_identity("meshtastic", "")[0]        # ...and the launch gate refuses
    # the Settings page POST
    assert svc.save_config_bundle("meshtastic", values={"node_name": "Shack"}).ok
    monkeypatch.setattr(type(svc), "is_installed", lambda self, t: True)
    monkeypatch.setattr(type(svc), "unbuilt_components", lambda self, t: [])
    monkeypatch.setattr(type(svc), "missing_system_deps", lambda self, t: [])
    client = create_app(lambda: svc).test_client()
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/stacks").get_data(as_text=True)).group(1)
    client.post("/stacks/meshtastic/config",
                data={"_csrf": tok, "p_node_name": "", "p_node_short": "SHCK"})
    assert svc._stored_param_value("meshtastic", "run", "meshtastic", "node_name") == ""


def test_an_explicit_unavailable_band_is_refused_not_remapped(tmp_path, monkeypatch):
    # AUDIT-FOUND: `--band 433` on 868-only hardware was silently remapped — the plan and the launch
    # went to 868 although the caller (or boot-restore, replaying a recorded band) asked for 433.
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "active_bands", lambda self: ("868",))
    for op in ("start", "restart"):
        r = getattr(svc, op)("voice", apply=False, band="433")
        assert not r.ok and "433" in r.summary and "868" in r.summary, r.summary
    started = []
    monkeypatch.setattr(type(svc), "_start_impl", lambda self, t, **kw: started.append(kw))
    r = svc.start("voice", apply=True, band="433")
    assert not r.ok and not started, r.summary          # apply agrees with the plan
    r = svc.start("daemon", apply=False, band="433")
    assert not r.ok, r.summary                          # ...including the daemon preview


def test_a_presented_interactive_command_is_a_live_identity_consumer(tmp_path, monkeypatch):
    # AUDIT-FOUND: only a RUNNING main or Voice's GUI fallback counted. Chat's command can be
    # prepared and presented without the stack "running", and it carries the identity generated for
    # it — so changing that identity left the operator able to transmit as the old callsign with no
    # warning at all.
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA-3"}).ok
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: False)
    monkeypatch.setattr(type(svc), "interactive_band", lambda self, sid: "")
    live, _band = svc.active_config_consumer("chat")
    assert live is True, "a presented interactive command is a live consumer"
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA-7"}).ok
    m = svc.restart_required("chat")
    assert m is not None and "call" in m["params"], m


def test_a_tx_test_is_never_unidentified(tmp_path, monkeypatch):
    """AUDIT-FOUND, twice. First: the `DE <call>` suffix was CONDITIONAL, so a box with no identity
    transmitted a bare frame. Then: the daemon/kiss fallback took the stored global VERBATIM, so a
    0.2.5 box holding a placeholder or malformed global transmitted `DE N0CALL`.

    The spy is on `Lifecycle.run_daemon_tx_test` — the object that actually drives RF. The previous
    version of this test spied on ControllerService, which production never calls, so its
    "no frame was sent" assertion proved nothing (audit-found)."""
    from lhpc.core import config as cfgmod
    from lhpc.core.lifecycle import Lifecycle

    class _Ready:
        ready = True
    sent = []
    from lhpc.core.lifecycle import TxTestResult
    def _spy(self, band, payload):
        sent.append((band, payload))
        return TxTestResult(ok=True, band=band, txok_before=0, txok_after=1, detail="stub")
    monkeypatch.setattr(Lifecycle, "run_daemon_tx_test", _spy)
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "daemon_view", lambda self, b: _Ready())

    # every global a 0.2.5 box could legitimately hold that this version will not transmit under
    for bad in ("", "N0CALL", "XX0XXA-12", "TOOLONGCALL99", "xx"):
        cfgmod.save_operator_config(svc._paths, bad)
        svc._invalidate_config()
        for target in ("daemon", "kiss"):
            assert not svc.test(target, tx=True, apply=False).ok, (bad, target)
            assert not svc.test(target, tx=True, apply=True).ok, (bad, target)
    assert sent == [], sent                      # the real transmitter was never reached

    # a valid global identifies both the plan and the applied transmission
    cfgmod.save_operator_config(svc._paths, "XX0XXA")
    svc._invalidate_config()
    plan = svc.test("daemon", tx=True, apply=False)
    assert any("LHPC TX TEST DE XX0XXA" in d for d in plan.details), plan.details
    svc.test("daemon", tx=True, apply=True)
    assert sent and all(p == "LHPC TX TEST DE XX0XXA" for _b, p in sent), sent

def test_a_legacy_global_does_not_fake_a_restart_requirement(tmp_path, monkeypatch):
    """REVIEW-FOUND: the two sides of the "did anything change?" comparison resolved the global
    differently — the stored side through raw `{callsign}` substitution, the submitted side through
    validated inheritance. With a legacy SSID-bearing global (the state every 0.2.5 installation
    upgrades from) or a hand-edited lowercase one, a save that wrote NOTHING still flagged the
    running stack restart-required and told the operator to restart."""
    from lhpc.core import config as cfgmod
    for stored_global in ("XX0XXA-12", "xx0xxa"):
        svc = _svc(tmp_path / stored_global)
        cfgmod.save_operator_config(svc._paths, stored_global)
        svc._invalidate_config()
        monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: True)
        # chat already inherits (its local field is empty); saving that same empty value is a no-op
        assert svc.save_config_bundle("chat", values={"file_call": ""}).ok
        assert svc.restart_required("chat") is None, (stored_global, svc.restart_required("chat"))


def test_resaving_a_legacy_global_unchanged_marks_nothing(tmp_path, monkeypatch):
    """REVIEW-FOUND: the affected-stack scan compared the NORMALIZED new global against the RAW
    stored one, so merely pressing Save on the operator card — which prefills the stored value —
    flagged every running licensed stack restart-required with no effective identity change at all.
    That is the state an existing installation upgrades into."""
    from lhpc.core import config as cfgmod
    for stored in ("xx0xxa", "XX0XXA"):
        svc = _svc(tmp_path / stored)
        cfgmod.save_operator_config(svc._paths, stored)
        svc._invalidate_config()
        monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: True)
        r = svc.set_operator_identity(callsign="XX0XXA")      # the value the card prefills
        assert r.ok, r.summary
        assert not any("ACTIVE with the old" in d for d in r.details), (stored, r.details)
        assert svc.restart_required("chat") is None, stored
    # ...while a REAL change still marks every inheriting stack
    svc2 = _svc(tmp_path / "real")
    svc2.set_operator_identity(callsign="XX0XXA")
    monkeypatch.setattr(type(svc2), "stack_running", lambda self, sid: True)
    r2 = svc2.set_operator_identity(callsign="XX0XXB")
    assert any("ACTIVE with the old" in d for d in r2.details), r2.details
    assert svc2.restart_required("chat") is not None


def test_the_apply_hint_agrees_with_the_restart_marker(tmp_path, monkeypatch):
    """REVIEW-FOUND: the marker used the live-consumer predicate (running main OR a presented
    interactive command) while the hint still asked `stack_running`, so one save could tell the
    operator "applies on the next Run" and simultaneously raise restart-required for a command
    still carrying the old identity."""
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: False)
    monkeypatch.setattr(type(svc), "interactive_band", lambda self, sid: "")
    assert svc.active_config_consumer("chat")[0] is True      # presented interactive command
    r = svc.save_config_bundle("chat", values={"file_call": "XX0XXA-7"})
    assert r.ok
    marked = svc.restart_required("chat") is not None
    said_restart = any("Restart the stack to apply" in d for d in r.details)
    assert marked and said_restart, (marked, r.details)


def test_an_old_versions_candidate_cannot_delete_a_pinned_identity(tmp_path):
    """AUDIT-FOUND: the identity exclusion lived only where candidates are CHOSEN. On the real
    0.2.5 -> this-version crossing the choosing is done by the OLD code, which has no such rule,
    so a deliberately pinned local callsign equal to the global was deleted by the very update
    that ships the rule. The applier must drop it.

    Covers EVERY legacy representation 0.2.5 could emit — scoped and flat, run and file, banded
    and band-less — and asserts that unrelated parameters are NOT exempted."""
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA"}).ok    # deliberate pin

    def cand(**kw):
        base = {"stack": "chat", "band": "", "expected": "XX0XXA", "from_head": "dead"}
        return {**base, **kw}

    # 1) the full triple, file-kind (chat's `call` is a config-file param)
    assert svc._is_identity_candidate(
        cand(kind="f", comp="loraham-chat", name="call", key="file_call")) is True
    # 2) run-kind identity on another stack, banded config
    assert svc._is_identity_candidate(
        cand(stack="meshtastic", band="868", kind="r", comp="meshtastic",
             name="node_name", key="node_name")) is True
    # 3) key-only fallback (a candidate predating kind/comp/name), all three spellings
    for key in ("file_call", svc._scoped_key("f", "loraham-chat", "call")):
        assert svc._is_identity_candidate({"stack": "chat", "key": key}) is True, key
    assert svc._is_identity_candidate(
        {"stack": "meshtastic", "key": "node_name"}) is True
    # 4) unrelated parameters are NOT exempted, in any spelling
    for c in (cand(kind="f", comp="loraham-chat", name="tx_freq", key="file_tx_freq"),
              cand(kind="r", comp="loraham-chat", name="tx_freq", key="tx_freq"),
              {"stack": "chat", "key": "file_tx_freq"},
              {"stack": "chat", "key": svc._scoped_key("f", "loraham-chat", "tx_freq")}):
        assert svc._is_identity_candidate(c) is False, c
    # 5) a name that matches an identity field on a DIFFERENT component is not exempted
    assert svc._is_identity_candidate(
        cand(kind="f", comp="some-other-comp", name="call", key="file_call")) is False

    # ...and end to end: the applier drops the identity candidate and keeps the pin
    migrated, _rem = svc._run_migration(
        [cand(kind="f", comp="loraham-chat", name="call", key="file_call")], "dead")
    assert migrated == 0, migrated
    assert svc._stored_param_value("chat", "file", "loraham-chat", "call") == "XX0XXA"


def test_required_post_start_runs_under_the_documented_cap(tmp_path):
    """AUDIT-FOUND, twice. First: a required post-start ran inside a fixed 300 s job budget while
    MeshCom declares a ~13-minute cold-boot retry window, so the window could never complete.
    Then: the per-manifest calculator that replaced it was a SECOND model of the executor's
    timing and disagreed with it. There is now ONE documented outer cap; the inner executor is
    already bounded (finite retries, bounded sockets, a 120 s exec cap). This checks the cap is
    what `run_required_post_start` actually defaults to, and that it comfortably exceeds every
    declared required sequence."""
    import inspect
    from lhpc.core.lifecycle import Lifecycle, REQUIRED_POST_TIMEOUT_S
    assert not hasattr(Lifecycle, "required_post_budget"), "the duplicate timing model is gone"
    sig = inspect.signature(Lifecycle.run_required_post_start)
    assert sig.parameters["timeout"].default == REQUIRED_POST_TIMEOUT_S
    svc = _svc(tmp_path)
    checked = 0
    for st in svc.stacks():
        for c in st.components:
            if not any(step.get("required") for step in (c.post_steps or ())):
                continue
            declared = sum(float(step.get("seconds", 0)) for step in c.post_steps
                           if step.get("kind") == "delay")
            declared += sum(float(n) * float(i) for step in c.post_steps
                            for n, i in (step.get("schedule") or []))
            assert REQUIRED_POST_TIMEOUT_S > declared, (st.id, c.id, declared)
            checked += 1
    assert checked >= 2, checked

def test_tx_refuses_a_legacy_invalid_local_identity(tmp_path, monkeypatch):
    """AUDIT-FOUND: `effective_identity` returns "" BOTH when no local value exists and when an
    explicit one exists but the current rules reject it. TX treated those alike and fell back to
    the global, so a chat stack pinned to a 0.2.5-era `XX0XXA-99` transmitted as `XX0XXA` — a
    different on-air identity than the operator configured. The precedence contract is
    explicit local > global, and an INVALID explicit local refuses."""
    from lhpc.core import config as cfgmod
    from lhpc.core.lifecycle import Lifecycle, TxTestResult

    class _Ready:
        ready = True
    sent = []
    monkeypatch.setattr(Lifecycle, "run_daemon_tx_test",
                        lambda self, band, payload: (sent.append((band, payload)),
                                                     TxTestResult(True, band, 0, 1, "stub"))[1])
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "daemon_view", lambda self, b: _Ready())
    cfgmod.save_operator_config(svc._paths, "XX0XXA")
    svc._invalidate_config()
    # a value 0.2.5 accepted that the current APRS rule rejects, persisted directly
    cfgmod.save_stack_config(svc._paths, "chat", {"file_call": "XX0XXA-99"}, "")
    svc._invalidate_config()
    assert svc.effective_identity("chat", "433") == ""          # invalid -> no effective value
    assert not svc.test("chat", tx=True, apply=False).ok
    assert not svc.test("chat", tx=True, apply=True).ok
    assert sent == [], sent                                     # never transmitted as XX0XXA



# ---- 0.2.9: the identity is SAVED configuration; the plan and the apply judge it alike ----------

def _web(svc, monkeypatch):
    import re
    from lhpc.adapters.web.app import create_app
    monkeypatch.setattr(type(svc), "is_installed", lambda self, t: True)
    monkeypatch.setattr(type(svc), "unbuilt_components", lambda self, t: [])
    monkeypatch.setattr(type(svc), "missing_system_deps", lambda self, t: [])
    client = create_app(lambda: svc).test_client()
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/stacks").get_data(as_text=True)).group(1)
    return client, tok


def test_web_refusal_names_every_missing_field_on_the_settings_row(tmp_path, monkeypatch):
    # Meshtastic needs BOTH node names: the plan refuses before any start, and the operator lands
    # on the Settings section with both rows marked (`?bad=` carries every field).
    from lhpc.core.services import ActionResult
    svc = _svc(tmp_path)
    started = []
    real = type(svc).run_action
    def spy(self, op, target, apply=False, **k):
        if apply:
            started.append(target)
            return ActionResult(True, "started")
        return real(self, op, target, apply=apply, **k)
    monkeypatch.setattr(type(svc), "run_action", spy)
    client, tok = _web(svc, monkeypatch)
    resp = client.post("/action", data={"_csrf": tok, "op": "start", "target": "meshtastic"})
    assert resp.status_code == 302 and started == []            # refused BEFORE any start
    loc = resp.location
    assert "cfg=meshtastic" in loc and "bad=c_node_name,c_node_short" in loc
    page = client.get(loc.split("#")[0]).get_data(as_text=True)
    assert page.count("field-bad") == 2                          # both name rows marked


def test_settings_rows_show_an_inherited_identity_as_inherited_not_saved(tmp_path):
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    rows = [r for g in svc.config_param_groups("chat") for r in g["rows"] if r["is_identity"]]
    assert rows and rows[0]["value"] == ""                       # never prefilled with the global
    assert "inherits global XX0XXA" in rows[0]["identity_hint"]
    assert "inherits the global operator callsign" in rows[0]["identity_note"]


def test_an_inherited_identity_is_the_only_launch_time_overlay(tmp_path):
    # Enforcement approved via the global; the argv/config build must carry the EFFECTIVE identity
    # (materialized for this launch, never persisted) — and nothing else.
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    run_values, file_values = svc._materialize_inherited_identity("igate", "")
    assert run_values == {"call": "XX0XXA"} and file_values == {}
    assert svc._stored_param_value("igate", "run", "loraham-igate", "call") == ""  # not persisted
    # a saved local override needs no overlay
    assert svc.save_config_bundle("igate", values={"call": "XX0XXA-5"}).ok
    assert svc._materialize_inherited_identity("igate", "") == ({}, {})
    assert svc.identity_resolution("igate")[0]["effective"] == "XX0XXA-5"


def test_the_launch_carries_the_inherited_callsign(tmp_path, monkeypatch):
    # The four invariants, #1: licensed stack, empty local field, valid global -> the process
    # is launched WITH the inherited callsign.
    from lhpc.core.lifecycle import Lifecycle, StartLaunch
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    (tmp_path / "src" / "LoRaHAM_Daemon" / "loraham_igate").write_text("#bin")
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    seen = {}
    def stub(self, stack, comp, cfg, band="", **_scope):
        seen[comp.id] = dict(cfg)
        return StartLaunch(True, "log", "")
    monkeypatch.setattr(Lifecycle, "start", stub)
    monkeypatch.setattr(type(svc), "_ensure_daemon", lambda self, *a, **k: ([], True))
    monkeypatch.setattr(type(svc), "_run_post_start",
                        lambda self, *a, **k: (None, ""))
    res = svc.start("igate", apply=True)
    assert seen.get("loraham-igate", {}).get("call") == "XX0XXA", (res.summary, seen)
    assert svc._stored_param_value("igate", "run", "loraham-igate", "call") == ""


def test_an_unlicensed_identity_never_inherits(tmp_path):
    # Invariant #2: Meshtastic/MeshCore local identity never falls back to the global callsign —
    # the plan refuses with the exact field, for the CLI and the web alike.
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    for target, fields in (("meshtastic", ["c_node_name", "c_node_short"]),
                           ("meshcore", ["f_node_name"])):
        plan = svc.run_action("start", target, apply=False)
        assert not plan.ok and "never used here" in plan.summary, plan.summary
        assert plan.data.get("enforce_fields") == fields, plan.data
        assert any(c.startswith(f"lhpc config {target}") for c in plan.next_commands)


def test_a_missing_identity_refuses_on_plan_and_apply_before_any_mutation(tmp_path, monkeypatch):
    # Invariant #3: CLI/web plan truth is identical to the apply, and a refused apply has touched
    # nothing — no owner stop, no config generation, no launch.
    svc = _svc(tmp_path)
    touched = []
    for name in ("write_config_files", "_ensure_daemon", "stop"):
        monkeypatch.setattr(type(svc), name,
                            lambda self, *a, _n=name, **k: touched.append(_n) or [])
    from lhpc.core.lifecycle import Lifecycle
    monkeypatch.setattr(Lifecycle, "start", lambda self, *a, **k: touched.append("launch"))
    for apply in (False, True):
        r = svc.start("chat", apply=apply)
        assert not r.ok and "callsign is required" in r.summary
        assert r.data.get("enforce_fields") == ["f_call"]
        assert any(c.startswith("lhpc config chat") for c in r.next_commands)   # invariant #4
    assert touched == []
    plan = svc.restart("chat", apply=False)
    assert not plan.ok and "Cannot restart 'chat'" in plan.summary


def test_a_stored_placeholder_identity_is_refused_by_the_plan(tmp_path):
    # A retired generic default (LoRaHAM Pi, ...) is not an identity: hand-written into the store,
    # it is refused before anything runs, naming the field.
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_stack_config(svc._paths, "meshtastic",
                             {"node_name": "LoRaHAM Pi", "node_short": "SHCK"}, "")
    svc._invalidate_config()
    plan = svc.start("meshtastic", apply=False)
    assert not plan.ok and "c_node_name" in plan.data.get("enforce_fields", [])
    assert "'node_name' is required" in plan.summary


def test_web_start_judges_the_active_band(tmp_path, monkeypatch):
    # A bandless dashboard POST is judged on the band the operation will run on: voice running on
    # 868 with a 433-only identity is refused and the Settings row is marked.
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "running_band",
                        lambda self, sid, default="": "868" if sid == "voice" else default)
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/P"},
                                  band="433").ok                # 433-only identity
    client, tok = _web(svc, monkeypatch)
    resp = client.post("/action", data={"_csrf": tok, "op": "start", "target": "voice"})
    assert resp.status_code == 302 and "bad=f_callsign" in resp.location
    assert "band=868" in resp.location                     # the refused store, not the primary
    assert svc.identity_resolution("voice", "433")[0]["source"] == "local"
    assert svc.identity_resolution("voice", "868")[0]["source"] == "missing"


def test_web_restart_identity_refusal_happens_before_any_stop(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    stops = []
    monkeypatch.setattr(type(svc), "stop", lambda self, *a, **kw: stops.append(1), raising=False)
    client, tok = _web(svc, monkeypatch)
    resp = client.post("/action", data={"_csrf": tok, "op": "restart", "target": "graywolf"})
    assert resp.status_code == 302 and "bad=c_call" in resp.location
    assert stops == []                                         # refused BEFORE any stop


def test_web_bandless_restart_is_one_band_end_to_end(tmp_path, monkeypatch):
    # A bandless Web restart of a stack running on 868 runs on 868 — the 433 store is untouched.
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/868",
                                                   "file_sf": "11"}, band="868").ok
    assert svc.save_config_bundle("voice", values={"file_callsign": "XX0XXA/433",
                                                   "file_sf": "7"}, band="433").ok
    b433 = svc._paths.runtime_root / "config" / "stacks" / "voice@433.toml"
    before = b433.read_bytes()
    monkeypatch.setattr(type(svc), "running_band", lambda self, sid, d="": "868")
    seen = {}
    real_impl = type(svc)._start_impl
    def impl(self, target, **kw):
        seen["launch"] = kw.get("band")
        return real_impl(self, target, **kw)
    monkeypatch.setattr(type(svc), "_start_impl", impl)
    client, tok = _web(svc, monkeypatch)
    resp = client.post("/action", data={"_csrf": tok, "op": "restart", "target": "voice"})
    assert resp.status_code == 302                                # runs directly
    assert seen.get("launch") == "868", seen
    assert b433.read_bytes() == before                            # 433 byte-identical


def test_a_stale_confirmed_band_is_refused_never_remapped(tmp_path, monkeypatch):
    # AUDIT RULE (review-found again): a CONFIRMED form's band is honoured or refused, never
    # silently re-resolved — its consequences (owners to stop) were computed on that band. The box
    # narrowed to 868 under a form that says 433: nothing runs; the operator plans again.
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    started = []
    monkeypatch.setattr(type(svc), "spawn_start_job",
                        lambda self, op, target, band="", stop_owners=False, cascade=False:
                        started.append(band) or (f"web-{op}-{target}.log", "admitted", ""))
    client, tok = _web(svc, monkeypatch)
    monkeypatch.setattr(type(svc), "stack_bands", lambda self, t: ("868",))
    resp = client.post("/action", data={"_csrf": tok, "op": "start", "target": "voice",
                                        "band": "433", "confirmed": "yes"})
    assert resp.status_code == 302 and started == []
    page = client.get("/stacks").get_data(as_text=True)
    assert "now runs it on 868" in page and "confirm again" in page
    # RE-AUDIT: the target MOVED while both bands stay served — an explicit band outranks the
    # running-band marker in operation_band, so the live band is checked on its own here.
    monkeypatch.setattr(type(svc), "stack_bands", lambda self, t: ("433", "868"))
    monkeypatch.setattr(type(svc), "running_band", lambda self, sid, d="": "868")
    resp = client.post("/action", data={"_csrf": tok, "op": "restart", "target": "voice",
                                        "band": "433", "confirmed": "yes", "cascade": "yes"})
    assert resp.status_code == 302 and started == []
    assert "now runs it on 868" in client.get("/stacks").get_data(as_text=True)
    # a STOPPED target may still be started on an explicitly chosen non-primary band
    monkeypatch.setattr(type(svc), "running_band", lambda self, sid, d="": d)
    resp = client.post("/action", data={"_csrf": tok, "op": "start", "target": "voice",
                                        "band": "868", "confirmed": "yes"})
    assert resp.status_code == 302 and started == ["868"]


def test_a_config_write_during_a_launch_is_reported_not_locked_out(tmp_path, monkeypatch):
    """SIMPLIFICATION (operator ruling): the write succeeds, and the live consumer gets a
    restart-required marker telling the operator the running process no longer matches the saved
    configuration."""
    svc = _svc(tmp_path)
    svc.set_operator_identity(callsign="XX0XXA")
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA-7"}).ok
    live = {"v": False}
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: live["v"])
    real_impl = type(svc)._start_impl
    seen = {}
    def spy(self, t, **kw):
        live["v"] = True                      # the stack is live from here on
        seen["at_launch"] = svc._stored_param_value("chat", "file", "loraham-chat", "call")
        return real_impl(self, t, **kw)
    monkeypatch.setattr(type(svc), "_start_impl", spy)
    svc.start("chat", apply=True)
    assert seen["at_launch"] == "XX0XXA-7"     # the launch used the saved identity
    # a later write is ACCEPTED (never blocked) and the live consumer is warned
    assert svc.save_config_bundle("chat", values={"file_call": "XX0XXA-9"}).ok
    m = svc.restart_required("chat")
    assert m is not None and "call" in m["params"], m


def test_a_legacy_global_refusal_says_why_not_that_none_exists(tmp_path):
    """REVIEW-FOUND: with a legacy SSID-bearing global the refusal said "there is no global
    operator callsign to inherit" while the Stacks card showed one set with a legacy warning."""
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_operator_config(svc._paths, "XX0XXA-12")      # legacy shape, not inheritable
    svc._invalidate_config()
    assert svc.operator_callsign_legacy() is True
    r = svc.start("chat", apply=False)
    assert not r.ok
    assert "there is no global operator callsign" not in r.summary, r.summary
    assert "XX0XXA-12" in r.summary, r.summary                # names the actual obstacle
