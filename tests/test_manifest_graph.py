"""4.2 — whole-manifest graph validation: unique stack + global component IDs, `main`
in its own stack, resolvable dependencies, no self-dep or cycle (with evidence), and
valid declared bands. A structurally-broken manifest fails at parse, not at launch."""

import pytest

from lhpc.core.manifest import parse_manifest, ManifestError


def _comp(cid, **kw):
    d = {"id": cid, "name": cid}
    d.update(kw)
    return d


def _stack(sid, comps, main=""):
    return {"id": sid, "component": comps, "main": main}


def _mf(*stacks):
    return {"stack": list(stacks)}


def test_valid_graph_parses():
    data = _mf(_stack("s", [_comp("a"), _comp("b", depends_on=["a"])], main="b"))
    assert len(parse_manifest(data)) == 1


def test_duplicate_stack_id_rejected():
    with pytest.raises(ManifestError, match="duplicate stack id"):
        parse_manifest(_mf(_stack("s", [_comp("a")]), _stack("s", [_comp("b")])))


def test_duplicate_component_id_rejected():
    with pytest.raises(ManifestError, match="duplicate component id"):
        parse_manifest(_mf(_stack("s1", [_comp("x")]), _stack("s2", [_comp("x")])))


def test_main_must_be_in_own_stack():
    with pytest.raises(ManifestError, match="main"):
        parse_manifest(_mf(_stack("s", [_comp("a")], main="ghost")))


def test_dependency_must_resolve():
    with pytest.raises(ManifestError, match="unknown component"):
        parse_manifest(_mf(_stack("s", [_comp("a", depends_on=["ghost"])])))


def test_self_dependency_rejected():
    with pytest.raises(ManifestError, match="depends on itself"):
        parse_manifest(_mf(_stack("s", [_comp("a", depends_on=["a"])])))


def test_cycle_rejected_with_evidence():
    data = _mf(_stack("s", [_comp("a", depends_on=["b"]), _comp("b", depends_on=["a"])]))
    with pytest.raises(ManifestError, match="dependency cycle: a -> b -> a"):
        parse_manifest(data)


def test_longer_cycle_rejected():
    data = _mf(_stack("s", [_comp("a", depends_on=["b"]),
                            _comp("b", depends_on=["c"]),
                            _comp("c", depends_on=["a"])]))
    with pytest.raises(ManifestError, match="dependency cycle"):
        parse_manifest(data)


def test_invalid_band_rejected():
    with pytest.raises(ManifestError, match="unknown band"):
        parse_manifest(_mf(_stack("s", [_comp("a", band="999")])))


def test_cross_stack_dependency_resolves():
    # A dependency may resolve to a component in ANOTHER stack (global namespace).
    data = _mf(_stack("s1", [_comp("dep")]),
               _stack("s2", [_comp("app", depends_on=["dep"])], main="app"))
    assert len(parse_manifest(data)) == 2
