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
    assert bases.get("meshcore-pi") == "examples/config-loraham868.toml"
    for cid, base in bases.items():
        assert not base.startswith("{runtime}/config/files/"), (cid, base)


def test_packaged_meshcom_host_test_is_recognized_at_component_level():
    """meshcom-qemu declares `test = scripts/test.sh` — it MUST parse into a component-level
    test_argv. (Regression: the key sat AFTER the [[…param]] sub-tables, so TOML bound it to the
    last param table and the stack reported 'skipped (no host tests)'.)"""
    stacks = {s.id: s for s in load_manifest(default_manifest_path())}
    qemu = next(c for c in stacks["meshcom"].components if c.id == "meshcom-qemu")
    assert qemu.test_argv == ("scripts/test.sh",), qemu.test_argv
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


def test_unknown_readiness_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "bogus"})


def test_missing_readiness_on_runnable_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"]})


def test_endpoint_readiness_without_ready_endpoint_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "endpoint",
             "endpoint": [{"kind": "tcp", "address": "127.0.0.1:9", "ready": False}]})


def test_endpoint_readiness_with_ready_endpoint_ok():
    _ok({"run_argv": ["./app"], "readiness": "endpoint",
         "endpoint": [{"kind": "tcp", "address": "127.0.0.1:9", "ready": True}]})


def test_malformed_command_token_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app", "a{b"], "readiness": "process"})


def test_unknown_parameter_placeholder_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app", "{param:nope}"], "readiness": "process"})


def test_unknown_operator_placeholder_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app", "{operator:secret}"], "readiness": "process"})


def test_invalid_pre_step_kind_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process",
             "pre_steps": [{"kind": "danger"}]})


def test_invalid_post_step_kind_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process",
             "post_steps": [{"kind": "danger"}]})


def test_invalid_env_name_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process",
             "run_env": {"BAD NAME": "x"}})


def test_interactive_must_be_manual():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process", "interactive": True})


# --- §11 endpoint validation -------------------------------------------------

def test_malformed_tcp_endpoint_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process",
             "endpoint": [{"kind": "tcp", "address": "not-an-address"}]})


def test_ready_tcp_endpoint_must_be_loopback():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "endpoint",
             "endpoint": [{"kind": "tcp", "address": "8.8.8.8:443", "ready": True}]})


def test_ready_loopback_ipv4_ok():
    _ok({"run_argv": ["./app"], "readiness": "endpoint",
         "endpoint": [{"kind": "tcp", "address": "127.0.0.1:4403", "ready": True}]})


def test_ready_loopback_ipv6_bracketed_ok():
    _ok({"run_argv": ["./app"], "readiness": "endpoint",
         "endpoint": [{"kind": "tcp", "address": "[::1]:4403", "ready": True}]})


def test_external_endpoint_cannot_be_ready_gate():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "endpoint",
             "endpoint": [{"kind": "unix", "address": "/tmp/x.sock",
                           "ready": True, "external": True}]})


def test_unknown_endpoint_kind_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process",
             "endpoint": [{"kind": "carrier-pigeon", "address": "x"}]})


def test_readiness_timeout_out_of_range_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process", "readiness_timeout": 9999})


def test_readiness_timeout_negative_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process", "readiness_timeout": -1})


def test_readiness_timeout_in_range_ok():
    _ok({"run_argv": ["./app"], "readiness": "process", "readiness_timeout": 45})


def test_build_step_announce_valid_placeholders_ok():
    _ok({"run_argv": ["./app"], "readiness": "process",
         "build_steps": [{"argv": ["make"],
                          "announce": "[resolve] watch {runtime}/build grow ({source})"}]})


def test_build_step_announce_unknown_placeholder_rejected():
    # Eager: a typo'd placeholder fails at manifest load, not minutes into a build.
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process",
             "build_steps": [{"argv": ["make"], "announce": "watch {root}/build grow"}]})


def test_build_step_announce_non_string_rejected():
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process",
             "build_steps": [{"argv": ["make"], "announce": 42}]})
    with pytest.raises(ManifestError):
        _ok({"run_argv": ["./app"], "readiness": "process",
             "build_steps": [{"argv": ["make"], "announce": "   "}]})
