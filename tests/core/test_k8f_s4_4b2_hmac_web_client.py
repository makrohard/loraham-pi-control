"""K8f S4-4b2 (G1-K8F-S4-D2-CTX N1, N2; the hmac L rows): a box-wide answer is every saved instance's. The mesh password
(one secret file per box) is enabled only when every saved meshcom instance names it, one at least, and enabling,
disabling or rolling it back writes every instance; the web client's status names each meshtastic instance whose own
web_root serves another directory."""
import radios_fixture

from lhpc.core import config, instances
from lhpc.core import web_client as wc
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, rows=(radios_fixture.R1, radios_fixture.R2)):
    radios_fixture.write(tmp_path, rows)                                         # r1 and r2, both on 433 unless named
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _password_files(svc):
    c = svc._hmac_component("meshcom")
    return {i.rid: svc._resolved_param_value("meshcom", "run", c.id, "password_file", inst=i)
            for i in instances.saved_insts(svc, "meshcom")}


def test_enabling_the_mesh_password_writes_every_meshcom_instance(tmp_path):
    # red on b4f83823: only the band's fallback instance (r1) was written; meshcom@r2 stayed open
    svc = _svc(tmp_path)
    assert svc.hmac_set_secret("meshcom", "enable").ok
    files = _password_files(svc)
    assert set(files) == {"r1", "r2"} and all(files.values()), files
    assert svc.hmac_status("meshcom") is True
    assert svc.hmac_set_secret("meshcom", "disable").ok
    assert not any(_password_files(svc).values()) and svc.hmac_status("meshcom") is False


def test_the_mesh_password_is_enabled_only_when_every_instance_names_it(tmp_path):
    # meshcom@r1 names the password file, meshcom@r2 does not: not enabled. red on b4f83823: True (r1's value only)
    svc = _svc(tmp_path)
    r1 = instances.parse_target(svc, "meshcom@r1", "start")[1]
    assert svc.save_config_bundle("meshcom", values={"password_file": "{runtime}/config/secrets/xr_pw"}, inst=r1,
                                  _allow_managed_params=frozenset({"password_file"})).ok
    assert svc.hmac_status("meshcom") is False


def test_a_box_without_a_meshcom_radio_has_no_mesh_password_to_name(tmp_path):
    # a hardware list with no 433 radio: no saved meshcom instance, so not enabled (all() over none is not "enabled"),
    # whatever the global file holds; enabling writes the secret file alone and says no instance uses it yet
    # (DEC-W409w: no refusal; the global file is not written). red on b4f83823: the global file's value (True)
    svc = _svc(tmp_path, (radios_fixture.R2_868,))
    assert instances.saved_insts(svc, "meshcom") == []
    config.save_stack_config(svc._paths, "meshcom", {"password_file": "/x"}, inst=None)          # the global file
    assert svc.hmac_status("meshcom") is False
    r = svc.hmac_set_secret("meshcom", "enable")
    assert r.ok and r.summary == "HMAC password enabled (no meshcom instance uses it yet)", r
    assert svc._xr_pw_path().exists() and svc.hmac_status("meshcom") is False
    assert config.load_stack_config(svc._paths, "meshcom", inst=None).get("password_file") == "/x"


def test_a_refused_instance_write_rolls_the_written_ones_back(tmp_path, monkeypatch):
    # meshcom@r2's write is refused: meshcom@r1, written first, gets its old value back and the enable fails
    svc = _svc(tmp_path)
    real = ControllerService.save_config_bundle

    def save(self, target, *, values=None, inst=None, **kw):
        if getattr(inst, "rid", "") == "r2":
            from lhpc.core.service_base import ActionResult
            return ActionResult(False, "refused for the test")
        return real(self, target, values=values, inst=inst, **kw)
    monkeypatch.setattr(ControllerService, "save_config_bundle", save)
    r = svc.hmac_set_secret("meshcom", "enable")
    assert not r.ok and r.summary == "could not enable HMAC password: meshcom@r2: refused for the test", r.summary
    assert not any(_password_files(svc).values()), _password_files(svc)


def _refusing(monkeypatch, refuse):
    """save_config_bundle refusing (False) or raising (an exception) where refuse(inst, values) says so."""
    real = ControllerService.save_config_bundle

    def save(self, target, *, values=None, inst=None, **kw):
        from lhpc.core.service_base import ActionResult
        how = refuse(inst, values or {})
        if isinstance(how, Exception):
            raise how
        return ActionResult(False, "refused for the test") if how else real(self, target, values=values, inst=inst, **kw)
    monkeypatch.setattr(ControllerService, "save_config_bundle", save)


