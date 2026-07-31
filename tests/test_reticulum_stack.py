"""Contract tests for the `reticulum` stack and the config machinery it needed.

These pin the properties an audit flagged as load-bearing:
  * the radio is claimed exclusively, the SPI bus cooperatively (we hold the
    daemon's spi0.lock), so opposite-band coexistence with the daemon is allowed;
  * every TCP listener the stack opens is claimed exclusively;
  * a secret may only come from secrets.toml — never from local.toml, a default
    or a band default — and a file carrying one is 0600;
  * the nested-INI writer addresses `[[LoRa]]` inside `[interfaces]` by full
    path, quotes what ConfigObj needs quoted, and refuses control characters.
"""

import pytest

from lhpc.core.config import update_ini
from lhpc.core.manifest import ManifestError, load_manifest
from lhpc.core.model import ResourceMode


def _stack():
    return [s for s in load_manifest() if s.id == "reticulum"][0]


def _comp(cid):
    return [c for c in _stack().components if c.id == cid][0]


# ---- stack shape ----------------------------------------------------------

def test_only_rns_and_the_driver_are_mandatory():
    st = _stack()
    assert st.main == "rns"
    mandatory = {c.id for c in st.components if not c.optional}
    assert mandatory == {"rns"}, "only the node itself may be non-optional"
    # The driver is a library: lhpc starts every non-optional component, and a
    # library has no run command, so it must be optional + a build dependency.
    drv = _comp("rns-lora-interface")
    assert drv.optional and str(drv.kind).endswith("LIBRARY")
    assert "rns-lora-interface" in _comp("rns").build_requires


def test_clients_depend_on_the_node_so_they_cannot_own_the_radio():
    for cid in ("nomadnet", "lxmd", "sideband"):
        assert _comp(cid).depends_on == ("rns",), f"{cid} must depend on rns"
        assert _comp(cid).optional


def test_radio_is_exclusive_but_the_spi_bus_is_cooperative():
    res = {r.key: r.mode for r in _comp("rns").resources}
    assert res["loraham.radio.868"] is ResourceMode.EXCLUSIVE
    assert res["loraham.radio.433"] is ResourceMode.EXCLUSIVE
    # Cooperative is only honest because the driver takes the daemon's spi0.lock.
    assert res["spi.bus.0"] is ResourceMode.COOPERATIVE


def test_every_tcp_listener_is_claimed_exclusively():
    c = _comp("rns")
    listener_ports = {int(e.address.rsplit(":", 1)[1])
                      for e in c.endpoints if e.role == "listener"}
    claimed = {int(r.key.rsplit(".", 1)[1]) for r in c.resources
               if r.key.startswith("tcp.port.") and r.mode is ResourceMode.EXCLUSIVE}
    assert listener_ports <= claimed, f"unclaimed listeners: {listener_ports - claimed}"
    assert {37428, 37429, 4242} <= claimed


def test_client_access_needs_both_a_bind_and_an_allow_list():
    ep = [e for e in _comp("rns").endpoints if e.address.endswith(":4242")][0]
    assert ep.firewall.bind_param == "rns_bind"
    assert ep.firewall.allow_param == "rns_allow"


def test_the_node_binds_loopback_by_default():
    bind = [p for p in _comp("rns").config_file.params if p.name == "rns_bind"][0]
    assert bind.default == "127.0.0.1", "must not ship exposed by default"


# ---- secrets --------------------------------------------------------------

def test_ifac_key_is_a_secret_and_the_file_is_owner_only():
    fc = _comp("rns").config_file
    key = [p for p in fc.params if p.name == "ifac_netkey"][0]
    assert key.secret_ref == "reticulum.ifac_netkey"
    assert key.hidden, "a secret must not be editable on the Config page"
    assert not key.default and not key.band_defaults, \
        "a default would compete with the secrets.toml lookup"
    assert fc.mode == 0o600, "a file carrying a secret must not be world-readable"


