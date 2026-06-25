"""Self-contained HTML reporter (Jinja2)."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from geoqa.result import Report

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def write_html(report: Report, path: str | Path, title: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("report.html.j2")
    html = template.render(
        report=report,
        data=report.to_dict(),
        title=title or report.suite_name,
    )
    path.write_text(html, encoding="utf-8")
    return path
