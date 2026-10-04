"""Every limit on the update path holds twice what the slow target measured (plans/PLAN-F43.md).

A build or update that passes on fast hardware can still be killed on a Pi Zero 2 W by a fixed
wall clock. The baseline `tests/data/slow-target-builds.toml` records what each operation took
on the Zero (row A) and in the throttled CI container (row C); this suite holds the product's
limits to it. The rule itself is `lhpc.core.slow_target`, shared with the `slow-build` lane.

An entry nobody measured is ABSENT, and its coverage case FAILS by name until row A (or an
`[excluded]` reason) supplies it: an unmeasured operation is never in budget. The one exception
is the bootstrap state — no measured entry at all — where coverage SKIPS and names them all.
"""
from __future__ import annotations

import datetime as dt
import re
import tomllib

import pytest

import repo_paths
from lhpc.core import slow_target as stt
from lhpc.core.manifest import default_manifest_path, load_manifest
from lhpc.version import __version__

BASELINE_FILE = repo_paths.DATA / "slow-target-builds.toml"
BASELINE = tomllib.loads(BASELINE_FILE.read_text())
MEASURED = BASELINE.get("measured", [])
EXCLUDED = BASELINE.get("excluded", {})
STACKS = load_manifest(default_manifest_path())
PYPROJECT = (repo_paths.REPO / "pyproject.toml").read_text()
THIS_MINOR = stt.version_minor(__version__)
REQUIRED = stt.required(STACKS, EXCLUDED)


def _entry(**kw) -> dict:
    e = {"component": "c", "op": "clone", "seconds": 100, "key": "pin:old", "source": "zero2w",
         "host": "h", "lhpc": f"v{THIS_MINOR[0]}.{THIS_MINOR[1]}.0 (abc1234)",
         "date": dt.date(2026, 10, 1), "evidence": "e"}
    e.update(kw)
    return e


# ---- the file ------------------------------------------------------------------------------

def test_every_baseline_entry_is_well_formed():
    bad = [f"{e.get('component')} {e.get('op')}: {err}"
           for e in MEASURED for err in stt.entry_errors(e)]
    assert not bad, "malformed [[measured]] entries:\n" + "\n".join(bad)
    pairs = [(e["component"], e["op"], e["source"], e["key"]) for e in MEASURED]
    assert len(pairs) == len(set(pairs)), "two entries for one (component, op, source, key)"


def test_every_calibration_entry_is_well_formed():
    for c in BASELINE.get("calibration", []):
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", str(c.get("workload", ""))), c
        assert c.get("source") in stt.SOURCES, c
        assert all(isinstance(c.get(p), (int, float)) and c[p] > 0
                   for p in ("cpu_s", "io_s", "mem_s")), c
        assert isinstance(c.get("date"), dt.date), c


def test_the_lhpc_label_takes_a_tag_or_a_candidate_ref():
    """Row A measures a release candidate before its tag exists: `release/<name> (sha7)` is true
    provenance and well-formed, as is a tag `vX.Y.Z (sha7)` and a self-update's
    `<from> -> <to> (sha7)`; the short SHA is mandatory in every form."""
    def label_errors(label):
        return [e for e in stt.entry_errors(_entry(lhpc=label)) if e.startswith("lhpc")]
    for good in ("v0.12.0 (6ff1937)", "release/0.11.12-bundle (3d0943a1)",
                 "v0.11.12 (3d0943a1c2b4)", "v0.11.10 -> v0.11.11 (35923b80)",
                 "v0.11.11 -> release/0.11.12-bundle (3d0943a1)"):
        assert label_errors(good) == [], good
    for bad in ("release/0.11.12-bundle", "v0.12.0", "release/0.11.12-bundle (zzzzzzz)",
                "release/ (3d0943a)", "main (3d0943a)", "v0.12 (6ff1937)", "",
                "v0.11.10 -> v0.11.11", "main -> v0.11.11 (35923b80)"):
        assert label_errors(bad), bad
    assert stt.minor(_entry(lhpc="release/0.11.12-bundle (3d0943a1)")) == (0, 11)
    assert stt.minor(_entry(lhpc="v0.12.0 (6ff1937)")) == (0, 12)
    assert stt.minor(_entry(lhpc="release/bundle (3d0943a1)")) is None
    assert stt.minor(_entry(lhpc="v0.10.9 -> v0.11.0 (35923b80)")) == (0, 11)