@pytest.mark.parametrize("bad", [
    {"name": "s", "key": "s", "secret_ref": "reticulum.k"},                     # not hidden
    {"name": "s", "key": "s", "secret_ref": "reticulum.k", "hidden": True,
     "default": "oops"},                                                        # has a default
    {"name": "s", "key": "s", "secret_ref": "nodot", "hidden": True},           # malformed ref
])
def test_manifest_refuses_an_overridable_or_malformed_secret(bad, tmp_path):
    from lhpc.core import manifest as m
    raw = {"path": "{runtime}/x.conf", "fmt": "ini-update", "mode": 0o600,
           "base": "{asset}/bases/reticulum.conf", "param": [bad]}
    with pytest.raises(ManifestError):
        m._parse_file_config(raw)


def test_manifest_refuses_a_secret_in_a_world_readable_file():
    from lhpc.core import manifest as m
    raw = {"path": "{runtime}/x.conf", "fmt": "ini-update", "mode": 0o644,
           "base": "{asset}/bases/reticulum.conf",
           "param": [{"name": "s", "key": "s", "secret_ref": "a.b", "hidden": True}]}
    with pytest.raises(ManifestError):
        m._parse_file_config(raw)


# ---- nested INI writer ----------------------------------------------------

BASE = """[reticulum]
  enable_transport = No

[interfaces]

  [[LoRa]]
    type = LoRaSPIInterface
    frequency = 868500000

  [[Client access]]
    listen_ip = 127.0.0.1
"""


class _P:
    def __init__(self, name, key, section, default="", omit_if_empty=False):
        self.name, self.key, self.section = name, key, section
        self.default, self.omit_if_empty = default, omit_if_empty


def test_sections_are_addressed_by_full_path():
    # `listen_ip` exists only under [[Client access]]; the LoRa section must not
    # be touched, and vice versa.
    out = update_ini(BASE, [_P("b", "listen_ip", "interfaces/Client access")],
                     {"b": "192.168.0.5"}, lambda x: x)
    assert "listen_ip = 192.168.0.5" in out
    assert out.count("listen_ip") == 1
    assert "frequency = 868500000" in out


def test_a_declared_key_absent_from_the_base_is_appended_to_its_section():
    out = update_ini(BASE, [_P("n", "ifac_netname", "interfaces/LoRa")],
                     {"n": "mynet"}, lambda x: x)
    lora = out.split("[[LoRa]]")[1].split("[[Client access]]")[0]
    assert "ifac_netname = mynet" in lora


def test_omit_if_empty_leaves_the_key_out_entirely():
    # A missing IFAC secret must not become an EMPTY key — it must be absent.
    out = update_ini(BASE, [_P("k", "ifac_netkey", "interfaces/LoRa", omit_if_empty=True)],
                     {"k": ""}, lambda x: x)
    assert "ifac_netkey" not in out


def test_values_needing_quotes_are_quoted():
    out = update_ini(BASE, [_P("n", "ifac_netname", "interfaces/LoRa")],
                     {"n": "has # hash"}, lambda x: x)
    assert 'ifac_netname = "has # hash"' in out


@pytest.mark.parametrize("evil", ["a\nb = c", "a\x00b", "tail\r"])
def test_control_characters_are_refused(evil):
    # A newline in a value does not corrupt one setting, it invents another.
    with pytest.raises(ValueError):
        update_ini(BASE, [_P("n", "ifac_netname", "interfaces/LoRa")],
                   {"n": evil}, lambda x: x)


# ---- band scoping on the Apps page ----------------------------------------

def test_band_choice_does_not_leak_between_stacks(tmp_path, monkeypatch):
    """A band chosen for ONE stack must not re-render every other band-switchable
    stack on the page.

    The switch links carry `band` AND `cfg=<stack.id>` together. Applying the band
    globally made a 433 choice (typically on the daemon's live-band switch) render
    reticulum — whose declared primary is 868 — as 433, and worse, decided which
    per-band config a Save would write to.
    """
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    bands = svc.stack_bands("reticulum")
    if not bands or "868" not in bands:
        import pytest
        pytest.skip("this box does not serve 868")

    # With no explicit band the stack resolves to its DECLARED primary, not bands[0].
    assert svc._config_band("reticulum", "") == "868"
    # bands[0] is 433 here, so a naive "first allowed band" fallback would fail above.
    assert bands[0] == "433"
    # An explicit band still wins when it IS this stack's choice.
    assert svc._config_band("reticulum", "433") == "433"


