"""The slow-target budget rule (plans/PLAN-F43.md §4a, §7, §8), in ONE place.

Two consumers read it: the ordinary suite (`tests/install/test_slow_target_budget.py`) checks
the checked-in baseline, and the `slow-build` lane (`testlab/tests/slowbuild`) checks a
release's fresh evidence. It lives in the product for the same reason as `build_regression`:
the lane cannot import a test module, and two copies of a rule drift.

Nothing here runs a command or touches the runtime root: it reads the limits the product
declares, computes entry keys and compares numbers.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import tomllib

OPS = ("build", "selfupdate-helper", "selfupdate-pip", "cli-venv", "deb-fetch", "clone",
       "checkout")
SOURCES = ("zero2w", "throttled-ci")
SELFUPDATE_COMPONENT = "lhpc-selfupdate"
CLI_VENV_COMPONENT = "meshtastic-cli-venv"
_KEY_RE = re.compile(r"(pin|deb|deps):\S+")
_MINOR_RE = re.compile(r"v?(\d+)\.(\d+)\.")
# `lhpc`: the LHPC tree an entry was measured on, the short SHA mandatory: a release tag
# `vX.Y.Z (sha7)`, or a release candidate's ref `release/<name> (sha7)` (row A measures the
# candidate before its tag exists). A self-update entry names the update it timed,
# `<from tag> -> <to tag or ref> (sha7)`. The minor is the (target) tag's X.Y, or the first X.Y.Z
# in a candidate ref's name.
_REL = r"(?:v\d+\.\d+\.\d+\S*|release/\S+)"
_LHPC_RE = re.compile(rf"(?:v\d+\.\d+\.\d+\S* -> )?{_REL} \([0-9a-f]{{7,40}}\)")
_REF_MINOR_RE = re.compile(r"(\d+)\.(\d+)\.\d")
# The first release whose self-update helper prints `[selfupdate] pip sync <n> s` (L4). An update
# TO it runs the previous release's helper, which cannot print the line, on the Zero and in the
# lane alike: L4 is measurable from the next release on (docs/maintenance.md).
PIP_SYNC_SINCE = "0.12.0"


class LimitUnavailable(Exception):
    """The product does not declare this limit on this tree."""


# ---- the limits (§2), read from the product, never restated --------------------------------

def _l1_stall():
    try:
        from . import progress  # type: ignore[attr-defined]
    except ImportError:
        raise LimitUnavailable("L1 (build stall) is lhpc.core.progress.STALL_S, which this tree "
                               "does not have (F42)") from None
    return float(progress.STALL_S)


def _l3_helper():
    from . import updater_units
    unit = updater_units.render(updater_units.HELPER_UNIT, "/r", "/c", "/v")
    m = re.search(r"^TimeoutStartSec=(\d+)\s*$", unit, re.MULTILINE)
    if not m:
        raise LimitUnavailable("the rendered self-update helper unit carries no TimeoutStartSec=")
    return float(m.group(1))


def _l4_pip():
    from .services import ControllerService
    return float(ControllerService._PIP_SYNC_TIMEOUT_S)


def _l7a_clone():
    from .install import Installer
    return float(Installer._CLONE_TIMEOUT_S)


def _l7b_checkout():
    from .install import Installer
    return float(Installer._CHECKOUT_TIMEOUT_S)


# op -> (limit name, reader). `build` is budgeted on its longest quiet gap (L1); the others on
# the op's wall clock.
LIMITS = {
    "build": ("L1 build stall", _l1_stall),
    "selfupdate-helper": ("L3 self-update helper TimeoutStartSec", _l3_helper),
    "selfupdate-pip": ("L4 self-update pip sync", _l4_pip),
    "clone": ("L7a clone", _l7a_clone),
    "checkout": ("L7b checkout", _l7b_checkout),
}


def limit(op: str) -> float:
    """The product's limit in seconds for `op`; LimitUnavailable, naming the limit and where it
    is read from, when this tree has none or the reader cannot reach it."""
    if op not in LIMITS:
        raise LimitUnavailable(f"no limit is mapped for op {op!r}")
    name, reader = LIMITS[op]
    try:
        return reader()
    except LimitUnavailable as exc:
        raise LimitUnavailable(f"{name} (read by slow_target.{reader.__name__}): {exc}") from None
    except (ImportError, AttributeError) as exc:
        raise LimitUnavailable(f"{name} (read by slow_target.{reader.__name__}): the product "
                               f"does not declare it ({exc})") from None


def quantity(entry: dict) -> float:
    """The number an entry is budgeted on: `quiet_s` for a build (L1), else `seconds`."""
    return float(entry["quiet_s"] if entry["op"] == "build" else entry["seconds"])


# ---- keys (§7a) ----------------------------------------------------------------------------

def deps_key(pyproject_text: str) -> str:
    """`deps:<sha256>` of LHPC's dependency set — build requires, dependencies and
    requires-python, sorted. `version` is not part of it, so a release commit leaves it alone."""
    doc = tomllib.loads(pyproject_text)
    proj = doc.get("project", {})
    blob = json.dumps({"build-system.requires": sorted(doc.get("build-system", {})
                                                      .get("requires", [])),
                       "dependencies": sorted(proj.get("dependencies", [])),
                       "requires-python": proj.get("requires-python", "")}, sort_keys=True)
    return "deps:" + hashlib.sha256(blob.encode()).hexdigest()


def _fetch_version(comp) -> str:
    """The release version a fetched component's build step downloads (the argument after the
    destination of its `*-fetch.sh` step)."""
    for step in comp.build_steps or ():
        argv = [str(a) for a in step.get("argv") or ()]
        for i, tok in enumerate(argv):
            if tok.endswith("-fetch.sh") and len(argv) > i + 2:
                return argv[i + 2]
    return ""


def current_key(component: str, op: str, stacks, pyproject_text: str,
                fetched: str = "") -> str:
    """The key an entry of (component, op) must carry to be FRESH on this tree. "" when it
    cannot be known here (a `deb-fetch` key is the version fetched at the run)."""
    if op in ("selfupdate-helper", "selfupdate-pip"):
        return deps_key(pyproject_text)
    if op == "deb-fetch":
        return f"deb:{fetched}" if fetched else ""
    comps = {c.id: c for st in stacks for c in st.components}
    comp = comps.get("meshtastic" if op == "cli-venv" else component)
    if comp is None:
        return ""
    if comp.source is not None and comp.source.pin_commit:
        return f"pin:{comp.source.pin_commit}"
    version = _fetch_version(comp)
    return f"deb:{version}" if version else ""


# ---- coverage (§5 change 2a) ----------------------------------------------------------------

def required(stacks, excluded) -> list[tuple[str, str]]:
    """Every (component, op) the baseline must hold: `build` for each component with build
    steps, `clone` + `checkout` once per pinned source tree (named after its first component
    that is not excluded), `deb-fetch` for each fetched release, and the self-update and CLI-venv
    ops. Excluded components are left out."""
    out: list[tuple[str, str]] = []
    trees: set[str] = set()
    for st in stacks:
        for c in st.components:
            if c.id in excluded:
                continue
            if c.build_steps:
                out.append((c.id, "build"))
            if c.source is not None and c.source.pin_commit and c.source.path not in trees:
                trees.add(c.source.path)
                out += [(c.id, "clone"), (c.id, "checkout")]
            if getattr(c, "release_repo", ""):
                out.append((c.id, "deb-fetch"))
    out += [(SELFUPDATE_COMPONENT, "selfupdate-helper"), (SELFUPDATE_COMPONENT, "selfupdate-pip"),
            (CLI_VENV_COMPONENT, "cli-venv")]
    return out


def tree_components(stacks, component: str) -> list[str]:
    """Every component sharing `component`'s source tree: whichever of them adopts the tree
    writes the clone/checkout log lines."""
    comps = [c for st in stacks for c in st.components]
    own = next((c for c in comps if c.id == component), None)
    if own is None or own.source is None:
        return [component]
    return [c.id for c in comps if c.source is not None and c.source.path == own.source.path]


BOOTSTRAP = "bootstrap: no row A yet"


def bootstrap_reason(measured, unmeasured) -> str:
    """The baseline's BOOTSTRAP state: no `[[measured]]` entry at all (its first introduction,
    before any row A). Then the visible skip reason naming every unmeasured operation; "" as
    soon as one entry exists, and coverage is enforced again."""
    if measured:
        return ""
    return f"{BOOTSTRAP} — {len(unmeasured)} operations unmeasured: " + ", ".join(unmeasured)


# ---- the file ------------------------------------------------------------------------------

def entry_errors(entry: dict) -> list[str]:
    """Why an entry is not well-formed (§7); [] when it is."""
    errs = []
    if not isinstance(entry.get("component"), str) or not entry.get("component"):
        errs.append("component missing")
    if entry.get("op") not in OPS:
        errs.append(f"op {entry.get('op')!r} not one of {OPS}")
    sec = entry.get("seconds")
    if isinstance(sec, bool) or not isinstance(sec, (int, float)) or sec <= 0:
        errs.append("seconds must be a positive number")
    if entry.get("op") == "build":
        q = entry.get("quiet_s")
        if isinstance(q, bool) or not isinstance(q, (int, float)) or q < 0:
            errs.append("a build entry needs quiet_s (longest gap without progress)")
    if not isinstance(entry.get("key"), str) or not _KEY_RE.fullmatch(entry.get("key", "")):
        errs.append("key must be pin:<..>, deb:<..> or deps:<..>")
    if entry.get("source") not in SOURCES:
        errs.append(f"source must be one of {SOURCES}")
    if not _LHPC_RE.fullmatch(str(entry.get("lhpc", ""))):
        errs.append("lhpc must name the release tag or candidate ref and its short SHA, e.g. "
                    "'v0.12.0 (6ff1937)' or 'release/0.12.0-bundle (6ff1937)'")
    if not isinstance(entry.get("date"), _dt.date):
        errs.append("date must be a TOML date")
    for f in ("host", "evidence"):
        if not isinstance(entry.get(f), str) or not entry.get(f):
            errs.append(f"{f} missing")
    return errs


def minor(entry: dict):
    """The minor of the entry's `lhpc` label (an update's: its target's): the tag's X.Y, or the
    first X.Y.Z in a candidate ref's name; None when it names none (such an entry is only ever
    FRESH, never carried)."""
    label = str(entry.get("lhpc", "")).split(" -> ")[-1]
    m = (_MINOR_RE.match(label) if label.startswith("v")
         else _REF_MINOR_RE.search(label.split(" ", 1)[0]) if label.startswith("release/")
         else None)
    return (int(m.group(1)), int(m.group(2))) if m else None


def version_minor(version: str):
    m = _MINOR_RE.match(version + ".")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _release(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", version)[:3])


def waiver(component: str, op: str, version: str) -> str:
    """Why (component, op) needs no measurement on a tree at `version`, or "". The one waiver:
    L4 (`selfupdate-pip`) up to and including the release that introduces its timing line
    (PIP_SYNC_SINCE) — nobody can measure it there. Excluded components never reach here:
    `required` leaves them out."""
    if (component, op) == (SELFUPDATE_COMPONENT, "selfupdate-pip") and \
            _release(version) <= _release(PIP_SYNC_SINCE):
        return (f"{component} {op} (L4): no evidence up to the introducing release "
                f"{PIP_SYNC_SINCE} — measured from the next release on")
    return ""


# ---- the rule (§8) -------------------------------------------------------------------------

def zero_baseline(entries, component: str, op: str, key: str, this_minor):
    """(Z, "fresh" | "carried" | "none") — the Zero entry with the current key, else the latest
    Zero entry of this (component, op) from this or the previous minor."""
    zero = [e for e in entries if e.get("source") == "zero2w"
            and e.get("component") == component and e.get("op") == op]
    fresh = [e for e in zero if key and e.get("key") == key]
    if fresh:
        return max(fresh, key=lambda e: e["date"]), "fresh"
    near = {this_minor, (this_minor[0], this_minor[1] - 1)}
    carried = [e for e in zero if minor(e) in near]
    if carried:
        return max(carried, key=lambda e: e["date"]), "carried"
    return None, "none"


def compare(component: str, op: str, lim: float, baseline, evidence, key: str,
            this_minor) -> tuple[list[str], str]:
    """§8 for one (component, op): (failures, summary line). `evidence` is the release run's
    fresh row-C entry, or None when the run produced none (a failure of its own)."""
    fails = []
    z, how = zero_baseline(baseline, component, op, key, this_minor)
    ci = [quantity(e) for e in baseline if e.get("source") == "throttled-ci"
          and e.get("component") == component and e.get("op") == op]
    e_q = quantity(evidence) if evidence is not None else None
    if evidence is None:
        fails.append(f"no row C evidence for {component} {op}: the slow-build run did not "
                     "measure it")
    seen = ([quantity(z)] if z else []) + ci + ([e_q] if e_q is not None else [])
    if seen and lim < 2 * max(seen):
        fails.append(f"{component} {op}: limit {lim:.0f} s < 2 x {max(seen):.0f} s measured")
    if how == "none":
        fails.append(f"no Zero baseline for {component} {op}: run row A")
    elif how == "carried" and e_q is not None and e_q > lim / 4:
        fails.append(f"pin moved and row C is near the budget: run row A ({component} {op}: "
                     f"{e_q:.0f} s > limit/4 = {lim / 4:.0f} s)")
    ratio = (f"{e_q / quantity(z):.2f}" if z and e_q is not None and quantity(z) > 0 else "n/a")
    summary = (f"{component} {op}: E={'n/a' if e_q is None else f'{e_q:.0f}'} s "
               f"Z={'n/a' if z is None else f'{quantity(z):.0f}'} s ({how}) E/Z={ratio} "
               f"limit={lim:.0f} s")
    return fails, summary


def calibration_failures(calibrations, workload: str, measured: dict) -> list[str]:
    """§4b: every part of the container's run of the fixed workload is at least as slow as the
    Zero's run of the SAME workload. Reads `[[calibration]]` entries only."""
    zero = [c for c in calibrations if c.get("source") == "zero2w"
            and c.get("workload") == workload]
    if not zero:
        return [f"uncalibrated: run the Zero row (no zero2w [[calibration]] for {workload})"]
    z = max(zero, key=lambda c: c.get("date"))
    return [f"the container is faster than the Zero at {part}: {measured.get(part)} s < "
            f"{z[part]} s — lower SLOW_CPUS"
            for part in ("cpu_s", "io_s", "mem_s")
            if not isinstance(measured.get(part), (int, float)) or measured[part] < z[part]]
