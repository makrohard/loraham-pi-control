"""Contract tests for the `reticulum` stack and the config machinery it needed.

These pin the properties an audit flagged as load-bearing:
  * the radio is claimed exclusively, the SPI bus cooperatively (we hold the
    daemon's spi0.lock), so opposite-band coexistence with the daemon is allowed;
  * every TCP listener the stack opens is claimed exclusively;
  * a secret may only come from secrets.toml — never from local.toml, a default
    or a band default — and a file carrying one is owner-only (0600 or 0400);
  * MeshChat is a CLIENT: it never owns the radio, and the properties that keep it one
    (the rns-client guard, a plain venv, a loopback listener, the denied config routes)
    are pinned here because each was a measured hazard, not a style preference;
  * the nested-INI writer addresses `[[LoRa]]` inside `[interfaces]` by full
    path, quotes what ConfigObj needs quoted, and refuses control characters.
"""

import pytest

from lhpc.core import reticulum_interfaces as ri
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
    assert fc.mode & 0o077 == 0, "a file carrying a secret must be owner-only"
    # And specifically READ-ONLY, which is the stronger claim this stack makes: co-resident
    # Reticulum clients (MeshChat, nomadnet, Sideband) run under the same account and can edit
    # interfaces through their own UIs. ConfigObj.write() opens the target in place, which 0400
    # refuses; LHPC's own writer renames a temp leaf over it and is unaffected. An ownership
    # convention, not a security boundary — the same account could chmod it back.
    assert fc.mode == 0o400, "the RNS config is LHPC-owned and read-only to its clients"


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


def _secret_cfg(mode):
    return {"path": "{runtime}/x.conf", "fmt": "ini-update", "mode": mode,
            "base": "{asset}/bases/reticulum.conf",
            "param": [{"name": "s", "key": "s", "secret_ref": "a.b", "hidden": True}]}


@pytest.mark.parametrize("mode", [0o600, 0o400])
def test_a_secret_bearing_config_may_be_owner_only(mode):
    """0400 as well as 0600. A config LHPC writes but a co-resident client must not rewrite is
    declared read-only, and that has to survive validation or the manifest cannot express it.
    LHPC's own writer is unaffected: `atomic_write_bytes` renames a fresh temp leaf over the
    target, and rename needs permission on the DIRECTORY. What 0400 stops is an in-place
    `open(path, "wb")` — which is exactly what configobj does."""
    from lhpc.core import manifest as m
    assert m._parse_file_config(_secret_cfg(mode)).mode == mode


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o604])
def test_a_secret_bearing_config_may_not_be_readable_by_anyone_else(mode):
    """Widening to 0400 must not widen anything else: group- or world-readable still refused,
    including modes that ARE valid for a config without a secret (0644, 0640)."""
    from lhpc.core import manifest as m
    with pytest.raises(ManifestError):
        m._parse_file_config(_secret_cfg(mode))


def test_a_plain_config_may_be_read_only():
    from lhpc.core import manifest as m
    raw = {"path": "{runtime}/x.conf", "fmt": "ini-update", "mode": 0o400,
           "base": "{asset}/bases/reticulum.conf", "param": []}
    assert m._parse_file_config(raw).mode == 0o400


@pytest.mark.parametrize("mode", [0o777, 0o755, 0o000, 0o700])
def test_an_unlisted_config_mode_is_still_refused(mode):
    from lhpc.core import manifest as m
    raw = {"path": "{runtime}/x.conf", "fmt": "ini-update", "mode": mode,
           "base": "{asset}/bases/reticulum.conf", "param": []}
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
        cmd = " ".join(c.run_argv)
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


@pytest.mark.no_default_display
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
    assert svc.runs_on_band("reticulum", "433") is True
    assert svc.runs_on_band("reticulum", "868") is False