def test_every_excluded_component_exists_and_says_why():
    known = {c.id for st in STACKS for c in st.components}
    assert set(EXCLUDED) <= known, f"excluded but not in the manifest: {set(EXCLUDED) - known}"
    assert all(isinstance(why, str) and why.strip() for why in EXCLUDED.values())


# ---- (a) coverage --------------------------------------------------------------------------

def test_coverage_names_every_kind_of_operation():
    """A coverage list that lost an op would pass for ever."""
    ops = {op for _, op in REQUIRED}
    assert ops == set(stt.OPS), f"ops never required: {set(stt.OPS) - ops}"


def _check_coverage(measured, required, version=__version__) -> list[str]:
    """Green when every required pair is measured or waived (stt.waiver); returns the waivers,
    printed by name. Excluded components are not required at all."""
    have = {(e["component"], e["op"]) for e in measured}
    open_ = [(c, o) for c, o in required if (c, o) not in have]
    waived = [w for w in (stt.waiver(c, o, version) for c, o in open_) if w]
    missing = [f"{c} {o}" for c, o in open_ if not stt.waiver(c, o, version)]
    for w in waived:
        print(f"waived: {w}")
    boot = stt.bootstrap_reason(measured, missing)
    if boot:
        pytest.skip(boot)
    assert not missing, (
        f"no entry in {BASELINE_FILE.name} for {len(missing)} operation(s): run row A "
        "(docs/test-matrix.md) and record them, or exclude the component with a reason:\n"
        + "\n".join(missing))
    return waived


def test_coverage():
    """Every (component, op) the manifest builds, clones or fetches, and the self-update and
    CLI-venv ops, are measured — or the component is excluded with a reason. While the baseline
    holds no measured entry at all (bootstrap, before the first row A) it SKIPS, naming them."""
    _check_coverage(MEASURED, REQUIRED)


def test_coverage_skips_visibly_while_bootstrapping():
    with pytest.raises(pytest.skip.Exception) as skip:
        _check_coverage([], [("a", "build"), ("b", "clone")])
    assert str(skip.value) == "bootstrap: no row A yet — 2 operations unmeasured: a build, b clone"


def test_coverage_enforces_once_one_entry_is_measured():
    with pytest.raises(AssertionError, match=r"for 1 operation\(s\)") as red:
        _check_coverage([_entry(component="a", op="build")], [("a", "build"), ("b", "clone")])
    assert "b clone" in str(red.value) and "a build" not in str(red.value)
    _check_coverage([_entry(component="a", op="build")], [("a", "build")])


PIP = (stt.SELFUPDATE_COMPONENT, "selfupdate-pip")


def test_coverage_waives_l4_up_to_the_introducing_release_and_names_it():
    """Row A on the introducing release ran the previous release's helper: no pip sync line, on
    the Zero or anywhere. That gap is GREEN there and before, by name; never past it."""
    measured = [_entry(component="a", op="build")]
    for version in (stt.PIP_SYNC_SINCE, "0.11.11"):
        waived = _check_coverage(measured, [("a", "build"), PIP], version=version)
        assert waived and waived[0].startswith("lhpc-selfupdate selfupdate-pip (L4)")
    nxt = stt.PIP_SYNC_SINCE.rsplit(".", 1)
    for version in (f"{nxt[0]}.{int(nxt[1]) + 1}", "1.0.0"):
        with pytest.raises(AssertionError, match="lhpc-selfupdate selfupdate-pip"):
            _check_coverage(measured, [("a", "build"), PIP], version=version)


