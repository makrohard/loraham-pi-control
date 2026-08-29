"""§4 — manifest lifecycle declarations are validated at load: bad readiness,
malformed command tokens, unknown placeholders, and invalid step/env schemas fail
early rather than launching a misconfigured process."""

import pytest

from lhpc.core.manifest import parse_manifest, ManifestError, load_manifest, default_manifest_path


def test_packaged_config_bases_are_not_unseeded_runtime_files():
    """A config_file `base` must be a source-relative template (read from the managed repo) or
    a properly-provisioned runtime path — NEVER a `{runtime}/config/files/*.toml`, which is the
    GENERATED-output dir that nothing seeds. (Regression: meshcore-pi pointed its base at
    `{runtime}/config/files/meshcore-pi-base.toml`, so config generation always failed with
    'No such file or directory' and the node never opened TCP 5000.)"""
    stacks = load_manifest(default_manifest_path())
    bases = {c.id: c.config_file.base
             for st in stacks for c in st.components if getattr(c, "config_file", None)}
    assert bases.get("meshcore-node") == "{asset}/bases/meshcore.toml"
    for cid, base in bases.items():
        assert not base.startswith("{runtime}/config/files/"), (cid, base)


def test_packaged_meshcom_host_test_is_recognized_at_component_level():
    """meshcom-qemu declares `test = scripts/test.sh` — it MUST parse into a component-level
    test_argv. (Regression: the key sat AFTER the [[…param]] sub-tables, so TOML bound it to the
    last param table and the stack reported 'skipped (no host tests)'.)"""
    stacks = {s.id: s for s in load_manifest(default_manifest_path())}
    qemu = next(c for c in stacks["meshcom"].components if c.id == "meshcom-qemu")
    assert qemu.test_argv[0] == "scripts/test.sh", qemu.test_argv
    # The managed-qemu path is FORWARDED so the self-boot finds the in-root source build
    # (fresh install: clean PATH, no ~/.espressif — live find on the Pi 5 reinstall).
    assert qemu.test_argv[1] == "--qemu" and "tool-cache/qemu-xtensa" in qemu.test_argv[2]
    testable = [c for c in stacks["meshcom"].components
                if c.test_argv and (getattr(c.source, "strategy", "") or "") != "link"]
    assert testable, "meshcom must have at least one testable component"


def test_process_exec_name_matches_run_binary():
    """`process.exec_name` MUST identify the launched binary the way probe_process matches it:
    basename(run_argv[0]), or — when argv[0] is a python interpreter — basename(the script token).
    Otherwise probe_process never matches the RUNNING process, so `status` reads "stopped" for a
    healthy component and `known-working` refuses to confirm it. (Regression: after kiss got its own
    repo the binary was renamed loraham_kiss_tnc -> loraham-kiss-tnc, but process.exec_name kept the
    underscores, so the running TNC showed "stopped" and could never be marked known-working.)"""
    import posixpath
    import re
    py = re.compile(r"^python[0-9.]*$")
    checked = 0
    for st in load_manifest(default_manifest_path()):
        for c in st.components:
            if not (c.process and c.process.exec_name and c.run_argv):
                continue
            a0 = c.run_argv[0]
            if "{" in a0:                      # placeholder argv[0] — not statically resolvable
                continue
            base0 = posixpath.basename(a0.lstrip("./") or a0)
            if base0.endswith(".sh"):          # a shell WRAPPER execs a different final binary
                continue                       # (e.g. meshcom-qemu run.sh -> qemu-system-xtensa)
            ok = base0 == c.process.exec_name
            if not ok and py.match(base0) and len(c.run_argv) >= 2 and "{" not in c.run_argv[1]:
                ok = posixpath.basename(c.run_argv[1]) == c.process.exec_name
            assert ok, (f"{c.id}: process.exec_name={c.process.exec_name!r} matches neither "
                        f"run_argv[0] basename {base0!r} nor its python-script token — "
                        "probe_process will never find the running process")
            checked += 1
    assert checked >= 3, f"invariant did not exercise enough components ({checked})"


def _manifest(comp: dict) -> dict:
    base = {"id": "c", "name": "c", "kind": "service"}
    base.update(comp)
    return {"stack": [{"id": "s", "main": "c", "component": [base]}]}


def _ok(comp: dict):
    return parse_manifest(_manifest(comp))


