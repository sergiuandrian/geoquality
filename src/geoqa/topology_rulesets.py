"""Named topology ruleset aliases (config sugar for Phase G / G5)."""

from __future__ import annotations

from typing import Any

RULESETS: dict[str, dict[str, Any]] = {
    "cadastre_coverage": {
        "enabled": True,
        "no_overlaps": True,
        "no_coverage_gaps": True,
        "no_spillover": True,
        "coincident_edges": True,
    },
    "network": {
        "enabled": True,
        "no_dangles": True,
        "no_undershoots": True,
        "no_overshoots": True,
        "ignore_boundary": True,
    },
    "admin_coverage": {
        "enabled": True,
        "no_overlaps": True,
        "no_coverage_gaps": True,
        "no_spillover": True,
        "coincident_edges": True,
        "min_area": 1.0,
    },
}


def expand_ruleset(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Merge ruleset defaults under explicit keys (explicit wins)."""
    base = dict(RULESETS[name])
    # User-provided keys override the alias; keep ``ruleset`` for traceability.
    out = {**base, **data}
    return out
