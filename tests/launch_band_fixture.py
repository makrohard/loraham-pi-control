"""K6 test helper (13.12.5): the per-stack band marker state/running/<sid>.band is gone; "the stack runs on BAND" is
what `ControllerService.launch_band` reports (the running instances' launch band, instances.launch_bands). For a test
that needs only that band, `set_launch_band` makes THIS service answer it for `sid`; every other stack is read as the
product reads it. A test whose decision reads the launch records themselves needs a real running instance instead
(a verified `record_launch(..., band=, extra={"rid": ...})` on a live pid)."""


def set_launch_band(svc, sid: str, band: str) -> None:
    bands = svc.__dict__.setdefault("_k6_launch_bands", {})
    bands[sid] = band
    real = type(svc).launch_band
    svc.launch_band = lambda stack_id, default="": bands.get(stack_id) or real(svc, stack_id, default)
