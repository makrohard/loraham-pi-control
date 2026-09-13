"""Shared start-path probes for the ordinary tests (core/ and stacks/), beside `htmlq` and
`repo_paths`: importable as a plain module because pytest puts this directory on `sys.path`.

    from seams import LifecycleSeam, outcomes, seed_built
"""


class LifecycleSeam(Exception):
    """Raised at the first lifecycle side effect (daemon ensure / config write) — proves whether a
    start reached the seam or was blocked BEFORE any side effect, without launching anything."""


def seed_built(tmp_path, *rels):
    """Installed AND built, through the real filesystem seams `is_installed`/`is_built` read: the
    source directory exists and the declared `bin` inside it is a file."""
    for rel in rels:
        (tmp_path / "src" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / rel).write_text("#!bin")


def outcomes(res):
    """What a start actually produced — an `any(...)` assertion otherwise reports only False,
    which is unusable when the run that fails is a CI runner you cannot attach to."""
    return [(r.component, getattr(r.outcome, "name", r.outcome), (r.summary or "")[:90])
            for r in res.results]
