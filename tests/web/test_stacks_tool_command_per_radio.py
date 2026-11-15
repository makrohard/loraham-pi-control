"""A running stack's tools list on the Stacks page (W250: its tools' launch lines) shows the command for the page
radio's own instance, as the stack's own command does: meshcore-cli on r2 connects to r2's Companion port and takes
r2's lock, never the template's port 5000. A radio's page lists the tools only while the stack runs on that radio,
never while it runs only on another one. Through the rendered page, the command box's exact text."""
import pathlib

import radios_fixture
from htmlq import parse

import lhpc
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

SCRIPT = pathlib.Path(lhpc.__file__).parent / "data" / "scripts" / "meshcli-run.sh"


def _page(tmp_path, web, query, running=("r2",)):
    """meshcore runs on each radio of `running` (its node process); the Stacks page `query` rendered and parsed."""
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    nodes = {500 + n: ["python", "-m", "meshcore_host", f"{tmp_path}/config/files/{rid}/meshcore.toml"]
             for n, rid in enumerate(running)}
    fake = FakeSystem(cmdlines_data=nodes, listeners=[Listener("ipv4", "127.0.0.1", 5133, 1)])
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    r = web(service_factory=lambda: svc).get(query)
    assert r.status_code == 200
    return parse(r.get_data(as_text=True))


def _command(tmp_path, web, query, running=("r2",)):
    """meshcore-cli's command box text on the Stacks page `query` (see `_page`)."""
    box = _page(tmp_path, web, query, running).by_id("runc-meshcore-cli")
    assert box is not None, query
    return box.text


def _expected(tmp_path, lock, port):
    return (f"cd {tmp_path}/src/meshcore-cli && {SCRIPT} {tmp_path}/state/{lock}meshcore-cli.lock .venv/bin/meshcli "
            f"-t 127.0.0.1 -p {port}; stty sane 2>/dev/null")


def test_the_tool_command_is_the_page_radios_instance(tmp_path, web):
    # red-before (f2ae0e36): r2's page showed the template's "-p 5000" and the lock without r2's directory
    assert _command(tmp_path, web, "/stacks?radio=r2&open=meshcore") == _expected(tmp_path, "r2/", 5133)


def test_each_radios_page_shows_its_own_port(tmp_path, web):
    # meshcore on r2 and r3: each page its own instance's port and lock
    for rid, port in (("r2", 5133), ("r3", 5266)):
        got = _command(tmp_path / rid, web, f"/stacks?radio={rid}&open=meshcore", running=("r2", "r3"))
        assert got == _expected(tmp_path / rid, f"{rid}/", port), rid


def test_without_a_page_radio_the_tool_command_is_the_stacks_own(tmp_path, web):
    # unchanged: no page radio, no instance (as the stack's own command) -> the template's values
    assert _command(tmp_path, web, "/stacks?open=meshcore") == _expected(tmp_path, "", 5000)


def test_a_radios_page_lists_no_tools_of_a_stack_running_only_on_another_radio(tmp_path, web):
    # red-before (747d4a63): r3's page listed meshcore-cli while meshcore ran only on r2 (the stack-wide running state)
    doc = _page(tmp_path, web, "/stacks?radio=r3&open=meshcore", running=("r2",))
    assert doc.by_id("stack-mode-meshcore") is not None          # anchor: meshcore's own row body is on the page
    assert doc.by_id("runc-meshcore-cli") is None


def test_a_stopped_stack_lists_no_tools(tmp_path, web):
    # unchanged: meshcore runs nowhere -> no tools, on a radio's page and without one
    for n, query in enumerate(("/stacks?radio=r2&open=meshcore", "/stacks?open=meshcore")):
        doc = _page(tmp_path / str(n), web, query, running=())
        assert doc.by_id("stack-mode-meshcore") is not None, query  # anchor: meshcore's own row body is on the page
        assert doc.by_id("runc-meshcore-cli") is None, query