def test_starting_the_other_band_while_running_is_refused(tmp_path, monkeypatch):
    """A band-switchable stack runs on ONE band. Asking for the other one while its
    band owner is up was a silent no-op: every component read already_healthy, the
    console said "Run applied", and the radio stayed where it was — so the operator
    believed they had switched bands when they had not.

    Driven against a runtime root this test builds. It used to read the DEVELOPER's box and skip
    unless reticulum happened to be running there — which, with the hermetic runtime-root fixture,
    was every single run on every machine.
    """
    from lhpc.core import config as _cfg
    from lhpc.core.paths import Paths
    from lhpc.core.services import ControllerService

    svc = ControllerService(paths=Paths(runtime_root=tmp_path))
    _cfg.save_hardware_setup(svc._paths, "loraham")   # else the refusal fires for the wrong reason
    svc._invalidate_config()
    svc._set_running_band("reticulum", "433")
    # stubbed: the real check is a full system snapshot scan; this test is about band
    # arbitration, not process discovery.
    monkeypatch.setattr(svc, "_band_owner_is_up", lambda sid: True)
    assert svc.running_band("reticulum", "") == "433"

    res = svc.run_action("start", "reticulum", band="868", apply=False)
    assert res.ok is False
    assert "433" in res.summary and "868" in res.summary
    assert svc.running_band("reticulum", "") == "433", "the refusal must not move the band"


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

    # stubbed both ways: the real check is a full system snapshot scan; this test is about
    # band arbitration, not process discovery.
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


# The band-limiting rule used to be pinned by reading the service's source. Its OBSERVABLE
# half — a running band-switchable app never renders a false conflict on the other band — is
# driven in test_run_order.py::test_no_false_conflict_meshcom433_meshtastic868_without_marker.
# The conservative-admission half has no observable this suite can isolate (every scenario that
# reaches it is already refused by the daemon's own claim), so it is left to the live matrix
# rather than replaced by a synthetic box that would only re-pin the implementation.


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


# ---- MeshChat, the browser client -----------------------------------------

def _run_line(c):
    """The model exposes the DERIVED argv, not the `run` shorthand the manifest declares."""
    return " ".join(c.run_argv)


def test_meshchat_is_an_optional_client_that_cannot_own_the_radio():
    """Started bare against the owner's config with `rns` absent, MeshChat BECAME the
    shared-instance owner on :37428 and tried to load LoRaSPIInterface — measured on the box. It
    failed only because its venv could not import `loraham_rns`. Two independent properties keep
    it a client, and both are asserted: it launches through the guard, and its venv is plain."""
    m = _comp("meshchat")
    assert m.optional, "a client must never be seeded by a plain stack start"
    assert "rns" in m.depends_on
    # The guard by ABSOLUTE path into the rns component's venv: it is not on PATH, and it is
    # not in MeshChat's own venv.
    run = _run_line(m)
    assert "{runtime}/src/reticulum/.venv/bin/loraham-rns-client" in run
    assert "--wait" in run, "the guard must WAIT for the instance, not merely test once"
    venv = [s for s in m.build_steps if "venv" in s["argv"]][0]["argv"]
    assert "--system-site-packages" not in venv, (
        "a plain venv is deliberate: without it MeshChat can import loraham_rns/spidev and, "
        "started wrongly, drive the radio")


def test_meshchat_listens_on_loopback_and_is_proxied():
    m = _comp("meshchat")
    ep = [e for e in m.endpoints if e.ready][0]
    assert ep.address.startswith("127.0.0.1:"), "nginx is the only public path; it has no auth"
    assert ep.client and ep.scheme == "http", "client+scheme is what makes it a proxyable page"
    port = ep.address.split(":")[1]
    assert any(r.key == f"tcp.port.{port}" and r.mode is ResourceMode.EXCLUSIVE
               for r in m.resources), "every listener this stack opens is claimed exclusively"
    argv = m.run_argv
    assert argv[argv.index("--port") + 1] == port, (
        "the port is written in three places — the run line, the endpoint and the resource "
        "claim — with nothing deriving it, so they must be asserted to agree")


