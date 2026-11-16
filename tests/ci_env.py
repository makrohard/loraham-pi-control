"""Whether this run is CI's: `CI` names a CI run only as 1 or true, in any case; unset, empty, 0 or false is a
local run. The suite's gates that fail under CI where a local run skips read it here (test rule E7)."""
from __future__ import annotations

import os


def under_ci() -> bool:
    return os.environ.get("CI", "").lower() in ("1", "true")
