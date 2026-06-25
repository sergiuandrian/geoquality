"""Reporters that turn a :class:`~geoqa.result.Report` into human/machine output."""

from geoqa.reporting.console import print_report
from geoqa.reporting.html_report import write_html
from geoqa.reporting.json_report import write_json

__all__ = ["print_report", "write_html", "write_json"]
