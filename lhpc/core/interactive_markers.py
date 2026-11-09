"""An interactive app's "run" marker (K7: one per instance): state/interactive/<sid>@<rid>.show holds the band the
operator ran it on, so the dashboard shows its terminal-command block; <sid>.show is a stack without a hardware list.
The service's public entries (mark_interactive, interactive_band, dismiss_interactive, clear_stale_interactive) keep
their names and delegate here."""
from __future__ import annotations

from . import instances, runtime_fs, validators
from .model import RunState


def marker(svc, stack_id: str, inst=None):
    """An instance's own marker `<sid>@<rid>.show` (`<sid>.show` without one: no hardware list)."""
    sid = validators.path_component(stack_id, field="stack id")
    return svc._paths.under("state", "interactive", f"{instances.key(sid, inst)}.show")


def markers(svc, stack_id: str) -> list:
    """Every marker of the stack: a bare one, then the instances'."""
    idir = svc._paths.runtime_root / "state" / "interactive"
    bare = marker(svc, stack_id)
    return ([bare] if bare.exists() else []) + (sorted(idir.glob(f"{bare.stem}@r*.show")) if idir.exists() else [])


def band(svc, stack_id: str, inst=None) -> str | None:
    """The band an interactive app was started on, or None if not active: an instance's own marker (or a bare one: it
    may be this instance's); without an instance, any instance's (their band when they agree, else "" = marked, band
    unknown)."""
    own = getattr(inst, "rid", "")
    files = [marker(svc, stack_id, inst), marker(svc, stack_id)] if own else markers(svc, stack_id)   # (fail-safe)
    bands = []
    for f in files:
        try:
            bands.append(runtime_fs.read_text(svc._paths, f).strip())
        except (OSError, ValueError):       # missing/unreadable/symlinked -> not active
            continue
    if own:
        return bands[0] if bands else None
    return None if not bands else (bands[0] if len(set(bands)) == 1 else "")


def clear_stale(svc, keep: str = "") -> list[str]:
    """Drop the markers of apps that aren't actually running — launched/marked but never run (or since stopped).
    Called when another stack starts so the dash doesn't keep showing a prior interactive app's command block. `keep`
    is the stack just started (its markers are preserved). Returns the cleared markers' names (`<sid>@<rid>`)."""
    idir = svc._paths.runtime_root / "state" / "interactive"
    if not idir.exists():
        return []
    up = (RunState.RUNNING, RunState.DEGRADED)
    live = {cid: st.run_state for ss in svc.build_snapshot().stacks for cid, st in ss.components.items()}
    cleared = []
    for f in idir.glob("*.show"):
        sid = f.stem.partition("@")[0]                     # K7: <sid>@<rid>.show is the instance's
        if sid == keep:
            continue
        s = svc.stack(sid)
        # Judge the marker by the COMMAND-BEARING component: the interactive main (chat), or the stack's interactive
        # sidecar when the main is not interactive (voice's terminal variant — its GTK main is never RUNNING on a Lite
        # box, and judging by it dismissed a freshly presented command whenever any other stack started).
        comp = None
        if s is not None:
            main = s.main_component
            comp = main if (main and main.interactive) else next((c for c in s.components if c.interactive), None)
        if not (comp and live.get(comp.id) in up):
            svc._safe_unlink(f)
            cleared.append(f.stem)
    return cleared
