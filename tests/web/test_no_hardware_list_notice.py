"""DEC-W384g (K4b): with no hardware list a stack has no instance, so no pages, rows or cards; the box says so instead
of showing them silently empty: the console's banner on every page and the first line of `lhpc status` (the
service's status, which the CLI renders), one text (radio_start.NO_LIST_TEXT)."""
import pytest
import radios_fixture

from lhpc.core import radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TEXT = "No hardware list: run lhpc hardware <setup> (until then no stack has an instance on a radio)"


def test_the_text_is_the_decided_one():
    assert radio_start.NO_LIST_TEXT == TEXT


@pytest.mark.no_default_hardware                 # the test baseline gives every box a list (tests/conftest.py)
def test_status_names_the_missing_hardware_list_first_and_drops_it_with_a_list(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.status().details[0] == TEXT
    radios_fixture.write(tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert TEXT not in svc.status().details


@pytest.mark.no_default_hardware
def test_every_console_page_shows_the_banner_without_a_list_and_none_with_one(web, tmp_path):
    shown = TEXT.replace("<", "&lt;").replace(">", "&gt;")                 # as the page renders it (autoescape)
    for url in ("/", "/stacks"):
        page = web().get(url).get_data(as_text=True)
        assert f'<p class="depnote depnote-warn" id="no-list-notice">{shown}.</p>' in page, url
    radios_fixture.write(tmp_path)
    assert 'id="no-list-notice"' not in web().get("/").get_data(as_text=True)
