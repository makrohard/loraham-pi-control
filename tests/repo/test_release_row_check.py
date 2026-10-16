"""tools/release-row-check.py: when a controller release needs the real MeshCom row on the Zero.

One throwaway repository per case: a release tag, then one change. Each of the four clauses is a
case of its own, and a docs-only change stays on the virtual row."""

import importlib.util
import subprocess

import pytest
import repo_paths

TOOL = repo_paths.REPO / "tools" / "release-row-check.py"
OLD, NEW = "a" * 40, "b" * 40


def _tool():
    spec = importlib.util.spec_from_file_location("release_row_check", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest(daemon=OLD, radiolib=OLD, bridge=OLD, runner=OLD, txmode="managed", other=OLD):
    return f'''[[stack]]
id = "daemon"
[[stack.component]]
id = "loraham-daemon"
run_argv = ["loraham_daemon", "--tx-mode", "{txmode}"]
[stack.component.source]
path = "src/loraham-daemon"
pin_commit = "{daemon}"
[[stack.component]]
id = "radiolib"
[stack.component.source]
path = "src/RadioLib"
pin_commit = "{radiolib}"

[[stack]]
id = "meshcom"
[[stack.component]]
id = "meshcom-bridge"
[stack.component.source]
path = "src/meshcom-loraham-bridge"
pin_commit = "{bridge}"
[[stack.component]]
id = "meshcom-qemu"
[stack.component.source]
path = "src/meshcom-qemu-raspi"
pin_commit = "{runner}"

[[stack]]
id = "meshtastic"
[[stack.component]]
id = "meshtasticd"
[stack.component.source]
path = "src/meshtastic-firmware"
pin_commit = "{other}"
'''


CONFIG = 'X = 1\nHW_SETUPS: dict = {{\n    "loraham": ("LoRaHAM", {{"433": "{p}"}}),\n}}\nY = 2\n'


def _git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(repo, files, msg):
    for path, text in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def lab(tmp_path):
    """A controller repo tagged `base`, and a bridge clone with one backend and one docs commit."""
    ctl, bridge = tmp_path / "ctl", tmp_path / "src" / "src" / "meshcom-loraham-bridge"
    for r in (ctl, bridge):
        r.mkdir(parents=True)
        _git(r, "init", "-q")
    b0 = _commit(bridge, {"src/backend/loraham/b.cpp": "1\n", "README.md": "r\n"}, "b0")
    b_docs = _commit(bridge, {"README.md": "r2\n"}, "docs")
    b_backend = _commit(bridge, {"src/backend/loraham/b.cpp": "2\n"}, "backend")
    _commit(ctl, {"lhpc/data/manifest.example.toml": _manifest(bridge=b0),
                  "lhpc/core/config.py": CONFIG.format(p="loraham"),
                  "lhpc/core/daemon_control.py": "A = 1\n", "docs/x.md": "x\n"}, "base")
    _git(ctl, "tag", "base")
    return ctl, tmp_path / "src", {"b0": b0, "docs": b_docs, "backend": b_backend}


def _check(lab, files):
    ctl, src, _ = lab
    _commit(ctl, files, "candidate")
    return _tool().reasons(str(ctl), "base", "HEAD", str(src))


def test_a_docs_only_release_stays_on_the_virtual_row(lab):
    assert _check(lab, {"docs/x.md": "changed\n"}) == []


@pytest.mark.parametrize("pin", ["daemon", "radiolib"])
def test_clause_i_a_daemon_or_radiolib_pin_move_needs_the_real_row(lab, pin):
    got = _check(lab, {"lhpc/data/manifest.example.toml":
                       _manifest(bridge=lab[2]["b0"], **{pin: NEW})})
    assert [r for r in got if r.startswith("(i) ")] and len(got) == 1


@pytest.mark.parametrize("files", [
    {"lhpc/core/daemon_control.py": "A = 2\n"},
    {"lhpc/core/config.py": CONFIG.format(p="uputronics-ce0")},
])
def test_clause_ii_the_radio_configuration_needs_the_real_row(lab, files):
    assert [r for r in _check(lab, files) if r.startswith("(ii) ")]


def test_clause_ii_reads_the_daemon_component_but_not_the_rest_of_config_py(lab):
    assert _check(lab, {"lhpc/core/config.py": CONFIG.format(p="loraham").replace(
        "Y = 2", "Y = 3")}) == []
    got = _check(lab, {"lhpc/data/manifest.example.toml":
                       _manifest(bridge=lab[2]["b0"], txmode="direct")})
    assert got == ["(ii) the daemon component in the manifest changed"]


@pytest.mark.parametrize("to, real", [("backend", True), ("docs", False)])
def test_clause_iii_only_a_bridge_move_into_its_hardware_backend(lab, to, real):
    got = _check(lab, {"lhpc/data/manifest.example.toml": _manifest(bridge=lab[2][to])})
    assert bool([r for r in got if r.startswith("(iii) ")]) is real


def test_clause_iii_an_unreadable_bridge_diff_counts_as_touched(lab):
    got = _check(lab, {"lhpc/data/manifest.example.toml": _manifest(bridge="c" * 40)})
    assert [r for r in got if r.startswith("(iii) ")]


def test_clause_iv_a_runner_pin_move_needs_the_real_row(lab):
    got = _check(lab, {"lhpc/data/manifest.example.toml":
                       _manifest(bridge=lab[2]["b0"], runner=NEW)})
    assert got == ["(iv) src/meshcom-qemu-raspi pin moved"]


def test_another_stacks_pin_move_stays_on_the_virtual_row(lab):
    """The negative of clauses (i) and (iv): a pin outside the daemon, RadioLib, bridge and runner."""
    assert _check(lab, {"lhpc/data/manifest.example.toml":
                        _manifest(bridge=lab[2]["b0"], other=NEW)}) == []


def test_the_real_manifest_names_the_daemon_pin_the_checker_watches():
    """The checker's daemon/RadioLib paths are the ones the shipped manifest pins."""
    import tomllib
    pins = _tool()._pins(tomllib.loads(
        (repo_paths.REPO / "lhpc" / "data" / "manifest.example.toml").read_text()))
    assert {"src/loraham-daemon", "src/RadioLib"} <= set(pins)
    assert {"src/meshcom-loraham-bridge", "src/meshcom-qemu-raspi"} <= set(pins)


def test_the_command_prints_the_verdict_and_exits_2_on_an_unknown_ref(lab, capsys):
    ctl, src, _ = lab
    _commit(ctl, {"docs/x.md": "y\n"}, "c")
    tool = _tool()
    assert tool.main(["base", "HEAD", "--repo", str(ctl), "--src-root", str(src)]) == 0
    assert capsys.readouterr().out.strip() == "virtual-row"
    assert tool.main(["nope", "HEAD", "--repo", str(ctl)]) == 2
