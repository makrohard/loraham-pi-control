"""STATUS-DEAD-SOCKET: each stack instance's own liveness, for `lhpc status` and the console's component cards.

An instance is RUNNING only while its process is alive AND every endpoint it serves answers: each TCP listener of
its own (instances.endpoint_statuses, Home's rule) and its own daemon CONF socket answering STATUS with RADIO=READY.
A live process with an endpoint that does not answer is DEGRADED, the endpoint named. An instance whose launch
record of this boot is left while its process is gone (it ended without a stop: a crash, a lost USB stick, a kill)
is NOT RUNNING. One live instance never speaks for another (box H 11-15: daemon@r4 kept "running" on screen while
daemon@r3's process was gone and /tmp/loraconf-r3.sock refused). Read-only; the snapshot's component states, which
start and stop decide by, are not changed here."""
from __future__ import annotations

from dataclasses import dataclass

from . import instances
from .model import ComponentKind

RUNNING, DEGRADED, NOT_RUNNING = "running", "degraded", "not running"


@dataclass(frozen=True)
class Liveness:
    key: str          # `<stack>@<rid>`
    rid: str
    state: str        # RUNNING | DEGRADED | NOT_RUNNING
    detail: str = ""  # why it is not plainly running ("" when it is)


def rows(svc, snap) -> dict:
    """{component id: [Liveness, by rid order]} of every service component that runs as an instance or whose
    instance ended without a stop; a component with neither is not listed (its snapshot state says it all)."""
    procs = instances.instance_processes(svc, svc._system.procfs.cmdlines())
    ended = {}
    for rec in svc._lifecycle().ended_launches():
        ended.setdefault(rec.get("component", ""), {}).setdefault(rec["rid"], rec)
    out = {}
    for ss in snap.stacks:
        for comp in ss.stack.components:
            if comp.process is None or comp.kind is not ComponentKind.SERVICE:
                continue
            live = {rid for sid, rid, cid, _pid in procs if sid == ss.stack.id and cid == comp.id and rid}
            gone = {rid: rec for rid, rec in ended.get(comp.id, {}).items()
                    if (rec.get("stack") or ss.stack.id) == ss.stack.id and rid not in live}
            found = [_live(svc, ss.stack.id, comp, ss.components[comp.id], rid) for rid in live] + [
                Liveness(f"{ss.stack.id}@{rid}", rid, NOT_RUNNING,
                         f"its process (pid {rec['pid']}) ended without a stop — "
                         f"lhpc stack start {ss.stack.id}@{rid}, or stop it to clear this")
                for rid, rec in gone.items()]
            if found:
                out[comp.id] = sorted(found, key=lambda x: int(x.rid[1:]))
    return out


def _live(svc, sid, comp, cs, rid) -> Liveness:
    """A running instance's state by its own endpoints: every TCP listener present, its daemon socket answering."""
    inst = instances.copy_of(svc, sid, rid)[0]
    key = f"{sid}@{rid}"
    if inst is None:
        return Liveness(key, rid, DEGRADED, "its radio is not in the hardware list (endpoints not checked)")
    bad = [f"listener absent: {o.spec.address}" for o in instances.endpoint_statuses(svc, cs, inst)
           if o.spec.kind == "tcp" and not o.present]
    for spec in comp.endpoints:
        if spec.kind == "unix" and spec.readiness == "daemon-status":
            addr = inst.subst(spec.address)
            obs = next((o for o in cs.endpoints if o.spec.kind == "unix" and o.spec.address == addr), None)
            present, detail = (obs.present, obs.detail) if obs is not None else _probe(svc, addr)
            if not present:
                bad.append(f"socket {addr} not answering: {detail}" if "RADIO=" not in detail
                           else f"socket {addr} answers, {detail}")
    return Liveness(key, rid, DEGRADED, "; ".join(bad)) if bad else Liveness(key, rid, RUNNING)


def _probe(svc, addr) -> tuple:
    """(ready, detail) of a daemon CONF socket the snapshot did not probe (its radio not enabled), as status.py reads
    one: absent, unreadable, or its RADIO state."""
    from .probes.unixsock import probe_daemon_status, probe_socket
    if not probe_socket(svc._system, addr).is_socket:
        return False, "absent"
    ds = probe_daemon_status(svc._system, addr)
    if not ds.reachable:
        return False, ds.evidence.get("error", "status unreadable")
    return ds.ready, f"RADIO={ds.radio or '?'}"


def lines(found) -> list:
    """The `lhpc status` lines of one component's instances (`rows`' list)."""
    return [f"        instance {x.key}: {x.state}" + (f" — {x.detail}" if x.detail else "") for x in found]