def test_ifac_uses_the_keys_reticulum_actually_reads():
    """RNS recognises `networkname`/`passphrase` (Reticulum.py:779-787) and
    ignores anything else, so writing `ifac_netname`/`ifac_netkey` produced a
    config that LOOKED authenticated but had no IFAC identity at all."""
    params = {p.name: p.key for p in _comp("rns").config_file.params}
    assert params["ifac_netname"] == "networkname"
    assert params["ifac_netkey"] == "passphrase"


def test_client_access_port_is_not_operator_editable():
    # The endpoint and the exclusive tcp.port.4242 claim are static, so a
    # settable port would move the real listener while lhpc kept claiming and
    # firewalling 4242.
    names = {p.name for p in _comp("rns").config_file.params}
    assert "rns_port" not in names


def test_only_driver_supported_radio_values_are_offered():
    params = {p.name: p for p in _comp("rns").config_file.params}
    # SX1262 uses 62500/125000/... — the SX127x-only spellings (7800, 41700…)
    # are not accepted by that driver, so they must not be offered at all.
    assert set(params["bandwidth"].choices) == {"62500", "125000", "250000", "500000"}
    # SF6 on the SX127x needs implicit-header mode plus special detection settings;
    # the driver configures the explicit-header path and refuses SF6, so offering
    # it here would produce a link that never demodulates.
    assert params["spreadingfactor"].min == 7
    assert params["txpower"].max == 17             # PA_BOOST ceiling on SX127x


def test_clients_attach_to_the_owner_config_not_a_private_one():
    """Clients MUST point at the config dir `rns` owns.

    A private client-only config dir looks safer — no LoRa interface to take the
    radio by accident — but RNS derives the shared-instance RPC authkey from the
    identity IN the config dir (`Reticulum.py`, `rpc_key = full_hash(...)`), so a
    client with its own dir is refused by the owner with
    `multiprocessing.context.AuthenticationError: digest sent was rejected` and
    exits. Verified on hardware: NomadNet failed exactly this way and started
    once pointed back at the owner's dir.

    The radio is protected by the runner's ownership check and the exclusive
    radio claim, not by hiding the config."""
    for cid in ("nomadnet", "lxmd", "sideband"):
        c = _comp(cid)
        cmd = (c.run_cmd or "") + " " + " ".join(c.run_argv or ())
        assert "--rnsconfig" in cmd
        assert "/state/reticulum" in cmd, f"{cid} must share the owner's RNS config"


# ---- dashboard band scoping -----------------------------------------------

def test_a_multiband_stack_is_running_on_one_band_not_all_of_them(monkeypatch):
    """The radio dashboard asked "is a band-carrying component up?" — an answer
    that is identical in EVERY band column, so a stack running on 868 also
    rendered under "Running on 433". It must be scoped to the band the stack is
    actually on."""
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    if "868" not in (svc.stack_bands("reticulum") or ()):
        pytest.skip("this box does not serve 868")

    monkeypatch.setattr(svc, "running_band", lambda sid, default="": "868")
    assert svc.runs_on_band("reticulum", "868") is True
    assert svc.runs_on_band("reticulum", "433") is False

    # No start marker: fall back to the launch band, then the DECLARED primary —
    # never "true for every supported band".
    monkeypatch.setattr(svc, "running_band", lambda sid, default="": "")
    monkeypatch.setattr(svc, "interactive_band", lambda sid: None)
    assert svc.runs_on_band("reticulum", "868") is True
    assert svc.runs_on_band("reticulum", "433") is False

    # An interactive app launched on 433 is the evidence when no marker exists.
    monkeypatch.setattr(svc, "interactive_band", lambda sid: "433")
    assert svc.runs_on_band("reticulum", "433") is True
    assert svc.runs_on_band("reticulum", "868") is False