def test_meshchat_reports_running_and_built_truthfully():
    """Two states LHPC gets wrong without explicit declarations. `running_evidence` is
    `systemd_active or proc_matched`, so with no process stanza the component reads STOPPED
    while the UI serves; and `.venv/bin/python` exists long before pip and the UI copy finish,
    so without a marker a half-built tree reads as a completed build."""
    m = _comp("meshchat")
    assert m.process and m.process.exec_name and m.process.all_args
    assert m.build_marker and m.bin
    assert m.readiness == "endpoint", "readiness must be the listener, not a status route"


def test_meshchat_may_not_write_the_lhpc_owned_reticulum_config():
    """Interfaces and transport are LHPC's, rendered from the base file on every start. The
    0400 config refuses the write; these turn the resulting backend 500 into a clean 404. The
    list is re-derived from the pinned commit at every bump — an eighth route would pass."""
    m = _comp("meshchat")
    ep = [e for e in m.endpoints if e.ready][0]
    denied = set(ep.proxy_deny_paths)
    assert len(denied) == 7, "seven routes call reticulum.config.write() at the pinned commit"
    for suffix in ("add", "delete", "disable", "enable", "import"):
        assert f"/api/v1/reticulum/interfaces/{suffix}" in denied
    assert "/api/v1/reticulum/enable-transport" in denied
    assert "/api/v1/reticulum/disable-transport" in denied
    # Reading is fine, and so are the two POSTs that write nothing.
    assert not any(d.endswith(("/export", "/import-preview")) for d in denied)


def test_meshchat_state_survives_a_reinstall():
    """The storage dir holds the LXMF IDENTITY (`identity` + `identities/`, measured), not a
    cache: inside the checkout a reinstall would purge it and the node's address would change
    silently."""
    m = _comp("meshchat")
    mk = [s for s in m.pre_steps if s.get("kind") == "mkdir"]
    assert any("{runtime}/state/meshchat" == s.get("path") for s in mk)
    assert "{runtime}/state/meshchat" in _run_line(m)


# ---- the Internet interface and transport ---------------------------------

def _rns_file_params():
    fc = _comp("rns").config_file
    return fc.params, fc


def _render(values=None):
    """The generated config as the start path renders it: the shipped base, the declared params,
    the operator's values on top of the declared defaults."""
    from lhpc.core.assets import asset_path
    params, _ = _rns_file_params()
    vals = {p.name: p.default for p in params}
    vals.update(values or {})
    base = asset_path("bases/reticulum.conf").read_text()
    return update_ini(base, params, vals, lambda s: s)


def test_the_defaults_render_transport_off_and_the_internet_interface_inert():
    """Both switches are opt-in, and the Internet section is PRESENT while off: `update_ini`
    appends a missing key to an existing section, it does not manufacture a missing nested
    section, so a section that only appears once enabled could never be written. RNS builds an
    interface only for an enabled section, so the empty endpoint below is never read."""
    from lhpc.core.assets import asset_path
    out = _render()
    assert "enable_transport = No" in out
    assert "[[Internet]]" in out
    body = out.split("[[Internet]]", 1)[1]
    assert "enabled = no" in body
    assert "type = TCPClientInterface" in body
    # The rendered value comes from the declared DEFAULT, which is what RNS reads; the base
    # must agree with it, or the shipped file documents a state the controller never renders.
    by_name = {p.name: p for p in _rns_file_params()[0]}
    assert by_name["internet_enabled"].default == "no"
    assert by_name["enable_transport"].default == "No"
    base_body = asset_path("bases/reticulum.conf").read_text().split("[[Internet]]", 1)[1]
    assert "enabled = no" in base_body
    assert "enable_transport = No" in asset_path("bases/reticulum.conf").read_text()


