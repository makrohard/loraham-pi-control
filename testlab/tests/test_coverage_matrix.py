"""The coverage-matrix gate (default lane — no server needed): every live surface id
(route ops, CLI leaves, template forms, stack phases) must appear in
tests/coverage_matrix.toml as "acceptance" (a covers() claim must exist), "sweep"
(provable by the live-enumerating acceptance sweeps), or a documented limitation with a
non-empty reason. Fails with ONE aggregated report; also fails on stale rows/claims —
the matrix can never drift from the code. Closes the docs/maintenance.md route-table
gap."""
from __future__ import annotations

import tomllib
from pathlib import Path

import covscan

MATRIX = Path(__file__).parent / "coverage_matrix.toml"


def _load() -> dict[str, object]:
    data = tomllib.loads(MATRIX.read_text())
    rows: dict[str, object] = {}
    for section, prefix in (("route", "route:"), ("cli", "cli:"), ("labcli", "labcli:"),
                            ("form", "form:"), ("stack", "stack:")):
        for key, value in data.get(section, {}).items():
            rows[prefix + key] = value
    return rows


def test_every_surface_id_is_covered_or_documented():
    live = covscan.all_ids()
    rows = _load()
    claims = covscan.claimed_ids()
    sweepable = covscan.sweepable_route_ids()
    problems: list[str] = []
    for rid in sorted(live - rows.keys()):
        problems.append(f"UNLISTED: {rid} — add a matrix row (acceptance/sweep/"
                        "limitation)")
    for rid in sorted(rows.keys() - live):
        problems.append(f"STALE ROW: {rid} — no such live surface id")
    for rid, value in sorted(rows.items()):
        if rid not in live:
            continue
        if value == "acceptance":
            if rid not in claims:
                problems.append(f"UNTESTED: {rid} is marked acceptance but no "
                                "covers() claim names it")
        elif value == "sweep":
            if rid not in sweepable:
                problems.append(f"NOT SWEEPABLE: {rid} — the sweeps only prove "
                                "parameterless GETs and POST rows")
        elif isinstance(value, dict) and set(value) == {"limitation"}:
            if not str(value["limitation"]).strip():
                problems.append(f"UNDOCUMENTED LIMITATION: {rid} has an empty reason")
        else:
            problems.append(f"INVALID VALUE: {rid} = {value!r} (want 'acceptance', "
                            "'sweep', or {{ limitation = \"…\" }})")
    for rid in sorted(claims - live):
        problems.append(f"STALE CLAIM: covers({rid!r}) names no live surface id")
    assert not problems, "\n" + "\n".join(problems)


def test_acceptance_claims_are_marked_acceptance():
    """A claimed id whose matrix row says sweep/limitation is an upgrade the matrix
    must record — the row is the human-facing truth."""
    rows = _load()
    stale = [rid for rid in sorted(covscan.claimed_ids())
             if rid in rows and rows[rid] != "acceptance"]
    assert not stale, f"claimed by tests but not marked acceptance: {stale}"
