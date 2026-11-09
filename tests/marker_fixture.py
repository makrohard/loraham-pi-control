"""K7 test helper: an interactive app's marker as the start writes it with a hardware list (the baseline r1 433, r2
868): its instance's own, on the band's radio. A bare marker is a pre-K7 leftover that no reader takes (G1-K7A 3)."""
from lhpc.core.commands import Instance

BASELINE_RID = {"433": "r1", "868": "r2"}


def mark(svc, sid: str, band: str, stored=None) -> bool:
    """Mark `sid`'s instance on `band`'s baseline radio; `stored` is the band text the start stores (default `band`)."""
    rid = BASELINE_RID[band or "433"]
    return svc.mark_interactive(sid, band if stored is None else stored, Instance(rid, band=band or "433"))
