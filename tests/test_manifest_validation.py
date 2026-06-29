"""§4 — manifest lifecycle declarations are validated at load: bad readiness,
malformed command tokens, unknown placeholders, and invalid step/env schemas fail
early rather than launching a misconfigured process."""

import pytest

from lhpc.core.manifest import parse_manifest, ManifestError


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
