"""Lightweight metadata completeness (sidecar XML / key file)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd

from geoqa.checks.base import result, status_for
from geoqa.config import MetadataCheck
from geoqa.result import CheckResult, Issue

CHECK = "metadata"


def run(gdf: gpd.GeoDataFrame, layer: str, source: str, cfg: MetadataCheck) -> list[CheckResult]:
    if not cfg.enabled:
        return []

    path = _resolve_sidecar(source, cfg.sidecar)
    if path is None or not path.is_file():
        return [
            result(
                CHECK + ".presence", layer, source, status_for(1, cfg.severity),
                f"Metadata sidecar not found: {cfg.sidecar}",
                severity=cfg.severity, n_total=1, n_failed=1,
                issues=[Issue(message=f"missing sidecar {cfg.sidecar}",
                              detail={"sidecar": cfg.sidecar})],
            )
        ]

    keys = _extract_keys(path)
    missing = [k for k in cfg.required_keys if k.lower() not in keys]
    n = len(missing)
    issues = [
        Issue(message=f"missing metadata key {k!r}", detail={"key": k, "path": str(path)})
        for k in missing
    ]
    return [
        result(
            CHECK + ".keys", layer, source, status_for(n, cfg.severity),
            "Required metadata keys present." if n == 0
            else f"{n} required metadata key(s) missing.",
            severity=cfg.severity, n_total=len(cfg.required_keys), n_failed=n, issues=issues,
        )
    ]


def _resolve_sidecar(source: str, sidecar: str) -> Path | None:
    p = Path(sidecar)
    if p.is_file():
        return p
    src = Path(source)
    if src.is_file():
        return src.parent / sidecar
    if src.is_dir():
        return src / sidecar
    return p


def _extract_keys(path: Path) -> set[str]:
    """Return lowercased key names found in XML tags or ``key: value`` lines."""
    text = path.read_text(encoding="utf-8", errors="replace")
    keys: set[str] = set()
    suffix = path.suffix.lower()
    if suffix in {".xml", ".gml"}:
        try:
            root = ET.fromstring(text)
            for el in root.iter():
                tag = el.tag.split("}")[-1]  # strip namespace
                keys.add(tag.lower())
                if el.text and el.text.strip():
                    # also treat nested structure as presence of parent tags
                    pass
        except ET.ParseError:
            pass
    # Key: value / key=value lines (also supplements XML).
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z_][\w.-]*)\s*[:=]", line)
        if m:
            keys.add(m.group(1).lower())
    return keys