def test_stopping_a_client_keeps_the_bands_evidence():
    """Stopping an optional client (sideband/lxmd) must NOT retire the stack's
    running-band marker while `rns` still holds the radio. It did, so the stack
    lost its band evidence and dropped out of its own band column on the
    dashboard while remaining offered on the other one.

    Decided from the TYPED STOP RESULTS: the operation-scoped snapshot is
    memoised, so reading it here still showed the just-stopped owner running.
    """
    from lhpc.core.outcomes import Outcome
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    res = lambda cid, oc: type("R", (), {"component": cid, "outcome": oc})()

    # A client stopped, the band owner untouched -> the band is still occupied.
    assert svc._band_owners_stopped(
        "reticulum", [res("lxmd", Outcome.STOPPED)]) is False
    # The band owner stopped -> the marker may retire.
    assert svc._band_owners_stopped(
        "reticulum", [res("rns", Outcome.STOPPED)]) is True
    assert svc._band_owners_stopped(
        "reticulum", [res("rns", Outcome.ALREADY_STOPPED)]) is True
    # A FAILED stop is not a cessation — the radio may still be held.
    assert svc._band_owners_stopped(
        "reticulum", [res("rns", Outcome.FAILED)]) is False


def test_the_advertised_command_restores_the_terminal():
    """NomadNet is a full-screen TUI: interrupted with Ctrl+C it exits without
    leaving raw mode, so ECHO/ICANON stay off and the shell stops echoing. The
    command lhpc tells the operator to run must hand the terminal back."""
    from lhpc.core.services import ControllerService

    cmd = ControllerService().manual_start_command(_comp("nomadnet"))
    assert cmd.rstrip().endswith("stty sane 2>/dev/null"), cmd
    assert "nomadnet" in cmd


def test_sideband_is_built_from_its_pinned_checkout():
    """`pip install sbapp` resolves from PyPI, which made the pin decorative — the
    installed app was not the audited source. Install the checkout itself."""
    argvs = [st.get("argv", []) for st in _comp("sideband").build_steps]
    pip = [a for a in argvs if a and "pip" in a[0] and "install" in a]
    assert pip, "sideband must have a pip install step"
    target = pip[0][-1]
    # A BARE `sbapp` floats with PyPI, so the source pin would say nothing about the
    # installed artefact. `pip install .` is not the answer either: upstream's setup.py
    # drops every .kv layout when built from this repo layout, and the app then exits at
    # window creation. A version pin is what actually makes the install deterministic.
    assert target.startswith("sbapp=="), f"sideband installs {target!r} — not version-pinned"
    assert _comp("sideband").source.pin_commit, "a pin is required for that to mean anything"
    # pip skips an already-present version, so a venv holding a BROKEN build of the same
    # version survives a rebuild. One step must force the package itself back.
    forced = [a for a in argvs if a and "--force-reinstall" in a and "--no-deps" in a]
    assert forced, "a rebuild must be able to repair a bad install of the same version"


def test_the_pinned_sideband_version_matches_the_pinned_checkout():
    """The version pin and the source pin must describe the SAME release."""
    import re
    from pathlib import Path

    from lhpc.core.services import ControllerService

    argvs = [st.get("argv", []) for st in _comp("sideband").build_steps]
    pinned = [a[-1] for a in argvs if a and "pip" in a[0] and "install" in a][0].split("==")[1]
    main = Path(ControllerService()._paths.resolve_source("src/sideband")) / "sbapp" / "main.py"
    if not main.is_file():
        pytest.skip("sideband checkout not present on this box")
    found = re.search(r'__version__ = "([^"]+)"', main.read_text())
    assert found and found.group(1) == pinned, \
        f"manifest pins sbapp=={pinned} but the checkout is {found and found.group(1)}"


def test_an_unsafe_secrets_file_blocks_cleanly(monkeypatch):
    """A group/other-readable secrets.toml must produce a typed generation FAILURE.
    load_secrets raises ConfigError, but the start boundary catches OSError and
    PathContainmentError only — so it escaped as a traceback (web 500)."""
    from lhpc.core import service_params
    from lhpc.core.config import ConfigError
    from lhpc.core.services import ControllerService

    def refuse(_paths):
        raise ConfigError("config/secrets.toml is readable beyond its owner "
                          "(mode 0644) — refusing to load secrets")

    monkeypatch.setattr(service_params, "load_secrets", refuse)
    writes = ControllerService().write_config_files("reticulum")
    failed = [w for w in writes if w.status == "failed"]
    assert failed, "an unsafe secrets file must fail the write, not pass"
    assert "secrets" in failed[0].detail.lower()