@pytest.mark.parametrize("comp,msg", [
    pytest.param({"run_argv": ["./app"], "readiness": "bogus"}, None,
                 id="test_unknown_readiness_rejected"),
    pytest.param({"run_argv": ["./app"]}, None,
                 id="test_missing_readiness_on_runnable_rejected"),
    pytest.param({"run_argv": ["./app"], "readiness": "endpoint",
                  "endpoint": [{"kind": "tcp", "address": "127.0.0.1:9", "ready": False}]}, None,
                 id="test_endpoint_readiness_without_ready_endpoint_rejected"),
    pytest.param({"run_argv": ["./app", "a{b"], "readiness": "process"}, None,
                 id="test_malformed_command_token_rejected"),
    pytest.param({"run_argv": ["./app", "{param:nope}"], "readiness": "process"}, None,
                 id="test_unknown_parameter_placeholder_rejected"),
    pytest.param({"run_argv": ["./app", "{operator:secret}"], "readiness": "process"}, None,
                 id="test_unknown_operator_placeholder_rejected"),
    pytest.param({"run_argv": ["./app"], "readiness": "process",
                  "pre_steps": [{"kind": "danger"}]}, None,
                 id="test_invalid_pre_step_kind_rejected"),
    pytest.param({"run_argv": ["./app"], "readiness": "process",
                  "post_steps": [{"kind": "danger"}]}, None,
                 id="test_invalid_post_step_kind_rejected"),
    pytest.param({"run_argv": ["./app"], "readiness": "process",
                  "run_env": {"BAD NAME": "x"}}, None,
                 id="test_invalid_env_name_rejected"),
    pytest.param({"run_argv": ["./app"], "readiness": "process", "interactive": True}, None,
                 id="test_interactive_must_be_manual"),
    pytest.param({"run_argv": ["./app"], "readiness": "process",
                  "endpoint": [{"kind": "tcp", "address": "not-an-address"}]}, None,
                 id="test_malformed_tcp_endpoint_rejected"),
    pytest.param({"run_argv": ["./app"], "readiness": "endpoint",
                  "endpoint": [{"kind": "tcp", "address": "8.8.8.8:443", "ready": True}]}, None,
                 id="test_ready_tcp_endpoint_must_be_loopback"),
    pytest.param({"run_argv": ["./app"], "readiness": "endpoint",
                  "endpoint": [{"kind": "unix", "address": "/tmp/x.sock",
                                "ready": True, "external": True}]}, None,
                 id="test_external_endpoint_cannot_be_ready_gate"),
    pytest.param({"run_argv": ["./app"], "readiness": "process",
                  "endpoint": [{"kind": "carrier-pigeon", "address": "x"}]}, None,
                 id="test_unknown_endpoint_kind_rejected"),
    pytest.param({"run_argv": ["./app"], "readiness": "process", "readiness_timeout": 9999}, None,
                 id="test_readiness_timeout_out_of_range_rejected"),
    pytest.param({"run_argv": ["./app"], "readiness": "process", "readiness_timeout": -1}, None,
                 id="test_readiness_timeout_negative_rejected"),
    # Eager: a typo'd placeholder fails at manifest load, not minutes into a build.
    pytest.param({"run_argv": ["./app"], "readiness": "process",
                  "build_steps": [{"argv": ["make"], "announce": "watch {root}/build grow"}]}, None,
                 id="test_build_step_announce_unknown_placeholder_rejected"),
    # FAIL CLOSED on the TOML last-table trap: a component-level key (note/test/…) placed AFTER
    # a [[…param]] table binds to that table and used to be silently swallowed — a shipped
    # component note was lost exactly this way. The loader must reject it loudly instead.
    pytest.param({"run_argv": ["./app", "{param:rate}"], "readiness": "process",
                  "param": [{"name": "rate", "kind": "str", "arg": "--rate",
                             "note": "I belong to the component, not this param"}]}, "unknown key",
                 id="test_param_stray_key_rejected"),
])
def test_manifest_component_rejected(comp, msg):
    with pytest.raises(ManifestError, match=msg):
        _ok(comp)


def test_endpoint_readiness_with_ready_endpoint_ok():
    _ok({"run_argv": ["./app"], "readiness": "endpoint",
         "endpoint": [{"kind": "tcp", "address": "127.0.0.1:9", "ready": True}]})


# --- §11 endpoint validation -------------------------------------------------

def test_ready_loopback_ipv4_ok():
    _ok({"run_argv": ["./app"], "readiness": "endpoint",
         "endpoint": [{"kind": "tcp", "address": "127.0.0.1:4403", "ready": True}]})


def test_ready_loopback_ipv6_bracketed_ok():
    _ok({"run_argv": ["./app"], "readiness": "endpoint",
         "endpoint": [{"kind": "tcp", "address": "[::1]:4403", "ready": True}]})