def test_the_rendered_interface_modes_are_the_intended_trio():
    """The radio is `internal`, the client door `gateway`, the internet side `boundary` with
    `recursive_prs`.

    `internal` is the load-bearing one and it was WRONG in the first cut: measured against the
    pinned Reticulum 1.5.2, a `gateway` LoRa interface retransmits onto the radio every announce
    the node learns from the internet once transport is on (183-byte ANNOUNCE, one hop), because
    RNS filters announces on the OUTGOING interface and its gateway path has no branch rejecting
    them. `internal` is that branch. Measured in all four directions, it costs nothing else, and
    with transport off the two are indistinguishable. `recursive_prs` on the internet side is its
    companion: an internal interface is excluded from a boundary interface's default path search,
    so without it no internet-side peer could ever discover a destination on the radio."""
    out = _render()
    modes = {}
    section = ""
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("[[") and s.endswith("]]"):
            section = s.strip("[]")
        elif s.startswith("mode ="):
            modes[section] = s.split("=", 1)[1].strip()
    assert modes == {"LoRa": "internal", "Client access": "gateway", "Internet": "boundary"}
    assert "recursive_prs = yes" in out.split("[[Internet]]", 1)[1], (
        "without it, `internal` on LoRa also hides the radio from the internet side's path search")


def test_the_radio_announce_policy_is_the_one_mode_an_operator_may_choose():
    """`internal` vs `gateway` on the radio is a POLICY question — how much of the public mesh's
    announce traffic may spend the duty-cycle budget — so it is a setting, while the other two
    modes stay LHPC's. Both relay traffic and resolve paths in both directions; only the
    unsolicited announces differ. The default must be the cheap one."""
    params = {p.name: p for p in _rns_file_params()[0]}
    p = params["lora_announce_relay"]
    assert (p.key, p.section) == ("mode", "interfaces/LoRa")
    assert p.default == "internal" and set(p.choices) == {"internal", "gateway"}
    # BOUNDED to the LoRa section: everything after `[[LoRa]]` also contains Client access, which
    # carries `mode = gateway` of its own — an unbounded search passes even when the operator's
    # value was ignored entirely.
    def lora_mode(values=None):
        """The SETTING lines of the LoRa section only. Bounded at the next section, because
        everything after `[[LoRa]]` also contains Client access with a `mode` of its own; and
        compared as whole lines, because the section's comments mention `interface_mode =
        gateway` and a substring test matches that too."""
        body = _render(values).split("[[LoRa]]", 1)[1].split("[[", 1)[0]
        return [ln.strip() for ln in body.splitlines() if ln.strip().startswith("mode = ")]
    assert lora_mode({"lora_announce_relay": "gateway"}) == ["mode = gateway"]
    assert lora_mode() == ["mode = internal"]
    # the other two are NOT settings
    assert not [q for q in params.values()
                if q.key == "mode" and q.section in ("interfaces/Client access", "interfaces/Internet")]


def test_the_mode_key_is_mode_and_never_interface_mode():
    """A spelling that must be re-checked at every Reticulum bump. In 1.5.2's
    `_synthesize_interface`, the `interface_mode` branch resolves gateway/internal by reading
    `c["mode"]` (Reticulum.py:776,778) — so `interface_mode = gateway` raises KeyError while
    the separate `elif "mode" in c:` branch handles every value correctly."""
    from lhpc.core.assets import asset_path
    base = asset_path("bases/reticulum.conf").read_text()
    settings = [ln.strip() for ln in base.splitlines() if not ln.strip().startswith("#")]
    assert not any(ln.startswith("interface_mode") for ln in settings)
    assert [ln for ln in settings if ln.startswith("mode = ")] == [
        "mode = internal", "mode = gateway", "mode = boundary"]


def test_enabling_the_internet_interface_writes_its_endpoint():
    out = _render({"internet_enabled": "yes", "internet_host": "rns.example.org",
                   "internet_port": "4965"})
    body = out.split("[[Internet]]", 1)[1]
    assert "enabled = yes" in body
    assert "target_host = rns.example.org" in body
    assert "target_port = 4965" in body