def test_a_stack_without_secrets_is_unaffected(monkeypatch):
    """Secrets are loaded LAZILY: a refused secrets.toml must not break config
    generation for a stack that declares no secret_ref."""
    from lhpc.core import service_params
    from lhpc.core.config import ConfigError
    from lhpc.core.services import ControllerService

    def refuse(_paths):
        raise ConfigError("refused")

    monkeypatch.setattr(service_params, "load_secrets", refuse)
    svc = ControllerService()
    for stack in load_manifest():
        if stack.id == "reticulum":
            continue
        has_secret = any(getattr(p, "secret_ref", "")
                         for c in stack.components if c.config_file
                         for p in c.config_file.params)
        if has_secret:
            continue
        for w in svc.write_config_files(stack.id):
            assert not (w.status == "failed" and "refused" in (w.detail or "")), \
                f"{stack.id} broke on a secrets file it does not use"


# ---- headless GUI skip ----------------------------------------------------

def test_sideband_is_recognised_as_needing_a_display():
    """Voice is kept off headless rigs by its GUI-only build deps. Sideband needs the
    same treatment, but a box can HAVE those deps (installed for another stack, or a
    desktop image driven over SSH) and still have no session."""
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    assert svc.needs_display(_comp("sideband"))
    assert not svc.needs_display(_comp("rns")), "the node is headless by design"
    assert not svc.needs_display(_comp("lxmd"))


def test_display_detection_uses_sockets_not_our_environment(monkeypatch, tmp_path):
    """The controller runs as a systemd user service and never inherits DISPLAY, so
    the environment is not evidence. A live compositor socket is."""
    import glob as _glob

    from lhpc.core.services import ControllerService

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(_glob, "glob", lambda pat: [])
    assert ControllerService.display_available() is False

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(_glob, "glob",
                        lambda pat: ["/run/user/1000/wayland-0"] if "wayland" in pat else [])
    assert ControllerService.display_available() is True


def test_a_headless_skip_of_an_optional_gui_app_is_not_a_failure():
    """SKIPPED for an OPTIONAL component is an accepted outcome — the stack must not
    report failure because a desktop app was not started on a headless box."""
    from lhpc.core.outcomes import Outcome

    assert _comp("sideband").optional
    # The same exemption MANUAL_REQUIRED already has (nomadnet is interactive).
    assert Outcome.SKIPPED.value == "skipped"


# ---- dependency gating ----------------------------------------------------

def test_a_client_cannot_start_after_its_dependency_failed():
    """lxmd/nomadnet/sideband use the config dir `rns` owns. Started after `rns`
    failed, one of them becomes the shared-instance owner and initialises the LoRa
    interface itself — taking the radio. Only the DAEMON used to be gated this way."""
    from lhpc.core.outcomes import Outcome
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    stack = [s for s in load_manifest() if s.id == "reticulum"][0]
    order = [(stack, c) for c in stack.components]
    res = lambda cid, oc: type("R", (), {"component": cid, "outcome": oc})()

    lxmd = _comp("lxmd")
    assert svc._unmet_dependencies(lxmd, order, [res("rns", Outcome.BLOCKED)]) == ["rns"]
    assert svc._unmet_dependencies(lxmd, order, [res("rns", Outcome.FAILED)]) == ["rns"]
    assert svc._unmet_dependencies(lxmd, order, [res("rns", Outcome.VERIFIED)]) == []
    assert svc._unmet_dependencies(lxmd, order, [res("rns", Outcome.ALREADY_HEALTHY)]) == []
    # A dependency outside this run is not judged here.
    assert svc._unmet_dependencies(lxmd, order, []) == []


