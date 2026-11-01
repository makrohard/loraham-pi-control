"""The host-listener inventory (A §2.4, F4): every TCP port this LHPC would listen on, with its owner.

Three kinds of owner:
- every stack component's TCP listener endpoint, at its configured port (the endpoint's firewall
  `port_param`, saved or default) or its address' port;
- the console's port;
- each stack web page's saved nginx port.

A save that would put two owners on one port, or a port outside 1024..65535, is refused at save and
names both owners: a stack port param (`param_conflicts`) and a stack web page (`webserver.plan_stack_exposure`
with `stack_listeners`). The exclusive `tcp.port` claims stay the runtime backstop. Until L5 every instance is
slot 0 (one per stack), so the inventory is today's listeners."""

from __future__ import annotations

from collections import defaultdict

from .probes.endpoints import parse_endpoint

PORT_MIN, PORT_MAX = 1024, 65535
# The listener bases of A §2.4: ten follow their instance's slot (base + 10 x slot, slots 0–7), four are fixed
# (Reticulum is one node). T-P1 holds them disjoint over every slot.
SLOTTED_BASES = (8001, 8080, 4403, 9443, 7000, 18083, 12323, 5000, 8000, 8788)
FIXED_PORTS = (37428, 37429, 4242, 8790)


def _endpoint_port(svc, comp, ep, overrides) -> int | None:
    meta = ep.firewall
    name = meta.port_param if meta is not None else ""
    if name:
        raw = overrides.get((comp.id, name))
        if raw is None:
            raw = svc._resolved_param_value(comp.id, "run", comp.id, name)
        try:
            return int(str(raw))
        except ValueError:
            return None
    try:
        return int(parse_endpoint(ep.address)[1])
    except (TypeError, ValueError):
        return None


def stack_listeners(svc, overrides: dict | None = None) -> dict[int, list[str]]:
    """{port: [owner]} of every stack component's TCP listener; `overrides` {(component id, param): value}
    stands for values a save is about to write."""
    out: dict[int, list[str]] = defaultdict(list)
    for st in svc.stacks():
        for comp in st.components:
            for ep in comp.endpoints:
                if ep.kind != "tcp" or ep.role != "listener" or ep.external:
                    continue
                port = _endpoint_port(svc, comp, ep, overrides or {})
                if port is not None and f"{st.id}/{comp.id}" not in out[port]:
                    out[port].append(f"{st.id}/{comp.id}")
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


def param_conflicts(svc, changes) -> list[str]:
    """The refusals for a stack config save: each changed port param whose new port is outside
    1024..65535 or already another owner's. `changes` are config_save Changes (kind, component, param,
    value)."""
    overrides = {(ch.comp.id, ch.param.name): str(ch.value) for ch in changes if ch.kind == "r"}
    if not overrides:
        return []
    stack = stack_listeners(svc, overrides)
    every: dict[int, list[str]] = defaultdict(list)
    for src in (stack, nginx_listeners(svc)):
        for port, owners in src.items():
            every[port].extend(owners)
    problems = []
    for cid, name in overrides:
        comp = next((c for st in svc.stacks() for c in st.components if c.id == cid), None)
        for ep in (e for e in (comp.endpoints if comp else ()) if e.firewall and e.firewall.port_param == name):
            owner = f"{svc.stack_of(cid)}/{cid}"
            port = _endpoint_port(svc, comp, ep, overrides)
            if port is None:
                continue
            if not PORT_MIN <= port <= PORT_MAX:
                problems.append(f"{owner}: port {port} is outside {PORT_MIN}..{PORT_MAX}")
            others = [o for o in every.get(port, []) if o != owner]
            if others:
                problems.append(f"{owner}: port {port} is already {', '.join(others)}'s listener")
    return problems
