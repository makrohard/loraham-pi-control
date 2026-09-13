"""The stack row's 'Update' link and the head pill, rendered from the CACHED source-check
verdict: a behind DEPENDENCY opens its own #comp-<id> subsection, a behind MAIN opens the
stack's Install section and paints the head pill yellow; a stale or empty cache shows neither.
The first three cases fake freshness at the cached-verdict seam (`_fake_freshness`); the
render cases below drive the real seam with a recorded verdict and a probed head."""

import time

import pytest

import htmlq
from lhpc.core import stackupdates as su
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult as CR, FakeSystem
from lhpc.core.services import ControllerService

DAEMON_REMOTE = "https://github.com/makrohard/LoRaHAM_Daemon.git"
A = "a" * 40
B = "b" * 40


@pytest.fixture
def _fake_freshness(monkeypatch):
    # resolve a cached entry straight to our injected status (bypass the head-match check).
    monkeypatch.setattr(su, "effective_status",
                        lambda entry, head: (entry or {}).get("k", "unchecked"))


def _ids(tmp_path):
    d = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).stack("daemon")
    dep = [c.id for c in d.components if c.id != d.main_component.id][0]
    return d.main_component.id, dep


def _faked_client(web, tmp_path, behind):
    def factory():
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        svc.source_check_view = lambda: {"components": {cid: {"k": st} for cid, st in behind.items()},
                                         "checked_at": int(time.time())}
        return svc
    return web(service_factory=factory)


def _update_links(body):
    """Every stack-row Update link (the .row-actions overlay after the row's <details>)."""
    return htmlq.parse(body).find("a", class_="update-link")


def _daemon_update_href(body):
    links = [a for a in _update_links(body) if "comp=" in (a["href"] or "") or "stack-install" in (a["href"] or "")]
    return links[0]["href"] if links else ""


@pytest.mark.usefixtures("_fake_freshness")
def test_behind_dependency_links_to_its_component_subsection(web, tmp_path):
    main_id, dep = _ids(tmp_path)
    c = _faked_client(web, tmp_path, {dep: "behind"})
    href = _daemon_update_href(c.get("/stacks").get_data(as_text=True))
    assert f"comp={dep}" in href and href.endswith(f"#comp-{dep}")   # deep link to the component
    assert "stack-install" not in href
    # the linked component subsection exists and force-opens on the deep link
    opened = htmlq.parse(c.get(f"/stacks?open=daemon&comp={dep}").get_data(as_text=True))
    assert opened.by_id(f"comp-{dep}").has_attr("open")


@pytest.mark.usefixtures("_fake_freshness")
def test_behind_main_links_to_stack_install_section(web, tmp_path):
    main_id, dep = _ids(tmp_path)
    c = _faked_client(web, tmp_path, {main_id: "behind"})
    href = _daemon_update_href(c.get("/stacks").get_data(as_text=True))
    assert href.endswith("#stack-install-daemon") and "comp=" not in href   # main -> Install section
    assert "open=daemon" in href and "inst=daemon" in href


@pytest.mark.usefixtures("_fake_freshness")
def test_component_details_carry_an_anchor_id(web, tmp_path):
    main_id, dep = _ids(tmp_path)
    body = _faked_client(web, tmp_path, {}).get("/stacks?open=daemon").get_data(as_text=True)
    assert htmlq.parse(body).present(f"comp-{dep}")                 # anchorable even when up to date


# --- the real seam: a recorded verdict against a probed head --------------------------------------