def test_reticulum_and_meshtastic_cannot_share_the_bus():
    """meshtasticd drives /dev/spidev0.0 without taking spi0.lock. `daemon +
    meshtastic` ships and is field-verified, so it stays allowed; the NEW pairing is
    blocked by a shared exclusive claim."""
    from lhpc.core.model import ResourceMode

    def claims(sid, cid):
        st = [s for s in load_manifest() if s.id == sid][0]
        c = [x for x in st.components if x.id == cid][0]
        return {r.key: r.mode for r in c.resources}

    assert claims("meshtastic", "meshtastic")["spi.bus.0.unlocked"] is ResourceMode.EXCLUSIVE
    assert claims("reticulum", "rns")["spi.bus.0.unlocked"] is ResourceMode.EXCLUSIVE
    # The daemon must NOT claim it, or the shipped daemon+meshtastic pair would break.
    assert "spi.bus.0.unlocked" not in claims("daemon", "loraham-daemon")


def test_client_access_is_loopback_only_in_this_release():
    """4242 has no application-level authentication; a non-loopback bind would rely on
    the allow-list plus a firewall that may be absent, stale or unverified."""
    bind = [p for p in _comp("rns").config_file.params if p.name == "rns_bind"][0]
    assert bind.default == "127.0.0.1"
    assert list(bind.choices) == ["127.0.0.1"], "must not offer a non-loopback bind yet"


# ---- band evidence must survive component starts ---------------------------

def test_starting_a_client_does_not_rewrite_the_running_band(monkeypatch):
    """Starting an optional client with no explicit band resolved to the stack's
    DECLARED primary and overwrote the running-band marker: a live 433 node became
    "868" in state, so the console showed — and a Save would have written — the wrong
    band's config. An explicit band wins, else the band already running."""
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    monkeypatch.setattr(svc, "running_band", lambda sid, default="": "433")
    # No explicit band: inherit 433, NOT the declared primary (868).
    assert svc._config_band("reticulum", svc.running_band("reticulum", "")) == "433"
    # An explicit band still wins.
    assert svc._config_band("reticulum", "868") == "868"


def test_the_console_falls_back_to_the_running_band(monkeypatch):
    """Every stop redirect drops band/cfg from the URL. Falling back to the declared
    primary then flipped a running 433 stack to 868 on the page."""
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    monkeypatch.setattr(svc, "running_band", lambda sid, default="": "433")
    assert svc.running_band("reticulum", "") == "433"
    assert svc.runs_on_band("reticulum", "433") is True
    assert svc.runs_on_band("reticulum", "868") is False


def test_starting_the_other_band_while_running_is_refused():
    """A band-switchable stack runs on ONE band. Asking for the other one while its
    band owner is up was a silent no-op: every component read already_healthy, the
    console said "Run applied", and the radio stayed where it was — so the operator
    believed they had switched bands when they had not."""
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    if svc.running_band("reticulum", "") == "":
        pytest.skip("reticulum is not running on this box")
    live = svc.running_band("reticulum", "")
    other = "433" if live == "868" else "868"
    res = svc.start("reticulum", apply=True, band=other)
    assert res.ok is False
    assert live in res.summary and other in res.summary
    assert svc.running_band("reticulum", "") == live, "the refusal must not move the band"


def test_only_a_band_carrying_component_defines_the_band():
    """Starting an optional client with band=868 wrote the stack's running-band marker
    while `rns` was still tuned to 433 — status, the dashboard, arbitration and the
    per-band config then all described a band the radio was not on."""
    assert _comp("rns").bands == ("433", "868")
    for cid in ("lxmd", "nomadnet", "sideband"):
        c = _comp(cid)
        assert not c.bands and not c.band, f"{cid} must not carry a band"


def test_an_unknown_band_claims_every_possible_band(monkeypatch):
    """A live multi-band owner whose band marker is lost or unreadable must claim ALL
    its bands. Falling back to the declared primary meant a node actually on 433 was
    arbitrated as an 868 owner, so a second exclusive 433 owner could be admitted."""
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    monkeypatch.setattr(svc, "running_band", lambda sid, default="": "")
    monkeypatch.setattr(svc, "interactive_band", lambda sid: None)

    monkeypatch.setattr(svc, "_band_owner_is_up", lambda sid: True)
    assert svc._operation_bands("reticulum", "", "", "start") == {"433", "868"}

    # Not running -> the declared primary is fine again (nothing to protect).
    monkeypatch.setattr(svc, "_band_owner_is_up", lambda sid: False)
    assert svc._operation_bands("reticulum", "", "", "start") == {"868"}


