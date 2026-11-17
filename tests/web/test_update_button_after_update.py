"""Week item 9 (W204, W208): after a successful Update the stack row keeps no Update button.

A bare Update installs the stack's pin ("Known working": the manifest pin), but the freshness check compared the installed head with the branch TIP. On a pinned
source the tip is usually ahead of the pin, so the check said "behind" and the row offered an Update
that could never clear it (Chat: W204, MeshCore: W208). The verdict is now relative to what the
source follows: a source installed from the pinned selector is up to date at its pin and behind
anywhere else; one installed from dev keeps the branch tip; without an ownership record, as before.

Driven through the stack card routes: POST /source-check/<stack> (the check), then GET /stacks.
"""
from __future__ import annotations

import pytest

import htmlq
from lhpc.core import source_registry
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult as CR
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TIP = "c" * 40                              # the branch tip, ahead of every pin
OTHER = "d" * 40                            # a head that is neither the pin nor the tip


def _main_source(tmp_path, sid):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    comp = next(c for c in svc.stack(sid).components if c.id == svc.stack(sid).main)
    return comp, svc._effective_remote(comp)


def _client(web, tmp_path, sid, *, head, selector):
    """The stack's main source installed at `head`, owned with `selector` (None = no record), its remote's
    branch at TIP. Returns (client, comp)."""
    comp, remote = _main_source(tmp_path, sid)
    src = tmp_path / comp.source.path
    (src / ".git").mkdir(parents=True)
    a = str(src)
    full = f"refs/heads/{comp.source.branch}" if comp.source.branch else "HEAD"     # the exact name it is asked by
    cmds = {("git", "ls-remote", remote, full): CR(0, f"{TIP}\t{full}\n", ""),
            ("git", "-C", a, "rev-parse", "HEAD"): CR(0, head + "\n", ""),
            ("git", "-C", a, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
                CR(0, f"# branch.oid {head}\n# branch.head main\n", ""),
            ("git", "-C", a, "describe", "--tags", "--always"): CR(0, "v1\n", ""),
            # a full clone that holds no other commit of these tests (STABLE-LANE P-2 asks the checkout)
            ("git", "-C", a, "rev-parse", "--is-shallow-repository"): CR(0, "false\n", ""),
            **{("git", "-C", a, "cat-file", "-e", o): CR(1, "", "")
               for o in {TIP, OTHER, KW, comp.source.pin_commit} - {head}}}
    if selector is not None:
        assert source_registry.write_record(Paths(runtime_root=tmp_path), source_registry.RegistryRecord(
            source_rel=comp.source.path, remote=remote, selector=selector, resolved_commit=head,
            adopted_at=1000.0, txn_id="t" * 32, components=(comp.id,)))
    fake = FakeSystem(commands=cmds, paths={a, f"{a}/.git"})
    return web(system=fake.system), comp


def _checked_page(client, csrf, sid):
    r = client.post(f"/source-check/{sid}", data={"_csrf": csrf(client, "/stacks")})
    assert r.status_code == 302, r.status_code
    return htmlq.parse(client.get("/stacks").get_data(as_text=True))


def _update_links(doc, sid):
    return [a for a in doc.find("a", class_="update-link") if f"open={sid}" in (a["href"] or "")]


def _freshness(client, sid):
    """The stack's freshness pills in its Install section (the lazily loaded stack body): their state class."""
    doc = htmlq.parse(client.get(f"/stacks/{sid}/body").get_data(as_text=True))
    body = doc.within(doc.by_id(f"stack-install-{sid}"))
    return sorted({c for s in body.find("span", class_="ver") for c in s.classes} & {"ver-green", "ver-yellow", "ver-red"})


@pytest.mark.parametrize("sid", ["chat", "meshcore"])
def test_r1_a_pinned_source_at_its_pin_has_no_update_button_after_the_check(web, csrf, tmp_path, sid):
    """RED before the fix: the check compared the pin with the branch tip and said behind."""
    comp, _ = _main_source(tmp_path / "probe", sid)
    client, comp = _client(web, tmp_path, sid, head=comp.source.pin_commit, selector="pinned")
    doc = _checked_page(client, csrf, sid)
    assert _update_links(doc, sid) == []
    assert _freshness(client, sid) == ["ver-green"]


@pytest.mark.parametrize("sid", ["chat", "meshcore"])
def test_r2_control_a_pinned_source_off_its_pin_offers_the_update(web, csrf, tmp_path, sid):
    """CONTROL on the decision: a pinned source not at its pin is behind (Update moves it to the pin)."""
    client, _comp = _client(web, tmp_path, sid, head=OTHER, selector="pinned")
    doc = _checked_page(client, csrf, sid)
    assert len(_update_links(doc, sid)) == 1


@pytest.mark.parametrize("head,behind", [(TIP, False), (OTHER, True)])
def test_r3_control_a_dev_source_follows_its_branch_tip(web, csrf, tmp_path, head, behind):
    client, _comp = _client(web, tmp_path, "chat", head=head, selector="dev")
    doc = _checked_page(client, csrf, "chat")
    assert bool(_update_links(doc, "chat")) is behind


@pytest.mark.parametrize("head,behind", [(TIP, False), (OTHER, True)])
def test_r4_control_without_an_ownership_record_the_branch_tip_decides(web, csrf, tmp_path, head, behind):
    client, _comp = _client(web, tmp_path, "chat", head=head, selector=None)
    doc = _checked_page(client, csrf, "chat")
    assert bool(_update_links(doc, "chat")) is behind


KW = "e" * 40                               # the commit a 0.13.x known-working store names, not the manifest pin


@pytest.mark.parametrize("at_old_record", [True, False], ids=["at-old-record", "at-manifest-pin"])
def test_r5_an_old_known_working_store_is_not_read_the_manifest_pin_decides(web, csrf, tmp_path, at_old_record):
    """U7 (D3): the "Known working" lane is the manifest pin itself. A store an older LHPC left under
    profiles/known-working/ is not read: at the manifest pin the row is up to date, at the store's commit it is
    behind. RED at 912c37d5: the store's commit decided (the two cases were the other way round)."""
    import json
    comp, remote = _main_source(tmp_path / "probe", "chat")
    store = tmp_path / "profiles" / "known-working" / "chat.json"
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"version": 1, "compositions": [{"hash": "f" * 64, "validated": {}, "entries": {
        comp.id: {"commit": KW, "selector": "pinned", "remote": remote, "source_rel": comp.source.path}}}]}))
    head = KW if at_old_record else comp.source.pin_commit
    client, _comp = _client(web, tmp_path, "chat", head=head, selector="pinned")
    doc = _checked_page(client, csrf, "chat")
    assert bool(_update_links(doc, "chat")) is at_old_record
