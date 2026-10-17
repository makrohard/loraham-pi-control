"""The service family does not grow (tests/README.md, rule 13).

The family is `lhpc/core/services.py` and every `service_*` module it imports relatively
(`service_base.py` and the mixins). Each file has a ceiling for its lines and for its public methods
(functions defined directly in a class body whose name does not start with `_`). This test enforces
the ceilings only. A raise with `# <item id>: <one sentence>` on its line, and an item removing at
least what it adds, are review rules this test does not check; lowering a value is free.

A repository invariant, not behaviour: it reads the production files to measure their size, which
rule 1 otherwise forbids; nothing it asserts is about what the code does.
"""
import ast
import importlib

import pytest

import repo_paths

CORE = repo_paths.REPO / "lhpc" / "core"

# file: (max lines, max public methods) — measured on dev at 0.12.9, raised where a line says why
BUDGETS = {
    "services.py": (2254, 23),  # split-s6: the import of the new mixin; split-s7: the import of the new mixin; split-s5: the import of the new mixin
    "service_base.py": (166, 0),
    "service_admission.py": (459, 0),  # split-s4: split from services.py
    "service_auto_install.py": (1875, 16),
    "service_binary_channel.py": (299, 19),  # trackF-c3: the web client keeps its own selector
    "service_binary_ops.py": (1078, 5),  # trackF-c3: the web client follows meshtastic's binary install
    "service_boot_restore.py": (678, 2),
    "service_build.py": (797, 10),  # split-s5: split from service_lifecycle_ops.py
    "service_daemon_params.py": (508, 11),  # split-s2: split from service_params.py and service_lifecycle_ops.py
    "service_firewall.py": (1533, 19),
    "service_gps.py": (893, 13),  # split-s3: split from service_params.py and services.py
    "service_hmac.py": (1016, 17),
    "service_jobs.py": (765, 9),  # split-s6: split from service_lifecycle_ops.py
    "service_lifecycle_ops.py": (3357, 32),
    "service_maintenance.py": (2892, 30),  # testlab-det-1: S1 keeps curl rc and message; trackF-c3: update routes the web client
    "service_network.py": (1183, 9),
    "service_params.py": (2485, 39),
    "service_rflog.py": (474, 13),  # split-s1: split from service_lifecycle_ops.py
    "service_secrets.py": (399, 2),
    "service_selfupdate.py": (1932, 22),
    "service_stop.py": (791, 5),  # split-s7: split from service_lifecycle_ops.py
    "service_system.py": (759, 2),
    "service_webserver.py": (2388, 42),
}


def measure(source: str) -> tuple[int, int]:
    """(lines, public methods) of one module's source."""
    tree = ast.parse(source)
    public = sum(1 for cls in tree.body if isinstance(cls, ast.ClassDef)
                 for node in cls.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and not node.name.startswith("_"))
    return len(source.splitlines()), public


def family(services_source: str) -> set:
    """`services.py` and every `service_*` module it imports relatively."""
    return {"services.py"} | {f"{node.module}.py" for node in ast.walk(ast.parse(services_source))
                              if isinstance(node, ast.ImportFrom) and node.level == 1
                              and (node.module or "").startswith("service_")}


