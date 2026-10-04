"""The stateless stages of a Settings save (`ControllerService.save_config_bundle`).

NORMALIZE turns the submitted names into identified parameters and requested changes, and plans
which stored keys they set or clear; it runs before the config lock and learns about the stacks
only through the values and callables it is given. DECIDE for a store file (`overlay`) is called
inside the transaction with the file read there. Reading the current state under the lock, the
rechecks, the restart-marker decision and the journalled commit stay in `service_params.py`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from . import validators


class Change(NamedTuple):
    """One submitted value, identified: run (`"r"`) or file (`"f"`) param of a component.
    `value` is the validated value, or the submitted blank as is (never validated)."""
    kind: str
    comp: object
    param: object
    value: str


@dataclass(frozen=True)
class Switch:
    """A STACK-LEVEL switch: one per owner stack, stored under `key(sid)` in the band-less file,
    whatever shape (flat or component-scoped) its key was submitted in. A submitted value equal to
    `kept` (stripped, lower-case) is kept; any other value reads as `other`. Only a deviation from
    `default(stacks, sid)` is stored; a removed key means the default."""
    name: str
    kept: str
    other: str
    matches: Callable[[str], bool]
    default: Callable[..., str]
    key: Callable[[str], str]


def normalize_values(values: dict, resolve, *, own_ids, optional_ids, stack_of,
                     refused) -> tuple[list, dict, list]:
    """Validate a submission's values → (changes, autostart, errors); nothing is written here.

    `resolve(kind, key)` → (component, param, err) for kind "run"/"file"; `own_ids` the target's
    own component ids; `optional_ids` the components with an autostart flag; `stack_of(comp_id)`
    names a dependency's stack; `refused(component, param)` → a refusal text for a run param the
    generic save may not write ("" = allowed)."""
    errors: list[str] = []
    # Each submitted value carries component identity: a run value key is the API key
    # (`name`/`component.name`); a file value key is `file_<apikey>`. `_param_ref` resolves it to
    # (component, param) and REJECTS an unqualified duplicate — so colliding names never flatten.
    clean_params: list = []      # Change(kind 'r'|'f', component, param, value)
    clean_auto: dict = {}        # autostart_<id> -> "on"/""  (stack target only, flat)
    # `_param_ref` also resolves DEPENDENCY components' params (the start-override channel);
    # a persisted config write must stay in the component's OWN stack store, so anything the
    # dependency fallback resolved is refused here with a pointer to the right stack.

    def _own(c, key):
        if c.id in own_ids:
            return True
        errors.append(f"{key!r} belongs to dependency component '{c.id}' — save it on its "
                      f"own stack ('{stack_of(c.id)}')")
        return False
    for key, value in values.items():
        if key.startswith("autostart_"):
            if key[len("autostart_"):] in optional_ids:
                clean_auto[key] = "on" if str(value) in ("on", "1", "true", "yes") else ""
            else:
                errors.append(f"unknown config field: {key!r}")
            continue
        kind, name = ("f", key[len("file_"):]) if key.startswith("file_") else ("r", key)
        c, p, err = resolve("file" if kind == "f" else "run", name)
        if err:
            errors.append(f"unknown config field: {key!r}" if err.startswith("unknown") else err)
            continue
        if not _own(c, key):
            continue
        if kind == "r" and (why := refused(c, p)):
            errors.append(why)                          # never clearable via generic config
            continue
        v = str(value)
        if v.strip() == "":
            # BLANK = "clear this override / use the default" — never validated
            # as a literal value (an empty txpower/frequency is not an error).
            clean_params.append(Change(kind, c, p, v))
            continue
        try:
            clean_params.append(Change(kind, c, p, validators.validate_param(p, v)))
        except validators.ValidationError as exc:
            errors.append(str(exc))
    return clean_params, clean_auto, errors


def normalize_remotes(remotes: dict, *, target: str, allowed, stacks,
                      declarers) -> tuple[dict, list, list]:
    """Validate a remote-override submission → (patch, notes, errors).

    `allowed` the component ids of `target` that declare a source remote; `stacks()` every stack
    (read only when a remote validated); `declarers(source_path)` every component id that declares
    that checkout."""
    errors: list[str] = []
    # A remote submission is a PATCH for THIS stack's own source components only (enforced in
    # the service, not the web form): validated non-blank -> set, blank -> clear that
    # component's override. A component id not declared by `target` (unknown, another stack's,
    # or one without a source remote) is REJECTED. Other stacks' overrides are untouched.
    remote_patch: dict = {}
    for cid, url in remotes.items():
        try:
            vid = validators.path_component(cid, field="component id")
        except validators.ValidationError as exc:
            errors.append(str(exc))
            continue
        if vid not in allowed:
            errors.append(f"remote override not allowed for {vid!r} — not a source "
                          f"component of '{target}'")
            continue
        try:
            remote_patch[vid] = validators.remote_url(url or "", field="remote")   # "" clears
        except validators.ValidationError as exc:
            errors.append(str(exc))
    # ONE remote per shared checkout: a submission giving two components of the same
    # source path DIFFERENT remotes is rejected whole; a coherent value is expanded
    # ATOMICALLY to every declarer of that path (explicitly disclosed), so divergence
    # can never be saved — not even for consumers in other stacks.
    remote_notes: list = []
    if remote_patch:
        comp_index = {c.id: c for st in stacks() for c in st.components}
        by_path: dict = {}
        for vid, vurl in remote_patch.items():
            c = comp_index.get(vid)
            if c is None or c.source is None:
                continue
            by_path.setdefault(c.source.path, {})[vid] = vurl
        for pth, vals in by_path.items():
            if len(set(vals.values())) > 1:
                errors.append(f"conflicting remotes submitted for shared source {pth!r} "
                              f"({', '.join(sorted(vals))}) — one checkout has ONE remote")
                continue
            url = next(iter(vals.values()))
            group = declarers(pth)
            extra = sorted(set(group) - set(vals))
            for did in group:
                remote_patch[did] = url
            if extra:
                remote_notes.append(f"shared checkout {pth}: the same remote was applied "
                                    f"to {', '.join(extra)}")
    return remote_patch, remote_notes, errors


def plan_store(changes, autostart: dict, *, key_of, default_of, switches, stacks, sid: str,
               banded: bool) -> tuple[dict, set, dict, set, dict]:
    """Which stored keys the save sets and clears → (stack_set, stack_remove, bandless_set,
    bandless_remove, switched): the stack's own (banded) file, the band-less file (empty unless
    `banded`), and each touched switch name → (wanted, default). `key_of(change)` is the
    persisted key, `default_of(change)` the canonical default it is compared with."""
    to_set: dict = {}
    to_remove: set = set()
    # Autostart is a STACK-LEVEL flag: it must live in the BAND-LESS stack file —
    # `_run_order` reads it band-independently. (Live finding: stored in the
    # band-suffixed file, the option never took effect for band-switchable
    # stacks like kiss.)
    auto_set = {k: av for k, av in autostart.items() if av == "on"}
    auto_remove = {k for k, av in autostart.items() if av != "on"}
    for change in changes:
        key = key_of(change)
        if str(change.value) == default_of(change):
            to_remove.add(key)                                          # at default -> not persisted
        else:
            to_set[key] = change.value                                  # override -> persisted
    switched: dict = {}
    for sw in switches:
        now, want = False, ""
        for _k in [k for k in to_set if sw.matches(k)]:
            # The WANTED state comes from the VALUE, never from which bucket the key landed in.
            # Reading it as "in to_set => on" made `use_gps=""` — an override that differs from
            # the default and so lands in to_set — look like "on": it matched the current "on",
            # was seen as no change, skipped the running-stack refusal, and then disabled GPS,
            # because every reader compares against the literal "on".
            now = True
            want = sw.kept if str(to_set.pop(_k)).strip().lower() == sw.kept else sw.other
        default = sw.default(stacks, sid)
        for _k in [k for k in to_remove if sw.matches(k)]:
            to_remove.discard(_k)
            now, want = True, default                   # removing the key = back to default
        if now:
            # Store the CANONICAL value: only a deviation FROM THE DEFAULT is an override;
            # the default itself is cleared, so the file never holds a third state.
            key = sw.key(sid)
            if want != default:
                auto_set[key] = want
                auto_remove.discard(key)
            else:
                auto_remove.add(key)
                auto_set.pop(key, None)
            switched[sw.name] = (want, default)
    if not ((auto_set or auto_remove) and banded):
        to_set.update(auto_set)                         # unbanded: one file holds everything
        to_remove |= auto_remove
        auto_set, auto_remove = {}, set()
    return to_set, to_remove, auto_set, auto_remove, switched


def overlay(current: dict, setv: dict, rmv) -> dict:
    """DECIDE a store file's new content from what was read under the lock: overlay the override
    keys (keeping daemon-profile dp_*, other bands + unrelated manual scalars), then drop the
    at-default keys."""
    merged = dict(current)
    merged.update(setv)
    for k in rmv:
        merged.pop(k, None)
    return merged
