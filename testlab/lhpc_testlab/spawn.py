"""The spawn guard: Lifecycle spawns are the one path that bypasses the command runner
(detached Popen), so lab mode wraps it. A power trigger (`sh -c '… systemctl --no-block
reboot'`) is replaced by the detached `_testlab-power` helper, which performs a FAITHFUL
simulated reboot (stop owned stacks, advance the boot identity, restart the fakes) — the
host never reboots, but the admission gate and boot-bound records behave exactly as on a
real box. Any other denied host mutator refuses (None = spawn failure, the callers'
existing error path). Everything else — stack starts, network/HMAC helpers — spawns for
real.
"""
from __future__ import annotations

import sys

from . import rules, scenarios


def _shim_env(paths, argv, env):
    """Per-process env injection, scoped to exactly the spawn:
      * the reticulum node gets the fake spidev/gpiod modules on PYTHONPATH (the venv
        build keeps importing the real system bindings);
      * the deprecated igate gets the APRS-IS DNS interposer on LD_PRELOAD, so it
        connects to the lab's local sink and can NEVER reach the live ham network.
    """
    import os
    joined = " ".join(str(a) for a in argv)
    base = None
    if "loraham-rns-node" in joined:
        shims = paths.under("state", "testlab", "pyshims")
        if (shims / "spidev.py").exists():
            base = dict(os.environ if env is None else env)
            prior = base.get("PYTHONPATH", "")
            base["PYTHONPATH"] = f"{shims}:{prior}" if prior else str(shims)
    elif "loraham_igate" in joined:
        so = paths.under("state", "testlab", "aprs-redirect.so")
        if so.exists():
            base = dict(os.environ if env is None else env)
            prior = base.get("LD_PRELOAD", "")
            base["LD_PRELOAD"] = f"{so}:{prior}" if prior else str(so)
    return base if base is not None else env


def make_wrap_spawn(paths):
    def wrap(real_spawn):
        def guarded(argv, log_path, cwd=None, env=None):
            kind = rules.power_kind_in(argv)
            if kind:
                scenarios.log_event(paths, f"SIMULATED power trigger: {kind}")
                helper = [sys.executable, "-m", "lhpc_testlab", "_power",
                          "--kind", kind]
                return real_spawn(helper, log_path, cwd=cwd, env=env)
            verdict, _detail = rules.classify(argv)
            if verdict == "deny":
                scenarios.log_event(paths,
                                    f"DENIED spawn: {' '.join(map(str, argv))[:200]}")
                return None
            # SAFETY: the deprecated igate may only run behind the APRS-IS DNS
            # interposer. If the .so is missing (build failed), REFUSE the spawn — it
            # must never reach the live ham network, redirect-or-nothing.
            if ("loraham_igate" in " ".join(str(a) for a in argv)
                    and not paths.under("state", "testlab",
                                        "aprs-redirect.so").exists()):
                scenarios.log_event(paths, "REFUSED igate: APRS redirect .so absent "
                                           "(would reach live APRS-IS)")
                return None
            return real_spawn(argv, log_path, cwd=cwd,
                              env=_shim_env(paths, argv, env))
        return guarded
    return wrap
