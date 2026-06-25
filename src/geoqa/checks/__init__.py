"""Built-in geoqa checks.

Each module exposes a ``run(gdf, layer, source, cfg)`` function returning a list
of :class:`~geoqa.result.CheckResult`. The engine dispatches to them based on
the resolved per-layer configuration.
"""

from geoqa.checks import attributes, crs, duplicates, geometry, topology

__all__ = ["attributes", "crs", "duplicates", "geometry", "topology"]
