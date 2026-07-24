"""Engine package: suite orchestration with a phased per-layer pipeline.

Public API: ``from geoqa.engine import run_suite``.
"""

from __future__ import annotations

from geoqa.checks.sql_postgis import only_sql_safe_checks, sql_geometry_checks
from geoqa.datasource import _load_postgis, iter_layers
from geoqa.engine.suite import run_suite

__all__ = [
    "run_suite",
    "sql_geometry_checks",
    "only_sql_safe_checks",
    "_load_postgis",
    "iter_layers",
]
