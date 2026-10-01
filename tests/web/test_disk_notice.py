"""The disk-space notice on every page, and the watchdog's one log line per level change."""
import pytest
from htmlq import parse

from lhpc.adapters.web import app as app_mod
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

GIB = 1024 ** 3
MIB = 1024 ** 2


def _row(level, reason, path="/", free_b=GIB, free_inodes=0, total_inodes=0):
    return {"path": path, "total_b": 8 * GIB, "free_b": free_b, "free_inodes": free_inodes,
            "total_inodes": total_inodes, "level": level, "reason": reason}


def test_no_disk_notice_while_ok(monkeypatch, web):
    monkeypatch.setattr(ControllerService, "disk_health", lambda self: [_row("ok", "", free_b=5 * GIB)])
    body = web().get("/", headers={"Host": "127.0.0.1"}).get_data(as_text=True)
    assert _notice(body) is None


@pytest.mark.parametrize("row, cls, figure", [
    (_row("low", "bytes"), "depnote-warn", "/: 1.0 GiB free"),
    (_row("critical", "bytes", free_b=GIB // 4), "depnote-bad", "/: 0.2 GiB free"),
    (_row("critical", "inodes", free_b=5 * GIB, free_inodes=4, total_inodes=100), "depnote-bad",
     "/: 4 % inodes free"),
])
def test_the_disk_notice_shows_level_figure_and_link_on_every_page(monkeypatch, web, row, cls, figure):
    monkeypatch.setattr(ControllerService, "disk_health", lambda self: [row])
    client = web()
    for path in ("/", "/stacks"):
        body = client.get(path, headers={"Host": "127.0.0.1"}).get_data(as_text=True)
        notice = _notice(body)
        assert notice is not None, path
        classes, text, hrefs = notice
        assert cls in classes and figure in text and "/#sysbox" in hrefs, path


def _page(web, tmp_path, root, runtime=None):
    """The dashboard over a FakeSystem's statvfs data: the rows come from the classifier itself."""
    data = {"/": {"dev": 1, **root}}
    data[str(tmp_path)] = {"dev": 2, **runtime} if runtime else {"dev": 1, **root}
    client = web(system=FakeSystem(statvfs_data=data).system)
    return client.get("/", headers={"Host": "127.0.0.1"}).get_data(as_text=True)


def _notice(body):
    """The notice element's own class tokens, text and link targets, or None (other elements use
    depnote-bad too)."""
    doc = parse(body)
    el = doc.by_id("disk-notice")
    if el is None:
        return None
    return el.classes, el.text, [a["href"] for a in doc.within(el).find("a")]


@pytest.mark.parametrize("free_b, free_inodes, figure", [
    (int(1.4 * GIB), 4000, "/: 4 % inodes free"),       # bytes low + inodes critical
    (400 * MIB, 9000, "/: 0.4 GiB free"),               # bytes critical + inodes low
])
def test_the_notice_shows_the_worse_metric(web, tmp_path, free_b, free_inodes, figure):
    body = _page(web, tmp_path, {"total_b": 8 * GIB, "free_b": free_b,
                                 "total_inodes": 100000, "free_inodes": free_inodes})
    classes, text, _ = _notice(body)
    assert "depnote-bad" in classes and figure in text


@pytest.mark.parametrize("root, root_figure", [
    ({"total_b": 8 * GIB, "free_b": GIB}, "/: 1.0 GiB free"),                # / low
    ({"total_b": 32 * GIB, "free_b": 20 * GIB}, "/: 20.0 GiB free"),         # / ok
])
def test_the_notice_names_the_critical_runtime_filesystem(web, tmp_path, root, root_figure):
    body = _page(web, tmp_path, root, runtime={"total_b": 8 * GIB, "free_b": 100 * MIB})
    classes, text, _ = _notice(body)
    assert "depnote-bad" in classes and f"{tmp_path}: 0.1 GiB free" in text and root_figure not in text


def test_the_disk_notice_fails_closed(monkeypatch, web):
    def boom(self):
        raise OSError("statvfs")
    monkeypatch.setattr(ControllerService, "disk_health", boom)
    body = web().get("/", headers={"Host": "127.0.0.1"}).get_data(as_text=True)
    assert _notice(body) is None


class _Svc:
    """A fresh stand-in per pass, as the web loop builds a new ControllerService every pass."""
    def __init__(self, rows):
        self._rows = rows

    def disk_health(self):
        return self._rows


@pytest.fixture
def levels(monkeypatch):
    monkeypatch.setattr(app_mod, "_DISK_LEVELS", {})


def _pass(capsys, rows):
    app_mod._disk_level_log(_Svc(rows))
    return [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("disk ")]


def test_one_log_line_per_level_change_across_service_instances(levels, capsys):
    assert _pass(capsys, [_row("ok", "", free_b=5 * GIB)]) == []                # initial ok: silent
    assert _pass(capsys, [_row("ok", "", free_b=5 * GIB)]) == []                # unchanged
    assert _pass(capsys, [_row("low", "bytes")]) == ["disk /: ok -> low (/: 1.0 GiB free)"]
    assert _pass(capsys, [_row("low", "bytes")]) == []                          # unchanged
    assert _pass(capsys, [_row("critical", "bytes", free_b=GIB // 4)]) == [
        "disk /: low -> critical (/: 0.2 GiB free)"]
    assert _pass(capsys, [_row("ok", "", free_b=5 * GIB)]) == [
        "disk /: critical -> ok (/: 5.0 GiB free)"]                            # recovery


def test_a_non_ok_level_at_console_start_is_logged_once(levels, capsys):
    assert _pass(capsys, [_row("low", "bytes")]) == ["disk /: start -> low (/: 1.0 GiB free)"]
    assert _pass(capsys, [_row("low", "bytes")]) == []