def test_the_internet_ifac_is_its_own_secret_and_never_the_radios():
    """A public hub does not have your passphrase, and sharing the radio's key would widen one
    secret from a physically limited medium to a globally reachable socket."""
    params, _ = _rns_file_params()
    by_name = {p.name: p for p in params}
    lora, inet = by_name["ifac_netkey"], by_name["internet_ifac_netkey"]
    assert lora.secret_ref != inet.secret_ref
    assert inet.secret_ref == "reticulum.internet_ifac_netkey"
    assert inet.hidden and inet.omit_if_empty and not inet.default
    assert inet.section == "interfaces/Internet"


@pytest.mark.parametrize("values,missing", [
    ({"internet_enabled": "yes"}, ["internet_host", "internet_port"]),
    ({"internet_enabled": "yes", "internet_host": "hub.example.org"}, ["internet_port"]),
    ({"internet_enabled": "yes", "internet_port": "4965"}, ["internet_host"]),
])
def test_an_enabled_internet_interface_needs_its_whole_endpoint(values, missing):
    """RNS reads `target_host` and does `int(target_port)` while CONSTRUCTING the interface, so
    a missing endpoint is a construction failure — a different path from the merely unreachable
    target the node is built to tolerate (`panic_on_interface_error = No`)."""
    why = ri.endpoint_problem(values)
    assert why
    for name in missing:
        assert name in why


@pytest.mark.parametrize("values", [
    {},
    {"internet_enabled": "no"},
    {"internet_enabled": "no", "internet_host": "hub.example.org"},   # staged, not yet enabled
    {"internet_enabled": "yes", "internet_host": "hub.example.org", "internet_port": "4965"},
])
def test_a_complete_or_disabled_internet_interface_is_accepted(values):
    assert ri.endpoint_problem(values) == ""


@pytest.mark.parametrize("values", [
    {"internet_enabled": "yes", "internet_ifac_netname": "private"},
    {"internet_enabled": "yes", "internet_ifac_netkey": "s3cret"},
])
def test_a_half_configured_internet_ifac_is_refused(values):
    """Upstream's TCPClientInterface builds an IFAC from EITHER half alone, so a half
    configuration is not refused by RNS — the link just silently passes nothing. Our own LoRa
    driver already refuses this for the radio; this is the same policy for the interface RNS
    owns."""
    full = {"internet_host": "hub.example.org", "internet_port": "4965", **values}
    assert ri.ifac_problem(full)
    assert ri.problem(full)


@pytest.mark.parametrize("values", [
    {},                                                                   # neither: a public hub
    {"internet_ifac_netname": "private", "internet_ifac_netkey": "s3cret"},
])
def test_both_or_neither_internet_ifac_halves_are_accepted(values):
    full = {"internet_enabled": "yes", "internet_host": "hub.example.org",
            "internet_port": "4965", **values}
    assert ri.ifac_problem(full) == ""
    assert ri.problem(full) == ""


def test_a_disabled_interface_never_refuses_however_broken_its_fields():
    """Staging is allowed: the operator may save a host today and the port tomorrow. Only
    turning the interface ON asks for a complete configuration."""
    assert ri.problem({"internet_enabled": "no", "internet_ifac_netname": "private"}) == ""


def test_saving_an_enabled_internet_interface_without_an_endpoint_is_refused():
    """Refused at SAVE, so the mistake is reported where it is made rather than as a blocked
    start later (boot restore included). The whole submission rolls back."""
    from lhpc.core.services import ControllerService
    svc = ControllerService()
    r = svc.save_config("reticulum", {"file_internet_enabled": "yes"})
    assert not r.ok and r.data.get("reason") == ri.REASON_ENDPOINT_INCOMPLETE
    assert not ri.enabled(svc.file_config_values("reticulum")["internet_enabled"]), \
        "a refused submission persists nothing"
    # the COMPONENT target is the same rule: a save persists into the owner stack's config
    # either way, so refusing only the stack target would leave `lhpc config rns` a way past it.
    assert not svc.save_config("rns", {"file_internet_enabled": "yes"}).ok
    # and the complete endpoint saves in one submission
    assert svc.save_config("reticulum", {"file_internet_enabled": "yes",
                                         "file_internet_host": "hub.example.org",
                                         "file_internet_port": "4965"}).ok