def test_a_restore_that_is_refused_is_named(tmp_path, monkeypatch):
    # G1-K8F-S4-4B2 B1: r2's write is refused and r1's restore is refused too: the enable fails naming meshcom@r1 as not
    # restored. red on 27b15a0c: only r2's refusal was reported, r1 silently kept the new value
    svc = _svc(tmp_path)
    _refusing(monkeypatch, lambda inst, values: getattr(inst, "rid", "") == "r2" or values.get("password_file") == "")
    r = svc.hmac_set_secret("meshcom", "enable")
    assert not r.ok and r.summary.endswith("meshcom@r2: refused for the test (not restored: meshcom@r1)"), r.summary


def test_a_write_that_raises_restores_the_written_ones(tmp_path, monkeypatch):
    # G1-K8F-S4-4B2 B2: saving meshcom@r2 raises OSError: meshcom@r1, written first, is restored and the enable fails by
    # name. red on 27b15a0c: the exception escaped and r1 kept the new value
    svc = _svc(tmp_path)
    _refusing(monkeypatch, lambda inst, values: OSError("disk full") if getattr(inst, "rid", "") == "r2" else False)
    r = svc.hmac_set_secret("meshcom", "enable")
    assert not r.ok and "meshcom@r2: disk full" in r.summary, r.summary
    assert not any(_password_files(svc).values()), _password_files(svc)


def test_a_secret_file_failure_names_an_instance_it_could_not_restore(tmp_path, monkeypatch):
    # G1-K8F-S4-4B2 B3: both instances written, the secret file write fails, and meshcom@r2's restore is refused: the
    # failure names it. red on 27b15a0c: the restore's result was discarded ("rolled back" only)
    from lhpc.core import runtime_fs
    svc = _svc(tmp_path)
    real_write = runtime_fs.atomic_write

    def write(paths, path, *a, **k):
        if path == svc._xr_pw_path():
            raise OSError("no space")                                            # the secret file only
        return real_write(paths, path, *a, **k)
    monkeypatch.setattr(runtime_fs, "atomic_write", write)
    _refusing(monkeypatch, lambda inst, values: getattr(inst, "rid", "") == "r2" and values.get("password_file") == "")
    r = svc.hmac_set_secret("meshcom", "enable")
    assert not r.ok and r.summary.endswith("no space — rolled back, not restored: meshcom@r2"), r.summary
    assert _password_files(svc)["r1"] == "", _password_files(svc)


def test_the_web_client_status_names_each_instance_serving_another_root(tmp_path, monkeypatch):
    # meshtastic on r1 (433) and r2 (868); meshtastic@r2's own web_root is /srv/x: the status names it. red on
    # b4f83823: the band's fallback instance (r1, the managed root) was read: no "not served"
    svc = _svc(tmp_path, (radios_fixture.R1, radios_fixture.R2_868))
    monkeypatch.setattr(wc, "status_line", lambda *a: "web client 2.6.0")
    monkeypatch.setattr(wc, "daemon_commit", lambda s: "")
    r2 = instances.parse_target(svc, "meshtastic@r2", "start")[1]
    config.save_stack_config(svc._paths, "meshtastic", {"file_web_root": "/srv/x"}, inst=r2)
    svc.invalidate_snapshot()
    assert wc.status(svc) == ("web client 2.6.0 — not served: meshtasticd's RootPath is /srv/x on meshtastic@r2"
                              " (an operator setting)"), wc.status(svc)


def test_a_save_that_fails_inside_its_write_leaves_its_own_file_as_it_was(tmp_path, monkeypatch):
    # G1-K8F-S4-4B2-RE B1 (measured, DEC: close by a pin): meshcom@r2's save fails INSIDE its write (the atomic replace
    # of its file raises): the journalled transaction leaves r2's file as it was, and meshcom@r1, written first, is
    # restored; the failure names r2
    from lhpc.core import config as cfg
    svc = _svc(tmp_path)
    r2_file = tmp_path / "config" / "stacks" / "meshcom@r2.toml"
    before = r2_file.read_bytes() if r2_file.exists() else None
    real = cfg._atomic_write

    def write(paths, path, *a, **k):
        if path.name == "meshcom@r2.toml":
            raise OSError("disk full")
        return real(paths, path, *a, **k)
    monkeypatch.setattr(cfg, "_atomic_write", write)
    r = svc.hmac_set_secret("meshcom", "enable")
    assert not r.ok and "meshcom@r2: " in r.summary and "not restored" not in r.summary, r.summary
    assert (r2_file.read_bytes() if r2_file.exists() else None) == before
    assert not any(_password_files(svc).values()), _password_files(svc)
