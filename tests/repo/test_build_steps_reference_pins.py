"""No build step may carry a commit as a literal — a repository fetched by ref uses `{pin:<path>}`.

The manifest's build steps are read RAW (TOML, before the parser resolves tokens): a 40-hex
token in an argv is exactly the drift this guards against (R8: `setup.sh --ref 674413c…` sat next
to a pin that had moved on, and the artifact was labelled with a commit it did not contain).
"""
import pathlib
import re
import tomllib

import repo_paths

MANIFEST = pathlib.Path(repo_paths.REPO) / "lhpc" / "data" / "manifest.example.toml"
HEX40 = re.compile(r"\A[0-9a-f]{40}\Z")


def _steps():
    data = tomllib.loads(MANIFEST.read_text())
    for st in data.get("stack", []):
        for c in st.get("component", []):
            for i, step in enumerate(c.get("build_steps", [])):
                yield f"{st['id']}/{c['id']}#{i}", [str(t) for t in step.get("argv", [])]


def test_no_build_step_carries_a_commit_literal():
    hits = [f"{where}: {tok}" for where, argv in _steps() for tok in argv if HEX40.match(tok)]
    assert not hits, ("a build step fetches a commit by literal — use {pin:<source path>} so the "
                      "step follows the pin:\n" + "\n".join(hits))


def test_every_pin_token_names_a_pinned_source():
    data = tomllib.loads(MANIFEST.read_text())
    paths = {c["source"]["path"] for st in data["stack"] for c in st.get("component", [])
             if c.get("source", {}).get("pin_commit")}
    tokens = [(where, tok) for where, argv in _steps() for tok in argv if tok.startswith("{pin:")]
    assert tokens, "the meshcom-qemu setup step is expected to use {pin:src/MeshCom-Firmware}"
    bad = [f"{w}: {t}" for w, t in tokens if t[len("{pin:"):-1] not in paths]
    assert not bad, bad