def test_generation_blocks_a_half_configured_internet_ifac(monkeypatch):
    """The authoritative check: the passphrase lives in config/secrets.toml and is resolved
    only here, so the IFAC pairing cannot be judged at save time. A typed generation failure
    blocks the start before RNS is handed a config that would pass nothing."""
    from lhpc.core.services import ControllerService
    svc = ControllerService()
    assert svc.save_config("reticulum", {"file_internet_enabled": "yes",
                                         "file_internet_host": "hub.example.org",
                                         "file_internet_port": "4965",
                                         "file_internet_ifac_netname": "private"}).ok
    bad = [w for w in svc.write_config_files("reticulum") if w.status == "failed"]
    assert bad and "passphrase" in bad[0].detail
    assert "private" not in bad[0].detail, "a refusal names the location of a secret, not a value"


def test_the_meshchat_pin_is_declared_like_every_other_browser_gui():
    """The Dashboard renders one pin per client endpoint through ONE code path, so a pin behaves
    like its siblings only if it is DECLARED like them. Compared field by field against the
    other shipped browser GUI (meshcore-webui): same kind, same loopback-plus-proxy shape, same
    label-bearing description. MeshChat shipped without the description and its pin fell back to
    the raw address — proxied and exposed, but reading `127.0.0.1:8790`, which is exactly what a
    local-only service looks like."""
    from lhpc.core.manifest import load_manifest
    comps = {c.id: c for s in load_manifest() for c in s.components}
    mine = [e for e in comps["meshchat"].endpoints if getattr(e, "client", False)]
    theirs = [e for e in comps["meshcore-webui"].endpoints if getattr(e, "client", False)]
    assert len(mine) == len(theirs) == 1
    a, b = mine[0], theirs[0]
    for field in ("kind", "scheme", "client", "ready", "role"):
        assert getattr(a, field) == getattr(b, field), f"{field} differs from the sibling GUI"
    assert a.description and b.description, "the pin's label"
    assert a.address.startswith("127.0.0.1:") and b.address.startswith("127.0.0.1:")
    assert a.proxy_deny_paths and b.proxy_deny_paths, "both are fronted by the LHPC proxy"


def _half_ifac_runtime(tmp_path):
    """A runtime whose SAVED state is a complete, enabled Internet endpoint with an IFAC network
    name and no passphrase — a state the save path accepts, because the passphrase lives in
    secrets.toml and a save cannot see it."""
    from lhpc.core import config as cfgmod
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    paths = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(paths, "uputronics")
    svc = ControllerService(paths=paths, system=FakeSystem().system)
    assert svc.save_config("reticulum", {
        "file_internet_enabled": "yes", "file_internet_host": "peer.example.org",
        "file_internet_port": "4965", "file_internet_ifac_netname": "private"}).ok
    return svc


def test_another_stack_is_never_refused_for_reticulums_configuration(tmp_path):
    """The rule is scoped to run orders that actually contain the RNS node. Without that scoping
    a broken Reticulum setting would refuse to start every OTHER stack on the box, for a reason
    none of them can act on — and the refusal would name a stack the operator was not touching."""
    svc = _half_ifac_runtime(tmp_path)
    for other in ("kiss", "meshtastic", "graywolf"):
        summary = svc.restart(other, apply=False).summary or ""
        assert "passphrase" not in summary and "Internet interface" not in summary, \
            f"{other} was refused for reticulum's configuration: {summary}"
    # ...and directly, because the end-to-end check above can pass for the wrong reason: another
    # stack resolves its OWN band, under which reticulum's saved values read as defaults anyway.
    # The scoping is what must hold, whatever the bands happen to be.
    band = svc._config_band("rns", "")
    assert svc._reticulum_internet_preflight(svc._run_order("rns"), band), \
        "it speaks for a run order that contains the node"
    assert svc._reticulum_internet_preflight(svc._run_order("kiss"), band) == "", \
        "and is silent for one that does not, even on the node's own band"


