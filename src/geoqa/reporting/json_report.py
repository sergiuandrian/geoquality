"""JSON reporter (machine-readable, good for CI artifacts)."""

from __future__ import annotations

import json
from pathlib import Path

from geoqa.result import Report


def write_json(report: Report, path: str | Path, max_issues: int = 50) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(report.to_dict(max_issues=max_issues), fh, indent=2, ensure_ascii=False)
    return path
