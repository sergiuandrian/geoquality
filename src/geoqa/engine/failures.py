"""Failure GeoJSON collection for HTML map / --geojson-out."""

from __future__ import annotations

import json
import logging
from typing import Any

import geopandas as gpd

from geoqa.result import CheckResult, Status

logger = logging.getLogger("geoqa")


def collect_failures(gdf: gpd.GeoDataFrame, results: list[CheckResult]) -> dict | None:
    """Build a WGS84 GeoJSON FeatureCollection of offending features.

    Prefer ``Issue.row_index`` (GeoDataFrame index). Fall back to
    ``Issue.feature_id`` only when it is a valid index label (legacy spatial
    checks that set feature_id to the row index). Attribute display ids that
    are not index labels are ignored for geometry collection.
    """
    failed: dict[Any, set[str]] = {}
    for r in results:
        if r.status not in (Status.FAIL, Status.ERROR):
            continue
        for issue in r.issues:
            key = issue.row_index if issue.row_index is not None else issue.feature_id
            if key is not None:
                failed.setdefault(key, set()).add(r.check)

    if not failed:
        return None

    try:
        index = gdf.index
        ids = [fid for fid in failed if fid in index]
        if not ids:
            return None
        subset = gdf.loc[ids, [gdf.geometry.name]].copy()
        subset["geoqa_failed_checks"] = [", ".join(sorted(failed[i])) for i in ids]
        if subset.crs is not None and subset.crs.to_epsg() != 4326:
            subset = subset.to_crs(4326)
        return json.loads(subset.to_json())
    except Exception:  # noqa: BLE001 - export is best-effort, never fail a run
        logger.exception("failed to collect offending features")
        return None