def _git_src(src, sha):
    """A clean git checkout at `sha`. `probe_source` needs both: a failing `status
    --porcelain=v2` makes it return UNKNOWN *without* a head, and the row then renders no @head."""
    a = str(src)
    return {("git", "-C", a, "rev-parse", "HEAD"): CR(0, sha + "\n", ""),
            ("git", "-C", a, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
                CR(0, f"# branch.oid {sha}\n# branch.head main\n", ""),
            ("git", "-C", a, "describe", "--tags", "--always", "--dirty"): CR(0, "v111a\n", "")}


def _repo(tmp_path, rel):
    """An installed git source whose probed HEAD the page will render as @<head>: a real dir
    (the probe's `is_dir()` guard) that `FakeSystem.paths` also knows (`fs.exists`, which
    `probe_source` consults before reading a head)."""
    d = tmp_path / rel
    (d / ".git").mkdir(parents=True)
    return d


def _fs_paths(*dirs):
    return {p for d in dirs for p in (str(d), f"{d}/.git")}


def _entry_for(status, at):
    return {"remote": DAEMON_REMOTE, "source_path": "src/x",
            "local_head_at_check": at, "upstream_head": B, "status": status}


def _seed(tmp_path, entries, now=1000):
    su.record(Paths(runtime_root=tmp_path), entries, now=now)


def _page(web, tmp_path, cmds, dirs):
    body = web(system=FakeSystem(commands=cmds, paths=_fs_paths(*dirs)).system).get("/stacks").get_data(as_text=True)
    return htmlq.parse(body)


def _head_behind(doc):
    """The stack head pill that says a main source is behind: the pill's `title` attribute is the
    typed marker (the yellow class is its styling). Only the seeded daemon can carry it here."""
    return doc.find("span", title="behind its remote")


def test_main_behind_paints_head_yellow_and_shows_the_link(web, tmp_path):
    ds = _repo(tmp_path, "src/loraham-daemon")
    _seed(tmp_path, {"loraham-daemon": _entry_for(su.BEHIND, A)})
    doc = _page(web, tmp_path, _git_src(ds, A), [ds])
    assert _head_behind(doc) and "@" + A[:9] in doc.text        # the main's head pill is yellow
    assert len(doc.find("a", class_="update-link")) == 1         # one link, in the row-actions overlay


def test_only_a_dependency_behind_shows_the_link_but_leaves_head_grey(web, tmp_path):
    # The @head pill IS the main's commit — it must not go yellow because radiolib is stale.
    ds = _repo(tmp_path, "src/loraham-daemon")
    rl = _repo(tmp_path, "src/RadioLib")
    _seed(tmp_path, {"loraham-daemon": _entry_for(su.UP_TO_DATE, A),
                     "radiolib": _entry_for(su.BEHIND, B)})
    doc = _page(web, tmp_path, {**_git_src(ds, A), **_git_src(rl, B)}, [ds, rl])
    assert doc.find("a", class_="update-link")                   # any component behind -> link (overlay)
    assert not _head_behind(doc)                                 # but the main's head (summary) stays grey


@pytest.mark.parametrize("cached", [None, su.UP_TO_DATE], ids=["never-checked", "up-to-date"])
def test_nothing_behind_shows_neither_link_nor_yellow_head(web, tmp_path, cached):
    ds = _repo(tmp_path, "src/loraham-daemon")
    if cached is not None:
        _seed(tmp_path, {"loraham-daemon": _entry_for(cached, A)})
    doc = _page(web, tmp_path, _git_src(ds, A), [ds])
    assert not doc.find("a", class_="update-link") and not _head_behind(doc)


@pytest.mark.parametrize("status", [su.BEHIND, su.UP_TO_DATE])
def test_stale_cache_renders_unchecked_not_a_stale_verdict(web, tmp_path, status):
    # The verdict was computed against A; the checkout has since moved to B.
    ds = _repo(tmp_path, "src/loraham-daemon")
    _seed(tmp_path, {"loraham-daemon": _entry_for(status, A)})
    doc = _page(web, tmp_path, _git_src(ds, B), [ds])
    assert not doc.find("a", class_="update-link")               # no stale nagging
    assert not _head_behind(doc)                                 # no stale yellow
    assert "unchecked" in doc.text                               # the Install panel says so
