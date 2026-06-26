"""Export offending features as GeoJSON so they open straight in QGIS.

Requires the report to have been produced with ``run_suite(collect_failures=True)``
so each :class:`~geoqa.result.LayerReport` carries a ``failures`` FeatureCollection.
"""

from __future__ import annotations

import json
from pathlib import Path

from geoqa.result import Report


def write_geojson_failures(report: Report, out_dir: str | Path) -> list[Path]:
    """Write one ``<layer>.failures.geojson`` per layer that has failures.

    Returns the list of files written (empty when nothing failed).
    """
    out = Path(out_dir)
    written: list[Path] = []
    for layer in report.layers:
        fc = layer.failures
        if not fc or not fc.get("features"):
            continue
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{_safe(layer.layer)}.failures.geojson"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(fc, fh, ensure_ascii=False)
        written.append(path)
    return written


def _safe(name: str) -> str:
    """Make a layer name safe to use as a filename."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "layer"
