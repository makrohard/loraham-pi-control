"""Test-lab fake `gpiod` (libgpiod v2 API surface): request_lines returns an in-memory
line store; wait_edge_events SLEEPS for the full timeout and reports no events (the
RX thread idles at the production cadence instead of busy-spinning). Injected only
into the lab's reticulum node process via PYTHONPATH."""
import time

from .line import Bias, Direction, Edge, Value  # noqa: F401  (re-exported API surface)


class LineSettings:
    def __init__(self, direction=None, active_low=False, output_value=None,
                 edge_detection=None, bias=None):
        self.direction = direction
        self.active_low = active_low
        self.output_value = output_value
        self.edge_detection = edge_detection
        self.bias = bias


class _Request:
    def __init__(self, config):
        self._values = {off: (s.output_value or Value.INACTIVE)
                        for off, s in (config or {}).items()}

    def set_value(self, offset, value):
        self._values[offset] = value

    def get_value(self, offset):
        return self._values.get(offset, Value.INACTIVE)

    def wait_edge_events(self, timeout):
        time.sleep(timeout.total_seconds() if hasattr(timeout, "total_seconds")
                   else float(timeout))
        return False

    def read_edge_events(self):
        return []

    def release(self):
        pass


def request_lines(path, consumer=None, config=None, **kwargs):
    return _Request(config)