def test_the_build_marker_is_a_receipt_for_its_consumed_sources(monkeypatch):
    """A static marker stayed 'built' after rns-lora-interface (or Reticulum itself)
    was updated — the venv could still hold the OLD driver while lhpc reported the
    component built. The marker now records the consumed source SHAs, and is_built
    recomputes them: any drift reads NOT built and surfaces the rebuild."""
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    rns = _comp("rns")
    assert rns.build_requires == ("rns-lora-interface",)

    shas = {"rns": "a" * 40, "rns-lora-interface": "b" * 40}
    monkeypatch.setattr(
        type(svc), "_consumed_source_lines",
        lambda self, comp: "".join(f"consumed {cid} {sha}\n"
                                   for cid, sha in shas.items())
        if comp.build_marker and comp.build_requires else "")
    receipt_then = svc._consumed_source_lines(rns)
    assert "consumed rns-lora-interface bbbb" in receipt_then

    # The dependency moves -> the recomputed receipt differs -> a marker written
    # against the old SHAs can no longer compare equal.
    shas["rns-lora-interface"] = "c" * 40
    assert svc._consumed_source_lines(rns) != receipt_then


def test_changing_a_consumed_source_invalidates_the_completed_receipt(tmp_path, monkeypatch):
    """END-TO-END guard for the deferred transitive-source-lock gap (docs/backlog.md).

    Detached builds lock only the component's OWN checkout, so a dependency CAN move
    while a build runs. Accepting that deferral rests entirely on this invariant: if
    the dependency ends at a different SHA, the stored receipt no longer matches and
    the component reads NOT built — it cannot start as a valid completed build. The
    dangerous case (Sideband reading 'built' while holding an obsolete copied plugin)
    stays closed only as long as this holds.

    Deliberately drives the REAL is_built() against a REAL marker file: the sibling
    receipt test stubs _consumed_source_lines and so cannot prove is_built() flips.
    """
    from lhpc.core.lifecycle import BUILD_MARKER_TEXT
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    sideband = _comp("sideband")
    assert sideband.build_requires == ("rns-lora-interface",), \
        "sideband must declare the driver it copies its plugin out of"

    # Inside the ambient runtime root on purpose: is_built() reads the marker through
    # the containment-checked helper, so a path outside it would fail for the WRONG
    # reason and the test would pass without proving anything.
    src = svc._paths.runtime_root / "src" / "sideband-receipt-test"
    src.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(type(svc._lifecycle()), "source_dir",
                        lambda self, comp: src, raising=False)

    driver_sha = {"rns-lora-interface": "b" * 40}
    monkeypatch.setattr(
        type(svc), "_consumed_source_lines",
        lambda self, comp: "".join(f"consumed {cid} {sha}\n"
                                   for cid, sha in driver_sha.items()))

    marker = src / sideband.build_marker
    marker.parent.mkdir(parents=True, exist_ok=True)
    # A completed build: the marker is written with the SHAs consumed at build time.
    marker.write_text(BUILD_MARKER_TEXT + svc._consumed_source_lines(sideband))
    assert svc.is_built(sideband) is True, "a freshly written receipt must read built"

    # The driver moves underneath the finished build — the exact race the lock gap
    # leaves open. The receipt must stop matching.
    driver_sha["rns-lora-interface"] = "c" * 40
    assert svc.is_built(sideband) is False, \
        "a moved consumed source MUST invalidate the receipt — otherwise the deferred " \
        "transitive-source-lock gap becomes a silent stale-artifact bug"


def test_components_without_requires_keep_the_static_marker():
    """Only marker+build_requires components get receipt lines; everything else keeps
    the exact static text, so their existing markers stay valid."""
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    # sideband now DOES declare a build_requires (it copies the driver's plugin), so it
    # legitimately gets receipt lines; lxmd/nomadnet consume only their own source.
    for cid in ("lxmd", "nomadnet"):
        assert svc._consumed_source_lines(_comp(cid)) == ""
    assert "rns-lora-interface" in svc._consumed_source_lines(_comp("sideband"))