def test_coverage_lists_only_the_genuinely_unmeasured():
    """An excluded component is never required; a waived pair is not listed; a measured
    entry of an excluded component (a shared tree adopted on the Zero) is harmless."""
    excluded = set(EXCLUDED)
    assert not [c for c, _ in REQUIRED if c in excluded]
    measured = [_entry(component="a", op="build"), _entry(component=next(iter(excluded)),
                                                            op="clone")]
    with pytest.raises(AssertionError) as red:
        _check_coverage(measured, [("a", "build"), ("b", "clone"), PIP],
                        version=stt.PIP_SYNC_SINCE)
    # One unmeasured pair per line after the header, as `_check_coverage` writes them — read
    # without relying on pytest's rendering (it indents a message's lines and appends `assert`).
    listed = str(red.value).split("with a reason:\n", 1)[1].splitlines()
    assert [ln.strip() for ln in listed if ln.strip() and not ln.startswith("assert ")] \
        == ["b clone"]


def test_the_introducing_release_is_a_changelog_release():
    """PIP_SYNC_SINCE names a CHANGELOG release section once this tree has reached it: a
    renumbered release cannot leave it pointing at nothing (and so waiving L4 for ever, or
    never). A tree still below it (the series before its release section) cannot tell yet."""
    changelog = (repo_paths.REPO / "CHANGELOG.md").read_text()
    reached = stt._release(__version__) >= stt._release(stt.PIP_SYNC_SINCE)
    assert f"\n## {stt.PIP_SYNC_SINCE}\n" in changelog or not reached
    assert reached or stt.waiver(*PIP, __version__)


# ---- (b) budget ----------------------------------------------------------------------------

@pytest.mark.parametrize("op", sorted(stt.LIMITS))
def test_budget(op):
    """Every limit holds twice its slowest entry; a limit that cannot be read FAILS."""
    _check_budget(op, MEASURED, REQUIRED)


def test_an_unreadable_limit_fails_naming_component_and_source():
    def unreadable(op):
        raise stt.LimitUnavailable("L1 build stall (read by slow_target._l1_stall): "
                                   "lhpc.core.progress.STALL_S is not on this tree")
    with pytest.raises(pytest.fail.Exception) as red:
        _check_budget("build", [], [("meshcore-cli", "build"), ("x", "clone")], read=unreadable)
    msg = str(red.value)
    assert msg.startswith("build of meshcore-cli: the limit cannot be read — L1 build stall")
    assert "slow_target._l1_stall" in msg and "lhpc.core.progress.STALL_S" in msg


def test_limit_names_the_reader_when_the_product_lacks_it(monkeypatch):
    def gone():
        from lhpc.core import install
        return install.NO_SUCH_LIMIT
    monkeypatch.setitem(stt.LIMITS, "clone", ("L7a clone", gone))
    with pytest.raises(stt.LimitUnavailable, match=r"L7a clone \(read by slow_target\.gone\)"):
        stt.limit("clone")


def _check_budget(op, measured, required, read=stt.limit) -> None:
    """A limit this test cannot read FAILS, naming the components it bounds and where the limit
    is read from: an unreadable limit is a defect of the lane, never a reason to skip."""
    try:
        lim, why = read(op), ""
    except stt.LimitUnavailable as exc:
        lim, why = 0.0, str(exc)
    if why:
        comps = sorted({c for c, o in required if o == op})
        pytest.fail(f"{op} of {', '.join(comps) or '(no component)'}: the limit cannot be read "
                    f"— {why}", pytrace=False)
    worst = max((stt.quantity(e) for e in measured if e["op"] == op), default=0.0)
    assert lim >= 2 * worst, (f"{stt.LIMITS[op][0]} = {lim:.0f} s is less than twice the "
                              f"slowest measured {op} ({worst:.0f} s)")


def test_every_op_has_a_limit():
    """An op with no limit mapped is never budgeted at all."""
    assert set(stt.LIMITS) == set(stt.OPS), f"unmapped: {set(stt.OPS) - set(stt.LIMITS)}"


