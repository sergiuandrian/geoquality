"""Reporters that turn a :class:`~geoqa.result.Report` into human/machine output."""

from geoqa.reporting.console import print_report
from geoqa.reporting.geojson_report import write_geojson_failures
from geoqa.reporting.html_report import write_html
from geoqa.reporting.json_report import write_json
from geoqa.reporting.junit import write_junit

__all__ = [
    "print_report",
    "write_geojson_failures",
    "write_html",
    "write_json",
    "write_junit",
]