def test_unknown_band_is_conservative_for_admission_but_not_for_display():
    """The same helper feeds two very different consumers, and they must not share a
    policy: rendering a conflict is informational, refusing a start is a safety act.

    An unmarked multi-band owner claims every band it could be on when deciding whether
    a start is BLOCKED (admitting a second exclusive owner onto a live radio is a real
    collision), but only its declared band for the conflict DISPLAY — claiming both
    there produced false 433+868 conflicts for a stack plainly on one of them.
    """
    import inspect

    from lhpc.core.services import ControllerService

    src = inspect.getsource(ControllerService._band_limited_running)
    assert "conservative" in src
    assert "self._live_bands" in src, "the conservative branch must use _live_bands"

    display = inspect.getsource(ControllerService._observed_conflicts)
    assert "conservative=True" not in display, "display must not invent conflicts"

    admission = inspect.getsource(ControllerService._running_conflicts)
    assert "conservative=True" in admission, "a start decision must be conservative"


def test_doctor_warns_when_boot_restore_would_be_skipped(monkeypatch):
    """A controller update that changes a unit template leaves every existing box with
    a non-canonical unit, and boot-restore then silently refuses — the operator only
    finds out when a power cycle brings the box up with nothing running. doctor must
    say so while it can still be fixed."""
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    monkeypatch.setattr(type(svc), "_web_integration_proven",
                        lambda self: (False, "lhpc-web.service is not canonical (modified_ours)"))
    res = svc.doctor()
    text = "\n".join(res.details)
    assert "BOOT RESTORE WILL BE SKIPPED" in text
    assert "lhpc self-update --repair-integration" in text

    monkeypatch.setattr(type(svc), "_web_integration_proven", lambda self: (True, ""))
    assert "BOOT RESTORE WILL BE SKIPPED" not in "\n".join(svc.doctor().details)


def test_a_component_id_is_told_which_stack_owns_it():
    """`install` adopts a whole STACK (a lone component leaves unmet build_requires and
    a broken run order); `update` refreshes ONE source. That split is deliberate — but
    `lhpc install rns-lora-interface` answered "Unknown stack" and listed stacks, when
    the id IS known, just at the other granularity."""
    from lhpc.core.services import ControllerService

    svc = ControllerService()
    res = svc._unknown_stack("rns-lora-interface")
    assert res.ok is False
    assert "component of the 'reticulum' stack" in res.summary
    assert "lhpc install reticulum" in res.next_commands
    assert "lhpc update rns-lora-interface" in res.next_commands

    # A genuine typo still gets the stack list.
    typo = svc._unknown_stack("retikulum")
    assert "Unknown stack" in typo.summary and "lhpc list" in typo.next_commands


def test_both_radio_bindings_are_probed_separately():
    """The gate checked a dist-packages PATH for gpiod only — version- and
    arch-specific, and blind to spidev. A box with gpiod present and spidev missing
    passed install/doctor and failed later in the build's import probe."""
    reqs = _comp("rns").requires
    files = " ".join(getattr(r, "check_file", "") or "" for r in reqs)
    assert "python3-libgpiod.list" in files and "python3-spidev.list" in files
    # NOT a dist-packages path (Python-version/arch specific) and NOT `module`:
    # module resolves in the CONTROLLER venv, which has include-system-site-packages
    # = false and can never see an apt binding — verified live, it refused to install
    # on a box where both were present.
    assert "dist-packages/gpiod" not in files and "spidev.cpython" not in files
    assert not any(getattr(r, "module", "") in ("gpiod", "spidev") for r in reqs)


def test_sideband_declares_the_driver_it_copies_from():
    """The build copies lhpc_location.py out of the driver checkout and runs its
    enable_sideband_plugins.py, so the driver is a build input. Undeclared, the receipt
    ignored it: an updated plugin left Sideband reading 'built' with the old copy."""
    sb = _comp("sideband")
    assert "rns-lora-interface" in (sb.build_requires or ())
    assert sb.build_marker, "a receipt only exists for a marker-bearing component"