def test_the_limits_are_the_products():
    """Read from the product: a literal here would stay green after the product changed."""
    from lhpc.core.install import Installer
    from lhpc.core.services import ControllerService
    assert stt.limit("clone") == Installer._CLONE_TIMEOUT_S
    assert stt.limit("checkout") == Installer._CHECKOUT_TIMEOUT_S
    assert stt.limit("selfupdate-pip") == ControllerService._PIP_SYNC_TIMEOUT_S
    assert stt.limit("selfupdate-helper") > 0
    from lhpc.core import service_binary_ops, service_maintenance
    assert stt.limit("cli-venv") == service_binary_ops.CLI_VENV_TIMEOUT_S
    assert stt.limit("deb-fetch") <= service_maintenance.UPSTREAM_FETCH_TIMEOUT_S


# ---- (c) keys ------------------------------------------------------------------------------

def test_every_required_entry_has_a_key_class_on_this_tree():
    """deb-fetch is keyed by the version fetched at the run; everything else must resolve."""
    missing = [(c, o) for c, o in REQUIRED
               if o != "deb-fetch" and not stt.current_key(c, o, STACKS, PYPROJECT)]
    assert not missing, f"no key for {missing}"


def test_deps_key_ignores_the_version_and_follows_the_dependencies():
    def doc(version="1.0.0", deps='"a>=1", "b"', python=">=3.11", build='"setuptools>=68"'):
        return (f'[build-system]\nrequires = [{build}]\n[project]\nname = "x"\n'
                f'version = "{version}"\nrequires-python = "{python}"\ndependencies = [{deps}]\n')
    base = stt.deps_key(doc())
    assert stt.deps_key(doc(version="9.9.9")) == base
    assert stt.deps_key(doc(deps='"b", "a>=1"')) == base                  # order-free
    for changed in (doc(deps='"a>=2", "b"'), doc(python=">=3.12"),
                    doc(build='"setuptools>=69"')):
        assert stt.deps_key(changed) != base
    assert re.fullmatch(r"deps:[0-9a-f]{64}", stt.deps_key(PYPROJECT))


def test_no_key_holds_an_lhpc_sha():
    keys = {stt.current_key(c, o, STACKS, PYPROJECT) for c, o in REQUIRED if o != "deb-fetch"}
    assert all(k.split(":", 1)[0] in ("pin", "deb", "deps") for k in keys)


def test_a_moved_key_is_carried_not_dropped():
    z, how = stt.zero_baseline([_entry(key="pin:old")], "c", "clone", "pin:new", THIS_MINOR)
    assert how == "carried" and z["key"] == "pin:old"
    z, how = stt.zero_baseline([_entry(key="pin:new")], "c", "clone", "pin:new", THIS_MINOR)
    assert how == "fresh"


def test_a_carried_entry_from_two_minors_ago_is_no_baseline():
    old = _entry(lhpc=f"v{THIS_MINOR[0]}.{THIS_MINOR[1] - 2}.0 (abc1234)")
    assert stt.zero_baseline([old], "c", "clone", "pin:new", THIS_MINOR) == (None, "none")


# ---- (d) the compare rule of §8 ------------------------------------------------------------

def _cmp(baseline, evidence, lim=900.0, key="pin:new"):
    return stt.compare("c", "clone", lim, baseline, evidence, key, THIS_MINOR)[0]


def test_compare_passes_a_fresh_zero_entry_within_budget():
    assert _cmp([_entry(key="pin:new", seconds=300)], _entry(source="throttled-ci",
                                                             seconds=400)) == []


def test_compare_fails_a_limit_below_twice_any_measurement():
    for base, ev in (([_entry(key="pin:new", seconds=500)], _entry(source="throttled-ci")),
                     ([_entry(key="pin:new"), _entry(source="throttled-ci", seconds=460)],
                      _entry(source="throttled-ci")),
                     ([_entry(key="pin:new")], _entry(source="throttled-ci", seconds=451))):
        assert any("limit 900 s < 2 x" in f for f in _cmp(base, ev))