# A split moves a group of methods to its own mixin: (new module, its class, old module, old class, the
# group's public methods). Each one lives on the new mixin and no longer on the old one.
SPLITS = [
    ("service_rflog", "RflogOpsMixin", "service_lifecycle_ops", "LifecycleOpsMixin", (
        "rflog_clear",
        "rflog_clear_all",
        "rflog_decode",
        "rflog_job",
        "rflog_logging_state",
        "rflog_records",
        "rflog_roll_native_all",
        "rflog_running",
        "rflog_switch",
        "rflog_switcher",
        "rflog_tail",
        "set_rflog",
        "set_rflog_all",
    )),
    ("service_daemon_params", "DaemonParamsMixin", "service_params", "ParamsConfigMixin", (
        "chip_family_for_band",
        "clear_daemon_feed",
        "daemon_feed",
        "daemon_set",
        "high_power_for_band",
        "high_power_state",
        "set_high_power",
    )),
    ("service_daemon_params", "DaemonParamsMixin", "service_lifecycle_ops", "LifecycleOpsMixin", (
        "apply_daemon_params",
        "daemon_params_view",
        "reset_daemon_params",
        "save_daemon_params",
    )),
    ("service_gps", "GpsOpsMixin", "service_params", "ParamsConfigMixin", (
        "gps_consumers_running",
        "gps_liveness_blockers",
        "gps_monitor",
        "gps_nmea",
        "gps_settings",
        "gps_view",
        "legacy_gps_values",
        "set_gps",
    )),
    ("service_gps", "GpsOpsMixin", "services", "ControllerService", (
        "gps_enabled_for",
        "gps_owner_stack",
        "gps_plan",
        "optional_role",
        "refresh_gps_auto",
    )),
    # The admission group has no public method: its private methods are listed.
    ("service_admission", "AdmissionOpsMixin", "services", "ControllerService", (
        "_acquire_key",
        "_admission_guard",
        "_admit",
        "_admit_raw",
        "_admit_release",
        "_held_counts",
        "_keys_guard",
        "_lifecycle_guard",
        "_lifecycle_lock_keys",
        "_operation_bands",
        "_operation_resource_keys",
        "_operation_source_paths",
        "_source_operation_guard",
        "_web_source_precheck",
    )),
    ("service_jobs", "JobsOpsMixin", "service_lifecycle_ops", "LifecycleOpsMixin", (
        "active_jobs",
        "cap_controller_logs",
        "cap_start_logs",
        "log_running",
        "log_tail",
        "logs",
        "prune_logs",
        "spawn_start_job",
        "spawn_web_job",
    )),
    ("service_stop", "StopOpsMixin", "service_lifecycle_ops", "LifecycleOpsMixin", (
        "owner_stop_commands",
        "owner_stops",
        "restart",
        "stop",
        "stop_dependents",
    )),
    ("service_build", "BuildOpsMixin", "service_lifecycle_ops", "LifecycleOpsMixin", (
        "build",
        "build_inputs_path",
        "build_inputs_text",
        "fetched_artifacts_present",
        "install_blocker",
        "is_built",
        "run_params_for",
        "test",
        "unbuilt_build_deps",
        "unbuilt_components",
    )),
]


@pytest.mark.parametrize("new, new_cls, old, old_cls, names", SPLITS)
def test_a_split_group_lives_on_its_own_mixin(new, new_cls, old, old_cls, names):
    moved = vars(getattr(importlib.import_module(f"lhpc.core.{new}"), new_cls))
    stayed = vars(getattr(importlib.import_module(f"lhpc.core.{old}"), old_cls))
    assert ([n for n in names if n not in moved], [n for n in names if n in stayed]) == ([], [])


def test_the_table_covers_the_whole_family():
    # Every module of the family has a budget, and the table names no other file: a new mixin
    # without an entry fails here, and so does an entry removed while its module stays.
    assert family((CORE / "services.py").read_text()) == set(BUDGETS)


@pytest.mark.parametrize("source, missing, extra", [
    ("from .service_base import X\nfrom .service_new import Y\n", {"service_new.py"}, set()),
    ("from .service_base import X\n", set(), set()),
])
def test_an_unbudgeted_mixin_or_a_removed_entry_breaks_the_equality(source, missing, extra):
    # Negative fixture for the completeness rule (a new invariant: no parent-red exists).
    table = {"services.py", "service_base.py", "service_gone.py"}
    found = family(source)
    assert (found - table, table - found) == (missing, {"service_gone.py"} | extra)


@pytest.mark.parametrize("name", sorted(BUDGETS))
def test_service_family_within_budget(name):
    lines, public = measure((CORE / name).read_text())
    max_lines, max_public = BUDGETS[name]
    assert lines <= max_lines and public <= max_public, (
        f"{name}: {lines} lines / {public} public methods, budget {max_lines} / {max_public}")


def test_measure_counts_lines_and_only_public_methods_of_classes():
    # Negative/positive fixture for the counter itself (a new invariant: no parent-red exists).
    source = ("def top():\n    pass\n"
              "class A:\n    def run(self):\n        pass\n    def _helper(self):\n        pass\n"
              "    async def fetch(self):\n        pass\n")
    assert measure(source) == (9, 2)
