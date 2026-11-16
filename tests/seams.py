"""Shared start-path probes for the ordinary tests (core/ and stacks/), beside `htmlq` and
`repo_paths`: importable as a plain module because pytest puts this directory on `sys.path`.

    from seams import LifecycleSeam, entry_args, exe, outcomes, readable_heads, seed_built, write_own_rev
"""


def exe(spec, default):
    """argv[0] of a hand-built process of the spec: this install's interpreter when the spec names one (`exec_path`,
    resolved by the service), else `default`."""
    return spec.exec_path or default


def entry_args(spec):
    """The argv tokens naming the program an interpreter spec runs (`-m <entry>`), else none: a hand-built
    argv of the spec is [exec_name, *entry_args(spec), *all_args, ...]."""
    return ("-m", spec.entry) if spec.entry else ()


class LifecycleSeam(Exception):
    """Raised at the first lifecycle side effect (daemon ensure / config write) — proves whether a
    start reached the seam or was blocked BEFORE any side effect, without launching anything."""


def seed_built(tmp_path, *rels):
    """Installed AND built, through the real filesystem seams `is_installed`/`is_built` read: the
    source directory exists and the declared `bin` inside it is a file."""
    for rel in rels:
        (tmp_path / "src" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / rel).write_text("#!bin")


def seed_framing_driver(root):
    """A Reticulum node built at the pins: its venv carries the LoRa driver's RNode framing module
    (the pinned rns-lora-interface has it). Framing is on by default since 0.14.0, and the start
    gate refuses a built driver without it, so a box that starts Reticulum needs this."""
    drv = root / "src" / "reticulum" / ".venv" / "lib" / "python3.13" / "site-packages" / "loraham_rns"
    drv.mkdir(parents=True, exist_ok=True)
    (drv / "framing.py").write_text("")


def outcomes(res):
    """What a start actually produced — an `any(...)` assertion otherwise reports only False,
    which is unusable when the run that fails is a CI runner you cannot attach to."""
    return [(r.component, getattr(r.outcome, "name", r.outcome), (r.summary or "")[:90])
            for r in res.results]


def readable_heads(root, sha="a" * 40):
    """`FakeSystem(commands=…)` entries answering `git rev-parse HEAD` for every shipped
    component's checkout under `root`: a build receipt then records real revisions (an
    unreadable one, `unknown`, never reads built)."""
    from lhpc.core.manifest import load_manifest
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import CommandResult
    paths = Paths(runtime_root=root)
    return {("git", "-C", str(paths.resolve_source(c.source.path)), "rev-parse", "HEAD"):
            CommandResult(0, sha + "\n", "")
            for st in load_manifest() for c in st.components if c.source}


def write_own_rev(svc, comp):
    """The side file a SOURCE build of a binary-capable component writes beside its marker
    (its own checkout's revision; nothing for any other component), as `svc.build` writes it."""
    rev = svc._own_rev(comp)
    if rev is not None:
        rev[0].parent.mkdir(parents=True, exist_ok=True)
        rev[0].write_text(rev[1])

