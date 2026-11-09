"""K6 test helper (13.12.5): the per-stack band marker state/running/<sid>.band is gone; "the stack runs on BAND" is
what `ControllerService._launch_bands_of` reports (the running instances' launch bands, instances.launch_bands; launch_band
and the overview read it). For a test
that needs only that band, `set_launch_band` makes THIS service answer it for `sid`; every other stack is read as the
product reads it. A test whose decision reads the launch records themselves needs a real running instance instead
(a verified `record_launch(..., band=, extra={"rid": ...})` on a live pid)."""


def set_launch_band(svc, sid: str, band: str) -> None:
    bands = svc.__dict__.setdefault("_k6_launch_bands", {})
    bands[sid] = band
    real = type(svc)._launch_bands_of
    svc._launch_bands_of = lambda stack_id: {bands[stack_id]} if bands.get(stack_id) else real(svc, stack_id)
