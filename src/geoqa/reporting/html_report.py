"""Self-contained HTML reporter (Jinja2).

When the report was produced with ``run_suite(collect_failures=True)`` and
``include_map`` is set, an interactive Leaflet map of the offending features is
embedded per layer (Leaflet + OSM tiles load from a CDN, so the map needs
network access; the rest of the report renders fully offline).
"""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from geoqa.result import Report

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def write_html(
    report: Report,
    path: str | Path,
    title: str | None = None,
    max_issues: int = 50,
    include_map: bool = True,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    map_ids: dict[int, str] = {}
    maps: dict[str, dict] = {}
    if include_map:
        for i, layer in enumerate(report.layers):
            fc = layer.failures
            if fc and fc.get("features"):
                mid = f"geoqa-map-{i}"
                map_ids[i] = mid
                maps[mid] = fc
    # `<\/` keeps the inline JSON valid while preventing a `</script>` breakout.
    maps_json = json.dumps(maps).replace("</", "<\\/")

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("report.html.j2")
    html = template.render(
        report=report,
        data=report.to_dict(max_issues=max_issues),
        title=title or report.suite_name,
        map_ids=map_ids,
        maps_json=maps_json,
        has_maps=bool(maps),
    )
    path.write_text(html, encoding="utf-8")
    return path
