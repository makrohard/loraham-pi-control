"""A fake of a production function keeps the real signature.

Every `monkeypatch.setattr` of this suite that puts a lambda or def over an LHPC function or method
is checked at each CALL against the real signature (`tests/conftest.py`). So when production
changes a signature — a parameter renamed, removed or added — a caller that still uses the old
shape fails the test that fakes it, instead of a permissive fake (`lambda *a, **k: …`) swallowing
the call that the real function would refuse. Checking the call, not the fake's own parameter
list, covers every fake whatever its target expression (`svc`, `type(svc)`, a module alias, a
dotted string) without rewriting them. A refused call is also recorded and fails the test at
teardown, so production code that catches the `TypeError` cannot hide it.
"""
from __future__ import annotations

import pytest

from lhpc.core import binary_install as bi
from lhpc.core import selfupdate
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def test_a_permissive_fake_of_a_module_function_refuses_a_call_the_real_one_refuses(
        monkeypatch, signature_violations):
    monkeypatch.setattr(selfupdate, "check_upstream", lambda *a, **k: {"ok": True})
    assert selfupdate.check_upstream(None, "main") == {"ok": True}        # the real shape passes
    with pytest.raises(TypeError, match=r"lhpc\.core\.selfupdate\.check_upstream.*refuses"):
        selfupdate.check_upstream(None, branch="main", remote="origin")  # `remote` does not exist
    assert len(signature_violations) == 1
    signature_violations.clear()


def test_a_refused_call_that_production_swallows_still_fails_the_test(monkeypatch,
                                                                      signature_violations):
    monkeypatch.setattr(selfupdate, "check_upstream", lambda *a, **k: {"ok": True})
    try:
        selfupdate.check_upstream(None, remote="origin")
    except Exception:                                           # what a broad handler does
        pass
    assert signature_violations and "check_upstream" in signature_violations[0]
    signature_violations.clear()


def test_a_fake_method_on_the_class_is_bound_with_self(monkeypatch, tmp_path, signature_violations):
    monkeypatch.setattr(ControllerService, "running_band", lambda self, *a, **k: "433")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.running_band("kiss", default="") == "433"
    with pytest.raises(TypeError, match="running_band"):
        svc.running_band("kiss", band="868")                    # the real one takes `default`
    signature_violations.clear()


def test_a_fake_of_a_staticmethod_is_checked_and_stays_static(monkeypatch, tmp_path,
                                                              signature_violations):
    """A staticmethod is a descriptor on its class, which the check once skipped: a permissive
    fake over it took any call. It is checked against the function it wraps, and stays static,
    so a call through an instance passes no `self`."""
    monkeypatch.setattr(ControllerService, "_boot_start_ok", lambda *a, **k: True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc._boot_start_ok(object()) is True                 # the real shape passes
    with pytest.raises(TypeError, match="_boot_start_ok"):
        svc._boot_start_ok(object(), "extra")                   # the real one takes one argument
    signature_violations.clear()


def test_a_fake_of_a_classmethod_is_checked_and_stays_a_classmethod(monkeypatch,
                                                                   signature_violations):
    """A classmethod's fake is checked against the function it wraps and installed as a
    classmethod again: it receives the class, through the class and through an instance."""
    import inspect

    from lhpc.core.service_auto_install import StackWork
    monkeypatch.setattr(StackWork, "of", lambda cls, stack, skip=(): (cls, stack))
    assert isinstance(inspect.getattr_static(StackWork, "of"), classmethod)
    assert StackWork.of("s") == (StackWork, "s")
    with pytest.raises(TypeError, match="StackWork.of"):
        StackWork.of("s", (), "extra")                          # the real one takes two
    signature_violations.clear()


def test_a_fake_method_on_one_instance_has_no_self(monkeypatch, tmp_path, signature_violations):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(svc, "running_band", lambda *a, **k: "868")
    assert svc.running_band("kiss") == "868"
    with pytest.raises(TypeError, match="running_band"):
        svc.running_band()                                      # `stack_id` is required
    signature_violations.clear()


def test_the_dotted_string_form_is_checked_too(monkeypatch, signature_violations):
    monkeypatch.setattr("lhpc.core.binary_install.require_zstd", lambda *a, **k: None)
    assert bi.require_zstd() is None
    with pytest.raises(TypeError, match="require_zstd"):
        bi.require_zstd("zstd")                                 # the real one takes nothing
    signature_violations.clear()


def test_a_second_fake_over_the_same_attribute_is_checked_too(monkeypatch, tmp_path,
                                                               signature_violations):
    """The same attribute patched twice in one test (a helper that installs its fake per call): the
    second fake is checked against the production function, not against the first fake."""
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(selfupdate, "check_upstream", lambda *a, **k: {"ok": 1})
    monkeypatch.setattr(ControllerService, "running_band", lambda self, *a, **k: "433")
    monkeypatch.setattr(svc, "running_band", lambda *a, **k: "433")
    for _ in range(2):                              # the second and the third patch of each
        monkeypatch.setattr(selfupdate, "check_upstream", lambda *a, **k: {"ok": 2})
        monkeypatch.setattr(ControllerService, "running_band", lambda self, *a, **k: "868")
        monkeypatch.setattr(svc, "running_band", lambda *a, **k: "868")
    assert selfupdate.check_upstream(None, "main") == {"ok": 2}
    with pytest.raises(TypeError, match=r"lhpc\.core\.selfupdate\.check_upstream.*refuses"):
        selfupdate.check_upstream(None, remote="origin")
    with pytest.raises(TypeError, match=r"\.running_band.*refuses"):
        ControllerService.running_band(svc, "kiss", band="868")
    with pytest.raises(TypeError, match=r"\.running_band.*refuses"):
        svc.running_band()
    assert len(signature_violations) == 3
    signature_violations.clear()


def test_a_fake_that_lacks_a_real_parameter_fails_on_the_real_call(monkeypatch, tmp_path):
    """The other direction: a fake written before a parameter was added cannot take the call."""
    monkeypatch.setattr(ControllerService, "running_band", lambda self, sid: "433")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    with pytest.raises(TypeError):
        svc.running_band("kiss", "")


def test_a_value_or_a_non_lhpc_target_is_installed_unchanged(monkeypatch):
    import os
    sentinel = object()
    monkeypatch.setattr(selfupdate, "_LOCAL_TIMEOUT", sentinel)
    assert selfupdate._LOCAL_TIMEOUT is sentinel
    fake = lambda *a, **k: 0                                    # noqa: E731
    monkeypatch.setattr(os, "getpid", fake)
    assert os.getpid is fake
