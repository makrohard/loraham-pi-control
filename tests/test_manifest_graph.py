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


@pytest.mark.parametrize("data,msg", [
    pytest.param(_mf(_stack("s", [_comp("a")]), _stack("s", [_comp("b")])), "duplicate stack id",
                 id="test_duplicate_stack_id_rejected"),
    pytest.param(_mf(_stack("s1", [_comp("x")]), _stack("s2", [_comp("x")])), "duplicate component id",
                 id="test_duplicate_component_id_rejected"),
    pytest.param(_mf(_stack("s", [_comp("a")], main="ghost")), "main",
                 id="test_main_must_be_in_own_stack"),
    pytest.param(_mf(_stack("s", [_comp("a", depends_on=["ghost"])])), "unknown component",
                 id="test_dependency_must_resolve"),
    pytest.param(_mf(_stack("s", [_comp("a", depends_on=["a"])])), "depends on itself",
                 id="test_self_dependency_rejected"),
    pytest.param(_mf(_stack("s", [_comp("a", depends_on=["b"]), _comp("b", depends_on=["a"])])),
                 "dependency cycle: a -> b -> a", id="test_cycle_rejected_with_evidence"),
    pytest.param(_mf(_stack("s", [_comp("a", depends_on=["b"]),
                                  _comp("b", depends_on=["c"]),
                                  _comp("c", depends_on=["a"])])),
                 "dependency cycle", id="test_longer_cycle_rejected"),
    pytest.param(_mf(_stack("s", [_comp("a", band="999")])), "unknown band",
                 id="test_invalid_band_rejected"),
])
def test_manifest_graph_rejected(data, msg):
    with pytest.raises(ManifestError, match=msg):
        parse_manifest(data)


def test_cross_stack_dependency_resolves():
    # A dependency may resolve to a component in ANOTHER stack (global namespace).
    data = _mf(_stack("s1", [_comp("dep")]),
               _stack("s2", [_comp("app", depends_on=["dep"])], main="app"))
    assert len(parse_manifest(data)) == 2
