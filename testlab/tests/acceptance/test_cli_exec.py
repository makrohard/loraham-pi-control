"""Every public CLI verb through the REAL installed executable: a success (or honest
plan/refusal) case, a malformed-input case (argparse rc 2), and web/CLI agreement on
shared state. The lab root keeps everything hermetic; slow lifecycle verbs are covered
by the chain test, not duplicated here."""
from __future__ import annotations

import re

import pytest
from lhpc_testlab.testing import run_lab, run_lhpc

# verb -> (argv, acceptable rcs). rc 2 = argparse refusal (malformed) asserted for all.
SUCCESS = {
    "list": (["list"], (0,)),
    "status": (["status"], (0,)),
    "explain": (["explain", "daemon"], (0,)),
    "doctor": (["doctor"], (0, 1)),
    "deps": (["deps"], (0, 1)),
    "source-check": (["source-check", "--help"], (0,)),
    "bootstrap": (["bootstrap"], (0,)),
    "install": (["install", "daemon", "--check"], (0,)),
    "auto-install": (["auto-install", "--help"], (0,)),
    "config": (["config", "daemon"], (0,)),
    "hardware": (["hardware"], (0,)),
    "gps": (["gps"], (0,)),
    "autostart": (["autostart"], (0, 1)),
    "firewall": (["firewall"], (0, 1)),
    "stack": (["stack", "--help"], (0,)),
    "build": (["build", "--help"], (0,)),
    "test": (["test", "--help"], (0,)),
    "update": (["update", "--help"], (0,)),
    "uninstall": (["uninstall", "--help"], (0,)),
    "clean": (["clean", "--help"], (0,)),
    "known-working": (["known-working", "--help"], (0,)),
    "daemon": (["daemon", "433"], (0, 1)),
    "logs": (["logs", "daemon"], (0, 1)),
    "web": (["web", "--help"], (0,)),
    "webserver": (["webserver", "status"], (0, 1)),
    "self-update": (["self-update", "--help"], (0,)),
    "hmac": (["hmac", "--help"], (0,)),
    "help": (["help", "safety"], (0,)),
}
MALFORMED = {
    "explain": ["explain"],                       # missing target
    "hardware": ["hardware", "--bogus-flag"],
    "gps": ["gps", "--source"],                   # flag without value
    "stack": ["stack", "levitate", "daemon"],     # unknown subverb
    "webserver": ["webserver", "explode"],
    "firewall": ["firewall", "--mode", "chaotic"],
    "logs": ["logs"],
}


@pytest.mark.parametrize("verb", sorted(SUCCESS))
def test_cli_verb_succeeds_or_refuses_honestly(lab, verb):
    argv, rcs = SUCCESS[verb]
    r = run_lhpc(lab.env, *argv, timeout=180)
    assert r.returncode in rcs, (verb, r.returncode, r.stdout[-400:], r.stderr[-400:])
    assert (r.stdout + r.stderr).strip()          # never silent


@pytest.mark.parametrize("verb", sorted(MALFORMED))
def test_cli_malformed_input_is_a_typed_refusal(lab, verb):
    r = run_lhpc(lab.env, *MALFORMED[verb], timeout=60)
    assert r.returncode == 2, (verb, r.returncode, r.stderr[-300:])
    assert r.stderr.strip()                       # names the problem


def test_cli_unknown_stack_refused_not_crash(lab):
    for argv in (["install", "flying-toaster", "--check"], ["explain", "flying-toaster"],
                 ["logs", "flying-toaster"]):
        r = run_lhpc(lab.env, *argv, timeout=60)
        assert r.returncode != 0 and "Traceback" not in r.stderr, argv


def test_web_and_cli_agree_on_scenario_state(lab, client):
    run_lab(lab.env, "scenario", "degraded", check=True)
    _s, page = client.get("/testlab")
    # Bind `checked` to the degraded radio INSIDE one tag. "degraded" appears in the page
    # whatever the scenario (the form lists every one), and the two attributes are never
    # adjacent, so the old pair of disjuncts could not tell the states apart at all.
    assert any('value="degraded"' in tag and "checked" in tag
               for tag in re.findall(r"<input[^>]*>", page)), page
    out = run_lab(lab.env, "status", check=True).stdout
    assert "degraded" in out and "868=FAILED" in out
