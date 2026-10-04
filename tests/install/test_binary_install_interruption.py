"""A binary re-install interrupted at every durable write is unwound, recognised and retried.

The binary transaction (`binary_install.py`, driven by `service_binary_ops.binary_install`) opens
`state/binary/install.journal.json` before it changes anything, moves the live file into
`state/binary/.backup-<txn>/`, publishes the staged one, writes the receipt, commits the journal,
drops the backups and clears the journal. The journal and receipt go through `runtime_fs`; the
moves use the module's own `os`/`shutil`, which the harness gates the same way. Each case fails
ONE write (disk full, I/O error, Ctrl-C) during a re-install over a working v1 and proves: no
journal recovery cannot read is left (`binary_recover`, which the next install runs first,
finishes it), the box holds v1 or v2 whole with its own receipt — never a mix, a backup or a
staging directory — and the same install then succeeds to the bytes an uninterrupted one writes.
"""
from __future__ import annotations

import json
import os

import pytest

from interrupts import FAILURES, durable_writes, run_interrupted, tree
from lhpc.core import binary_install as bi
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

pytestmark = pytest.mark.safety("binary-transaction")

BIN = "src/loraham-daemon/loraham_daemon/loraham_daemon"
RECEIPT = "state/binary/daemon.json"
RAW = [(bi, "os.replace"), (bi, "os.makedirs"), (bi, "os.unlink"), (bi, "shutil.rmtree")]
LOCKS = ("state/locks",)
# The re-install's durable writes, in order (service_binary_ops.py `_binary_install_locked`).
POINTS = [
    ("write_marker", "state/locks/controller-task-admission.owner"),
    ("write_marker", "state/locks/source-txn-index.owner"),
    ("write_marker", "state/locks/source.src-radiolib.owner"),
    ("write_marker", "state/locks/source.src-loraham-daemon.owner"),
    ("unlink", "state/locks/source-txn-index.owner"),
    ("mkdir", "state"),
    ("mkdir", "state/binary"),
    ("write_marker", "state/binary/install.journal.json"),            # open: prepared
    ("mkdir", "state/binary"),
    ("write_marker", "state/binary/install.journal.json"),            # publishing
    ("os.makedirs", "state/binary/.backup-*"),
    ("os.replace", BIN),                                              # live -> backup
    ("os.makedirs", "src/loraham-daemon/loraham_daemon"),
    ("os.replace", "state/lhpc-binary-*/stage/" + BIN),               # staged -> live
    ("mkdir", "state/binary"),
    ("write_marker", RECEIPT),
    ("mkdir", "state/binary"),
    ("write_marker", "state/binary/install.journal.json"),            # committed
    ("shutil.rmtree", "state/binary/.backup-*"),
    ("unlink", "state/binary/install.journal.json"),                  # cleared
    ("unlink", "state/locks/source.src-loraham-daemon.owner"),
    ("unlink", "state/locks/source.src-radiolib.owner"),
    ("unlink", "state/locks/controller-task-admission.owner"),
]


@pytest.fixture
def box(monkeypatch, stub_pipeline):
    """`box(root) -> (svc, install)`: the daemon installed from a binary once (v1); `install()`
    re-installs it with the bytes the next staged artifact carries (v2)."""
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    monkeypatch.setattr(bi, "run_probe", lambda paths, argv: "ok")
    version = ["v1"]

    def stage(tar, stage_dir, roots):
        os.makedirs(os.path.dirname(os.path.join(stage_dir, BIN)), exist_ok=True)
        with open(os.path.join(stage_dir, BIN), "w") as fh:
            fh.write(version[0])
        return [BIN]
    monkeypatch.setattr(bi, "validate_and_extract", stage)

    def make(root):
        version[0] = "v1"           # per box: an earlier box's re-install has staged v2 already
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=root))
        stub_pipeline(svc, download=lambda entry, path: None)
        assert svc.binary_install("daemon", apply=True).ok
        assert (root / BIN).read_bytes() == b"v1"
        version[0] = "v2"
        return svc, lambda: svc.binary_install("daemon", apply=True)
    return make


def _receipt(root):
    """The receipt minus its install time."""
    data = json.loads((root / RECEIPT).read_text())
    data.pop("installed_at")
    return data


def test_the_reinstall_writes_exactly_the_pinned_points(tmp_path, box):
    svc, install = box(tmp_path)
    with durable_writes(tmp_path, raw=RAW) as log:
        assert install().ok
    assert log == POINTS


@pytest.mark.parametrize("failure", sorted(FAILURES))
@pytest.mark.parametrize("point", range(len(POINTS)), ids=[f"{w}:{p}" for w, p in POINTS])
def test_an_interrupted_reinstall_recovers_and_retries(tmp_path, box, point, failure):
    done_root = tmp_path / "uninterrupted"
    _svc, done = box(done_root)
    v1_receipt = _receipt(done_root)
    assert done().ok
    v2_receipt = _receipt(done_root)
    want_after = tree(done_root, skip=LOCKS + (RECEIPT,))

    root = tmp_path / "interrupted"
    svc, install = box(root)
    assert ((root / BIN).read_bytes(), _receipt(root)) == (b"v1", v1_receipt)    # v1 -> v2
    run_interrupted(root, install, fail_at=point, exc=FAILURES[failure], raw=RAW)

    # (a) never a journal recovery cannot read; recovery (the next install's first step) finishes it
    assert bi.read_journal(svc._paths)[1] in ("absent", "valid")
    assert svc.binary_recover()[0]
    # (b) v1 or v2 whole, each with its own receipt; no journal, backup or staging directory left
    state = tree(root, skip=LOCKS + (RECEIPT,))
    assert not [p for p in state if p.startswith(("state/binary/.backup-", "state/lhpc-binary-"))
                or p == "state/binary/install.journal.json"]
    assert (state[BIN], _receipt(root)) in ((b"v1", v1_receipt), (b"v2", v2_receipt))
    assert svc.binary_receipt_state("daemon")[0] == "valid"
    # (c) the retry lands where an uninterrupted install does
    assert install().ok
    assert tree(root, skip=LOCKS + (RECEIPT,)) == want_after and _receipt(root) == v2_receipt
