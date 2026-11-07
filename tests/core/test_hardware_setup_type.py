"""Package item 13 (B5-D17): a hardware setup that is not a string is refused with a reason, never a TypeError.

`set_hardware_setup` looked the setup up with `setup_id not in setups`; a list, dict, set or bytearray is not
hashable, so the lookup raised. No command-line or web path passes one today (both pass strings); the service is
the public entry and refuses it like any other unknown setup, naming the type, and writes nothing."""
import pytest

from lhpc.core.paths import Paths


def _set(tmp_path, value):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    (tmp_path / "config").mkdir(exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).set_hardware_setup(value)


@pytest.mark.parametrize("value,kind", [(["loraham"], "list"), ({"loraham": 1}, "dict"), ({"loraham"}, "set"),
                                        (bytearray(b"loraham"), "bytearray")])
def test_an_unhashable_setup_is_refused_naming_its_type_and_writes_nothing(tmp_path, value, kind):
    res = _set(tmp_path, value)
    assert (res.ok, res.summary) == (False, f"unknown hardware setup {value!r}: a setup is named by a string "
                                            f"(got {kind})")
    assert res.details == _set(tmp_path, "nosuch").details                 # the same choose-from line as a string
    assert sorted(p.name for p in (tmp_path / "config").iterdir()) == []


@pytest.mark.parametrize("value,kind", [(1, "int"), (b"loraham", "bytes"), (("loraham",), "tuple")])
def test_a_hashable_non_string_names_its_type_too(tmp_path, value, kind):
    res = _set(tmp_path, value)
    assert (res.ok, res.summary) == (False, f"unknown hardware setup {value!r}: a setup is named by a string "
                                            f"(got {kind})")


def test_a_string_outside_the_catalog_keeps_its_message(tmp_path):
    res = _set(tmp_path, "nosuch")
    assert (res.ok, res.summary) == (False, "unknown hardware setup 'nosuch'")


class _S(str):
    """A str subclass: the contract is an EXACT str (`type(x) is str`), so a subclass is refused like any non-str."""


@pytest.mark.parametrize("value,kind", [(True, "bool"), (1.5, "float"), (_S("loraham"), "_S")])
def test_the_contract_is_an_exact_str(tmp_path, value, kind):
    # Ledger L18: bool is an int subclass and _S a str subclass; neither is a setup name, even one that equals a
    # catalog entry ("loraham").
    res = _set(tmp_path, value)
    assert (res.ok, res.summary) == (False, f"unknown hardware setup {value!r}: a setup is named by a string "
                                            f"(got {kind})")
    assert sorted(p.name for p in (tmp_path / "config").iterdir()) == []
