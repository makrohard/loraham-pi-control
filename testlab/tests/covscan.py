"""Coverage-matrix enumerators + the covers()-claim scanner (non-test module per suite
hygiene). Row identity is the OPERATION: dispatching routes expand per op (axes imported
from the real constants where they exist), templates contribute one row per form, the
CLI one row per subcommand leaf, and every stack a fixed phase set."""
from __future__ import annotations

import ast
import re
from pathlib import Path

TESTS_DIR = Path(__file__).parent
REPO = TESTS_DIR.parent

# Dispatching routes -> (axis source). WEB_ACTIONS and the power kinds are imported from
# the code; the network/testlab op tuples are inline literals in their routes — kept in
# lockstep here and CROSS-CHECKED by the gate (an op added to the route without a matrix
# row shows up as an unlisted id the moment its axis entry lands here; an axis entry the
# route refuses is caught by the acceptance sweep's 404).
def _axes() -> dict:
    from lhpc.core.services import ControllerService
    return {
        "POST /action": tuple(ControllerService.WEB_ACTIONS),
        "POST /power/<kind>": tuple(ControllerService._POWER_KINDS),
        "POST /network/<op>": ("scan", "connect", "prefer", "forget", "retry", "ap"),
        "POST /testlab/<op>": ("reset", "scenario", "inject", "check"),
    }


def _app():
    import os
    import tempfile

    from lhpc_testlab.labweb import build_app  # lhpc routes + the /testlab blueprint
    tmp = Path(tempfile.mkdtemp())
    (tmp / "config" / "stacks").mkdir(parents=True)
    had = os.environ.pop("LHPC_TESTLAB", None)
    try:
        return build_app()
    finally:
        if had is not None:
            os.environ["LHPC_TESTLAB"] = had


def route_ids() -> set[str]:
    axes = _axes()
    ids: set[str] = set()
    for rule in _app().url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            base = f"{method} {rule.rule}"
            if base in axes:
                ids.update(f"route:{base}#{op}" for op in axes[base])
            else:
                ids.add(f"route:{base}")
    return ids


def sweepable_route_ids() -> set[str]:
    """Rows the two live-enumerating sweeps prove by construction: every parameterless
    GET (render sweep) and every POST row (CSRF-refusal sweep)."""
    ids = set()
    for rid in route_ids():
        body = rid[len("route:"):]
        method = body.split(" ", 1)[0]
        rule = body.split(" ", 1)[1].split("#", 1)[0]
        if method == "POST" or (method == "GET" and "<" not in rule):
            ids.add(rid)
    return ids


def cli_ids() -> set[str]:
    from lhpc_testlab.cli import build_parser as lab_parser

    from lhpc.adapters.cli.main import build_parser
    ids: set[str] = set()

    def sub_actions(parser):
        for action in parser._actions:                         # argparse internals in ONE place
            if action.__class__.__name__ == "_SubParsersAction":
                return action.choices
        return {}
    for verb, sub in sub_actions(build_parser()).items():
        if verb.startswith("_"):
            continue
        nested = sub_actions(sub)
        if nested:
            ids.update(f"cli:{verb} {leaf}" for leaf in nested if not leaf.startswith("_"))
        ids.add(f"cli:{verb}")
    for verb in sub_actions(lab_parser()):
        if not verb.startswith("_"):
            ids.add(f"labcli:{verb}")
    return ids


_FORM_RE = re.compile(r"<form[^>]*action=\"\{\{\s*url_for\(\s*'([a-zA-Z0-9_.]+)'")


def form_ids() -> set[str]:
    import lhpc_testlab

    import lhpc.adapters.web.app as lhpc_app
    dirs = [Path(lhpc_app.__file__).parent / "templates",
            Path(lhpc_testlab.__file__).parent / "templates"]
    ids: set[str] = set()
    for tpl_dir in dirs:
        for tpl in sorted(tpl_dir.glob("*.html")):
            for endpoint in _FORM_RE.findall(tpl.read_text()):
                ids.add(f"form:{tpl.name}#{endpoint}")
    return ids


STACK_PHASES = ("configure", "start", "data", "stop", "restart", "failure", "ui")


def stack_ids() -> set[str]:
    from lhpc.core.manifest import load_manifest
    return {f"stack:{s.id}#{phase}" for s in load_manifest()
            for phase in STACK_PHASES}


def all_ids() -> set[str]:
    return route_ids() | cli_ids() | form_ids() | stack_ids()


def claimed_ids() -> set[str]:
    """Every string literal inside pytest.mark.covers(...) across the acceptance and
    browser lanes — a deterministic AST scan, independent of collection."""
    claims: set[str] = set()
    for lane in ("acceptance", "browser"):
        for path in sorted((TESTS_DIR / lane).glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "covers"):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            claims.add(arg.value)
                        # anything non-literal (starred/computed) is deliberately
                        # IGNORED — claims must be greppable string literals
    return claims
