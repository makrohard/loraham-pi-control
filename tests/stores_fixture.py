"""K6 test helper: the settings file the service reads and writes for a stack (and a band caller's band): its
instance's own config/stacks/<sid>@<rid>.toml (instances.store_inst), the global <sid>.toml without a list."""
from lhpc.core import instances
from lhpc.core.config import _stack_config_path


def inst_of(svc, sid: str, band: str = ""):
    return instances.store_inst(svc, sid, band)


def own_file(svc, sid: str, band: str = ""):
    p = _stack_config_path(svc._paths, sid, band, inst_of(svc, sid, band))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
