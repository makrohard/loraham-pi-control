"""The one build plan: which components a build covers, in which order, which sources it locks and
what its receipt records. Plain functions over explicit inputs, shared by the CLI build
(`ControllerService.build`), the console's detached build (`spawn_web_job`) and the detached
launcher (`build_launcher_runtime`), so changing any of these rules changes one place."""

from __future__ import annotations


def select(requested, stack) -> list:
    """The components a build of a target covers: `requested` (the target's runnable components)
    and, for a stack target (`stack`; None for a component target), every component of the stack
    with build steps — a library such as RadioLib is built by its stack's build. Only components
    with build steps are returned, in that order (`order` sorts them)."""
    chosen = list(requested)
    if stack is not None:
        chosen += [c for c in stack.components if c.build_steps and c not in chosen]
    return [c for c in chosen if c.build_steps]


def _rank(comp, by_id: dict, seen: frozenset = frozenset()) -> int:
    if comp.id in seen:
        return 0                                  # defensive: a cycle ranks flat
    deps = [d for d in (comp.build_requires or ()) if d in by_id]
    if not deps:
        return 0
    return 1 + max(_rank(by_id[d], by_id, seen | {comp.id}) for d in deps)


def order(comps: list) -> list:
    """`comps` with every `build_requires` provider before its consumers; stable otherwise. The
    rank counts only providers in `comps` (a provider dropped by a preflight does not count)."""
    by_id = {c.id: c for c in comps}
    return sorted(comps, key=lambda c: _rank(c, by_id))


def lock_sources(comps, by_id: dict) -> list[str]:
    """The sorted source paths a build of `comps` holds locked for its whole run: each
    component's own and every `build_requires` dependency's, transitively — a dependency without
    build steps (a pure checkout) included."""
    paths: set = set()
    todo = list(comps)
    seen: set = set()
    while todo:
        c = todo.pop()
        if c.id in seen:
            continue
        seen.add(c.id)
        if c.source:
            paths.add(c.source.path)
        todo += [by_id[d] for d in (c.build_requires or ()) if d in by_id]
    return sorted(paths)


def web_round(ordered: list, is_built) -> tuple[list, bool]:
    """What one round of the console's parallel per-component jobs may build, and whether a
    later round must follow: a provider and its consumer never build in the same round. While a
    provider in `ordered` is not built, the round is the unbuilt providers of the lowest rank;
    otherwise every component that is no provider (a built provider is not rebuilt)."""
    providers = {d for c in ordered for d in (c.build_requires or ())}
    by_id = {c.id: c for c in ordered}
    unbuilt = [c for c in ordered if c.id in providers and not is_built(c)]
    if unbuilt:
        low = min(_rank(c, by_id) for c in unbuilt)
        return [c for c in unbuilt if _rank(c, by_id) == low], True
    return [c for c in ordered if c.id not in providers], False


def consumed_sources(comp, by_id: dict, source_dir, binary: bool = False) -> list[tuple[str, str]]:
    """The `(id, source dir)` pairs a receipt records for `comp`: its own when it builds from a
    git source that no binary artifact can provide (`binary`: its marker ships inside the
    artifact and stays static, so every controller reads it the same way), and every
    `build_requires` dependency's, transitively, each once in first-seen order ("" for an id the
    manifest does not know). Empty for a component without a completion marker, or with neither
    (a fetched release such as graywolf, an artifact-capable component): its receipt is static.
    The one computation: the build writes it and `is_built` compares it."""
    own = bool(comp.source) and not binary
    if not comp.build_marker or not (own or comp.build_requires):
        return []
    ids = [comp.id]
    for cid in ids:                    # grows while walking: a dependency's own follow it
        dep = by_id.get(cid)
        ids += [d for d in ((dep.build_requires or ()) if dep is not None else ()) if d not in ids]
    return [(cid, str(source_dir(by_id[cid])) if cid in by_id else "")
            for cid in (ids if own else ids[1:])]


def consumed_lines(sources, run) -> str:
    """The receipt's lines, one `consumed <id> <sha>` per `(id, source dir)` pair. `run(argv)`
    returns `(returncode, stdout)`; an unreadable HEAD (no dir, not a repository, a failed git) is
    recorded as `unknown`, so the receipt matches only while it stays unreadable."""
    lines = []
    for cid, src in sources:
        sha = ""
        if src:
            rc, out = run(["git", "-C", src, "rev-parse", "HEAD"])
            sha = (out or "").strip() if rc == 0 else ""
        lines.append(f"consumed {cid} {sha or 'unknown'}\n")
    return "".join(lines)