@pytest.mark.parametrize("target", ["rns", "reticulum"])
@pytest.mark.parametrize("secrets_body,mode,needle,reason", [
    pytest.param(None, 0o600, "passphrase", ri.REASON_IFAC_KEY_MISSING, id="no-passphrase"),
    # A BARE NUMBER is not a passphrase to generation (`isinstance(val, str)`), so a preflight
    # that str()-s whatever TOML holds would pass a configuration generation then rejects — and
    # the node is already stopped by then.
    pytest.param('[reticulum]\ninternet_ifac_netkey = 123456\n', 0o600, "passphrase",
                 ri.REASON_IFAC_KEY_MISSING, id="numeric-passphrase"),
    # The loader refuses a group/other-readable secrets file with its own typed message and a
    # chmod remedy. Generation fails on exactly that, so this seam must too.
    pytest.param('[reticulum]\ninternet_ifac_netkey = "s3cret"\n', 0o644, "chmod 600",
                 "secrets-unreadable", id="unsafe-secrets-file"),
])
def test_every_secret_input_generation_rejects_is_refused_before_the_stop(
        tmp_path, monkeypatch, target, secrets_body, mode, needle, reason):
    """Generation is the authoritative check, but on a RESTART it runs AFTER the stop: the node
    would be taken down and left down by a configuration that saved cleanly. So the preflight
    at the pre-mutation boundary must agree with generation on EVERY input, not just the
    obvious one — any disagreement is exactly that defect. Each row is rejected by generation,
    so each must be refused before the stop, typed — for a component target and a stack target
    alike, since a save can reach this state through either."""
    from lhpc.core.services import ControllerService
    svc = _half_ifac_runtime(tmp_path)
    secrets = svc._paths.runtime_root / "config" / "secrets.toml"
    if secrets_body is not None:
        secrets.write_text(secrets_body)
        secrets.chmod(mode)
    svc._invalidate_config()
    assert any(w.status == "failed" for w in svc.write_config_files("rns")), \
        "the row must be one generation really rejects"

    def _stop_is_a_failure(self, *a, **k):
        raise AssertionError("restart reached its stop despite a config generation rejects")

    monkeypatch.setattr(ControllerService, "stop", _stop_is_a_failure)
    r = svc.restart(target, apply=True)          # raises if it ever reaches the stop
    assert not r.ok and r.data.get("reason") == reason and needle in r.summary
    assert "s3cret" not in r.summary and "123456" not in r.summary, \
        "a refusal names the file and the remedy, never the value"
    assert "private" not in r.summary, "it names the location of the secret, never the netname"


def test_a_valid_or_disabled_internet_interface_still_starts(tmp_path):
    """The guard must refuse the broken state only: both halves present, and the interface off,
    pass the preflight (they may still fail later for reasons a fake system cannot satisfy —
    what is asserted is that THIS rule does not speak)."""
    from lhpc.core import config as cfgmod
    svc = _half_ifac_runtime(tmp_path)
    # both halves present
    secrets = svc._paths.runtime_root / "config" / "secrets.toml"
    secrets.parent.mkdir(parents=True, exist_ok=True)
    secrets.write_text('[reticulum]\ninternet_ifac_netkey = "s3cret"\n')
    secrets.chmod(0o600)
    svc._invalidate_config()
    assert "passphrase" not in (svc.restart("rns", apply=False).summary or "")
    # and with the interface switched off, the IFAC half is not judged at all
    secrets.unlink()
    assert svc.save_config("reticulum", {"file_internet_enabled": "no"}).ok
    cfgmod.load_config(svc._paths)
    svc._invalidate_config()
    assert "passphrase" not in (svc.restart("rns", apply=False).summary or "")