def test_compare_fails_without_a_zero_baseline():
    fails = _cmp([_entry(source="throttled-ci")], _entry(source="throttled-ci"))
    assert "no Zero baseline for c clone: run row A" in fails


def test_compare_fails_without_evidence():
    assert any(f.startswith("no row C evidence") for f in _cmp([_entry(key="pin:new")], None))


def test_compare_requires_row_a_when_a_moved_pin_nears_the_budget():
    carried = [_entry(key="pin:old", seconds=100)]
    assert _cmp(carried, _entry(source="throttled-ci", seconds=225)) == []
    assert any(f.startswith("pin moved and row C is near the budget")
               for f in _cmp(carried, _entry(source="throttled-ci", seconds=226)))


def test_compare_summary_prints_the_ratio():
    _, line = stt.compare("c", "clone", 900.0, [_entry(key="pin:new", seconds=200)],
                          _entry(source="throttled-ci", seconds=300), "pin:new", THIS_MINOR)
    assert "E/Z=1.50" in line and "(fresh)" in line


def test_build_is_budgeted_on_its_quiet_gap():
    e = _entry(op="build", seconds=5000, quiet_s=100)
    assert stt.quantity(e) == 100
    assert stt.compare("c", "build", 600.0, [dict(e, key="pin:new")], dict(e),
                       "pin:new", THIS_MINOR)[0] == []


# ---- calibration (§4b) ---------------------------------------------------------------------

def test_calibration_reads_only_calibration_entries():
    """A stack's [[measured]] entries can never stand in for the calibration."""
    fails = stt.calibration_failures([], "sha256:w", {"cpu_s": 1, "io_s": 1, "mem_s": 1})
    assert fails and fails[0].startswith("uncalibrated: run the Zero row")


def test_calibration_fails_a_container_faster_than_the_zero():
    zero = [{"workload": "sha256:w", "source": "zero2w", "cpu_s": 400, "io_s": 90,
             "mem_s": 60, "date": dt.date(2026, 10, 1)}]
    assert stt.calibration_failures(zero, "sha256:w",
                                    {"cpu_s": 400, "io_s": 90, "mem_s": 60}) == []
    slow_io = stt.calibration_failures(zero, "sha256:w", {"cpu_s": 500, "io_s": 89, "mem_s": 60})
    assert len(slow_io) == 1 and "io_s" in slow_io[0]
    assert stt.calibration_failures(zero, "sha256:other", {"cpu_s": 9e9, "io_s": 9e9,
                                                           "mem_s": 9e9})
    assert stt.calibration_failures(zero, "sha256:w", {"cpu_s": 500})       # parts missing


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_number_is_malformed_never_compared(bad):
    """NaN compares false with everything, so `NaN <= 0` and `NaN < z` both pass: a non-finite
    number must be refused by name before any budget comparison sees it."""
    assert stt.entry_errors(_entry(seconds=1.5)) == []
    assert stt.entry_errors(_entry(op="build", seconds=2.0, quiet_s=0.5)) == []
    assert any(e.startswith("seconds") for e in stt.entry_errors(_entry(seconds=bad)))
    assert any(e.startswith("a build entry needs quiet_s")
               for e in stt.entry_errors(_entry(op="build", seconds=2.0, quiet_s=bad)))
    zero = [{"workload": "sha256:w", "source": "zero2w", "cpu_s": 400.0, "io_s": 90.0,
             "mem_s": 60.0, "date": dt.date(2026, 10, 1)}]
    ok = {"cpu_s": 400.5, "io_s": 90.0, "mem_s": 61.0}
    assert stt.calibration_failures(zero, "sha256:w", ok) == []
    fails = stt.calibration_failures(zero, "sha256:w", {**ok, "io_s": bad})
    assert len(fails) == 1 and "io_s" in fails[0], fails
    fails = stt.calibration_failures([{**zero[0], "mem_s": bad}], "sha256:w", ok)
    assert len(fails) == 1 and "mem_s" in fails[0], fails
