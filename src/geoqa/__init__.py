"""geoqa - data quality for geospatial data.

A single tool to orchestrate geometry, CRS, topology, duplicate and attribute
checks across a folder of geospatial files, configured entirely from YAML and
producing a human-readable report. Think "Great Expectations for GIS".
"""

from geoqa.result import CheckResult, LayerReport, Report, Severity, Status

__version__ = "0.3.0"

__all__ = [
    "CheckResult",
    "LayerReport",
    "Report",
    "Severity",
    "Status",
    "__version__",
]