def test_readiness_timeout_in_range_ok():
    _ok({"run_argv": ["./app"], "readiness": "process", "readiness_timeout": 45})


def test_build_step_announce_valid_placeholders_ok():
    _ok({"run_argv": ["./app"], "readiness": "process",
         "build_steps": [{"argv": ["make"],
                          "announce": "[resolve] watch {runtime}/build grow ({source})"}]})


def test_build_step_announce_non_string_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process",
             "build_steps": [{"argv": ["make"], "announce": 42}]})
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process",
             "build_steps": [{"argv": ["make"], "announce": "   "}]})


def test_param_known_keys_ok():
    stacks = _ok({"run_argv": ["./app", "{param:rate}"], "readiness": "process",
                  "param": [{"name": "rate", "kind": "str", "arg": "--rate",
                             "default": "5", "label": "Rate", "advanced": True}]})
    comp = stacks[0].components[0]
    assert comp.run_params[0].name == "rate"


# --- [stack.binary] (prebuilt-binary channel declaration) ---------------------------------------

def _binary_manifest(binary_table):
    return {"stack": [{
        "id": "s1", "name": "S1", "main": "c1",
        "component": [{
            "id": "c1", "name": "C1", "kind": "service", "purpose": "x",
            "source": {"path": "src/c1", "pin_commit": "a" * 40,
                       "remote": "https://example.invalid/c1.git", "branch": "main"},
        }],
        "binary": binary_table,
    }]}


def _valid_binary():
    return {
        "index_url": "https://example.invalid/index.json",
        "covers": ["c1"],
        "publish_roots": ["src/c1/out"],
        "proof_paths": ["src/c1/out/c1"],
        "probes": [["src/c1/out/c1", "--version"]],
    }


def test_binary_block_parses():
    from lhpc.core.manifest import parse_manifest
    st = parse_manifest(_binary_manifest(_valid_binary()))[0]
    assert st.binary is not None
    assert st.binary.covers == ("c1",)
    assert st.binary.probes == (("src/c1/out/c1", "--version"),)


def test_binary_block_absent_is_none():
    from lhpc.core.manifest import parse_manifest
    m = _binary_manifest(_valid_binary())
    del m["stack"][0]["binary"]
    assert parse_manifest(m)[0].binary is None


@pytest.mark.parametrize("mutate,msg", [
    (lambda b: b.update(index_url="http://example.invalid/i.json"), "https"),
    (lambda b: b.update(extra_key=1), "unknown key"),
    (lambda b: b.update(covers=["nope"]), "unknown component"),
    (lambda b: b.update(covers=[]), "non-empty"),
    (lambda b: b.update(publish_roots=["/etc/passwd"]), "unsafe path"),
    (lambda b: b.update(publish_roots=["src/../../x"]), "unsafe path"),
    (lambda b: b.update(proof_paths=["build/elsewhere/bin"]), "not under any publish root"),
    (lambda b: b.update(clone_required=["c9"]), "must also be in covers"),
    (lambda b: b.update(probes=[[]]), "invalid argv"),
    (lambda b: b.update(probes=[["/abs/bin", "--v"]]), "unsafe binary path"),
])
def test_binary_block_rejections(mutate, msg):
    from lhpc.core.manifest import ManifestError, parse_manifest
    b = _valid_binary()
    mutate(b)
    with pytest.raises(ManifestError, match=msg):
        parse_manifest(_binary_manifest(b))


def test_binary_covers_require_pinned_source():
    from lhpc.core.manifest import ManifestError, parse_manifest
    m = _binary_manifest(_valid_binary())
    del m["stack"][0]["component"][0]["source"]["pin_commit"]
    with pytest.raises(ManifestError, match="no pinned source"):
        parse_manifest(m)


def test_shipped_manifest_binary_declarations():
    # The three heavy stacks declare the channel; every covered component is pin-validated
    # (the index components-map comparison dies silently otherwise).
    from lhpc.core.manifest import load_manifest
    stacks = {s.id: s for s in load_manifest()}
    assert set(k for k, s in stacks.items() if s.binary) == {"daemon", "meshtastic", "meshcom"}
    for s in stacks.values():
        if not s.binary:
            continue
        by_id = {c.id: c for c in s.components}
        for cid in s.binary.covers:
            assert by_id[cid].source is not None and by_id[cid].source.pin_commit
        for cid in s.binary.clone_required:
            assert cid in s.binary.covers
