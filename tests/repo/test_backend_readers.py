"""Every reader of a component's `depends_on`, `resources` or `requires` sits on one of the backend paths (plan reti
v21 §1, B1 design note v4): C1 the start's run order (the instance's backend), C2 the snapshot (what runs), C3 a
named live / in_use reader, the manifest view itself, or a field the backend never keys (gui, absent_file). A new
reader fails here until it is classified, so a backend-keyed entry cannot leak past the variant unseen."""
from __future__ import annotations

import ast
import pathlib

import lhpc

ROOT = pathlib.Path(lhpc.__file__).parent
FIELDS = {"depends_on", "resources", "requires", "backend_depends"}

READERS = {
    "adapters/web/app.py::create_app._stack_groups": "C2 snapshot",
    "core/deps.py::stack_report": "M7 in_use (its caller passes stacks_in_use)",
    "core/instances.py::keyed": "the variant itself",
    "core/instances.py::for_backend": "the variant itself",
    "core/lifecycle.py::Lifecycle.missing_requirements": "the component it is given",
    "core/manifest.py::_validate_graph": "load: the manifest as declared",
    "core/manifest.py::_validate_graph._visit": "load: the manifest as declared",
    "core/manifest.py::_check_backend_keys": "load: the manifest as declared",
    "core/radio_start.py::radio_dependents": "C3 live",
    "core/resources.py::limit_radio_claims": "the component it is given",
    "core/resources.py::interpret_conflicts": "the components it is given (C2 / C1)",
    "core/service_admission.py::AdmissionOpsMixin._operation_resource_keys": "C1 order",
    "core/service_auto_install.py::AutoInstallOpsMixin._auto_install_dep_mandatory": "M7 in_use",
    "core/service_auto_install.py::AutoInstallOpsMixin._auto_install_scope_edges": "M7 in_use",
    "core/service_auto_install.py::AutoInstallOpsMixin._auto_install_scope": "M7 in_use",
    "core/service_lifecycle_ops.py::LifecycleOpsMixin._keyed": "the components it is given (C2 / C1)",
    "core/service_lifecycle_ops.py::LifecycleOpsMixin._running_conflicts": "C1 order (its component)",
    "core/service_lifecycle_ops.py::LifecycleOpsMixin._conflicting_service_hint": "absent_file only: never keyed",
    "core/service_lifecycle_ops.py::LifecycleOpsMixin.run_blockers": "C1 order + C2 snapshot",
    "core/service_lifecycle_ops.py::LifecycleOpsMixin._spi_shared_warning.unlocked": "C1 order + C2 snapshot",
    "core/service_lifecycle_ops.py::LifecycleOpsMixin._execute_start": "C1 order",
    "core/service_lifecycle_ops.py::LifecycleOpsMixin._direct_radio_owners": "C2 snapshot",
    "core/service_lifecycle_ops.py::LifecycleOpsMixin.needs_display": "gui only: never keyed",
    "core/service_lifecycle_ops.py::LifecycleOpsMixin._unmet_dependencies": "C1 order",
    "core/service_maintenance.py::MaintenanceOpsMixin._declared_dep_scopes": "bootstrap: ALL backends (stated)",
    "core/service_params.py::ParamsConfigMixin.system_deps": "M7 in_use",
    "core/service_stop.py::StopOpsMixin._uncertain_daemon_dependents": "C2 snapshot",
    "core/service_stop.py::StopOpsMixin.stop_dependents": "C2 snapshot",
    "core/service_stop.py::StopOpsMixin._daemon_bands_to_release": "C3 live",
    "core/service_stop.py::StopOpsMixin._dep_stacks_to_release": "manifest: excludes the daemon, the one backend_depends",
    "core/services.py::ControllerService._overlay_licensed_tx_enabled": "C2 snapshot",
    "core/services.py::ControllerService._tx_chain_components": "C3 live",
    "core/services.py::ControllerService._overlay_gui_unavailable": "gui only: never keyed",
    "core/services.py::ControllerService.explain": "the manifest view, labelled (backend X)",
    "core/services.py::ControllerService._run_order.visit": "C1 order (makes the variant)",
    "core/services.py::ControllerService._daemon_needs": "C1 order",
    "core/services.py::ControllerService._dependents_of": "C3 live",
    "core/spi_mode.py::drives_spi": "the component it is given",
    "core/status.py::StatusProber.assess_stacks": "C2 snapshot (build_snapshot hands it the live stacks)",
    "core/status.py::stack_dependencies": "C2 snapshot (app.py passes the snapshot's stacks)",
}
TEMPLATES = {"adapters/web/templates/_component_card.html": "C2 snapshot (the card's component is the snapshot's)"}


def _readers() -> set:
    found = set()
    for path in sorted(ROOT.rglob("*.py")):
        names: list = []

        def walk(node):
            for child in ast.iter_child_nodes(node):
                scoped = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                if scoped:
                    names.append(child.name)
                if isinstance(child, ast.Attribute) and child.attr in FIELDS and isinstance(child.ctx, ast.Load):
                    found.add(f"{path.relative_to(ROOT)}::{'.'.join(names) or '<module>'}")
                walk(child)
                if scoped:
                    names.pop()
        walk(ast.parse(path.read_text()))
    return found


def test_every_reader_of_the_backend_keyed_fields_is_classified():
    assert _readers() == set(READERS)


def test_every_template_reader_is_classified():
    used = {str(p.relative_to(ROOT)) for p in ROOT.rglob("*.html")
            if any(f".{f}" in p.read_text() for f in ("resources", "requires", "depends_on"))}
    assert used == set(TEMPLATES)
