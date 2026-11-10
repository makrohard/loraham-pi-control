"""The host-listener inventory (A §2.4, F4): every TCP port this LHPC would listen on, with its owner.

Three kinds of owner:
- every stack component's TCP listener endpoint, at its configured port (the endpoint's firewall
  `port_param`, saved or default) or its address' port;
- the console's port;
- each stack web page's saved nginx port.

A save that would put two owners on one port, or a port outside 1024..65535, is refused at save and
names both owners: a stack port param (`param_conflicts`) and a stack web page (`webserver.plan_stack_exposure`
with `stack_listeners`). The exclusive `tcp.port` claims stay the runtime backstop. Every instance listens on its
radio's ports (base + 133 x (n - 1), DEC-W348)."""

from __future__ import annotations

from collections import defaultdict

from . import commands, instances
from .probes.endpoints import parse_endpoint

PORT_MIN, PORT_MAX = 1024, 65535
# The listener bases of A §2.4: ten follow their instance's radio (base + 133 x (n - 1), r1..r64, DEC-W348), four are
# fixed (Reticulum is one node). T-P1 holds them disjoint over every radio and from the fixed and page ports.
INSTANCE_BASES = (8001, 8080, 4403, 9443, 7000, 18083, 12323, 5000, 8000, 8788)
FIXED_PORTS = (37428, 37429, 4242, 8790)


def _owners(svc, st):
    """(label, instance, saved) for every instance stack `st` listens as (DEC-W409p/q): each saved instance (its own
    store, DEC-W384ac) and the TEMPLATE instance on every other rid r1..r64 (the manifest bases: what a newly added
    radio's instance starts with, so W348's 64-radio space stays guarded); without a hardware list its one store
    (None) on r1 and the template on r2..r64."""
    saved = instances.saved_insts(svc, st.id)
    own = [(st.id, None, True)] if saved == [None] else [(f"{st.id}@{i.rid}", i, True) for i in saved]
    taken = {"r1"} if saved == [None] else {i.rid for i in saved}
    return own + [(f"{st.id}@r{n} (template)", commands.Instance(f"r{n}"), False)
                  for n in range(1, commands.PORT_RADIOS + 1) if f"r{n}" not in taken]


def _port(svc, st, comp, ep, inst, saved: bool, overrides) -> int | None:
    """The port `inst` listens on at `ep`: a saved instance at its own bases (a save's value for it in `overrides`
    {(component, param, rid): value} first), a template instance at the manifest's; None when it is no port."""
    name = ep.firewall.port_param if ep.firewall is not None else ""
    raw = overrides.get((comp.id, name, instances.own_rid(inst))) if name and saved else None
    try:
        if inst is None:                                 # no hardware list: the stack's own store, r1's port
            raw = raw if raw is not None else (svc._resolved_param_value(comp.id, "run", comp.id, name) if name else None)
            return int(str(raw)) if raw is not None else int(parse_endpoint(ep.address)[1])
        tmpl = getattr(ep, "address_template", "") or ""
        i = instances.with_bases(svc, st.id, inst) if saved else inst
        if raw is not None:
            q = next(q for q in comp.run_params if q.name == name)
            base = int(commands._PORT_RE.fullmatch(getattr(q, "default_template", "") or str(q.default)).group(1))
            i = commands.Instance(i.rid, band=i.band, bases={**i.bases, base: int(str(raw))})
        return int(parse_endpoint(i.subst(tmpl) if "{port" in tmpl else ep.address)[1])
    except (AttributeError, StopIteration, TypeError, ValueError, commands.CommandError):
        return None


def stack_listeners(svc, overrides: dict | None = None) -> dict[int, list[str]]:
    """{port: [owner]} of every stack component's TCP listener, per instance (`_owners`, owners
    "<sid>@<rid>/<component>"); a fixed-port listener (no `{port:B}`: one node) once, as "<sid>/<component>".
    `overrides` {(component id, param, rid): value} stands for values a save is about to write."""
    out: dict[int, list[str]] = defaultdict(list)
    for st in svc.stacks():
        owners = _owners(svc, st)
        for comp in st.components:
            for ep in comp.endpoints:
                if ep.kind != "tcp" or ep.role != "listener" or ep.external:
                    continue
                derived = "{port" in (getattr(ep, "address_template", "") or "")
                for label, inst, saved in (owners if derived else [(st.id, None, True)]):
                    port = _port(svc, st, comp, ep, inst, saved, overrides or {})
                    if port is not None and f"{label}/{comp.id}" not in out[port]:
                        out[port].append(f"{label}/{comp.id}")
    return dict(out)


def nginx_listeners(svc) -> dict[int, list[str]]:
    """{port: [owner]} of the console and every stack web page with a saved port."""
    cfg = svc.config()
    out: dict[int, list[str]] = defaultdict(list)
    out[int(cfg.webserver.port)].append("the console")
    for page, swc in cfg.stackweb.items():
        if getattr(swc, "port", 0):
            out[int(swc.port)].append(f"the {page} web page")
    return dict(out)


def param_conflicts(svc, changes, inst=None) -> list[str]:
    """The refusals for a stack config save of instance `inst` (the one the save writes; None without a hardware
    list): each changed port param whose base is outside 1024..65535 (or whose r64 port is), or whose port on its
    instance's radio is already another owner's (`stack_listeners`, `nginx_listeners`: exact ports, DEC-W409p).
    `changes` are config_save Changes (kind, component, param, value)."""
    rid = instances.own_rid(inst)
    overrides = {(ch.comp.id, ch.param.name, rid): str(ch.value) for ch in changes if ch.kind == "r"}
    if not overrides:
        return []
    taken: dict[int, list[str]] = defaultdict(list)
    for src in (stack_listeners(svc, overrides), nginx_listeners(svc)):
        for port, owners in src.items():
            taken[port].extend(owners)
    problems = []
    for cid, name, _rid in overrides:
        st = svc.stack(svc.stack_of(cid) or "")
        comp = next((c for c in (st.components if st else ()) if c.id == cid), None)
        owner = f"{st.id if inst is None else f'{st.id}@{rid}'}/{cid}" if st else cid
        for ep in (e for e in (comp.endpoints if comp else ()) if e.firewall and e.firewall.port_param == name):
            try:
                base = int(overrides[(cid, name, rid)])
            except ValueError:
                continue
            if not PORT_MIN <= base <= PORT_MAX:
                problems.append(f"{owner}: port {base} is outside {PORT_MIN}..{PORT_MAX}")
                continue
            if "{port" in (ep.address_template or ""):        # radio-derived: every radio's port is in range (r64)
                try:
                    commands.rid_port(base, f"r{commands.PORT_RADIOS}")
                except commands.CommandError as exc:
                    problems.append(f"{owner}: {exc}")
                    continue
            port = _port(svc, st, comp, ep, inst, True, overrides)
            others = [o for o in taken.get(port, ()) if o != owner]
            if others:
                problems.append(f"{owner}: port {port} is already {', '.join(others)}'s listener")
    return problems
