"""Configuration schema and loading.

The whole point of geoqa is that rules are configured in YAML, *without code*.
This module defines the schema (validated with pydantic) and the merge logic
that lets a ``defaults`` block apply to every layer while per-layer blocks
override individual keys.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    create_model,
    field_validator,
    model_validator,
)

from geoqa.result import Severity


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CrsCheck(_Base):
    enabled: bool = True
    severity: Severity = Severity.ERROR
    required: bool = True
    allowed_epsg: list[int] | None = None
    expected_epsg: int | None = None


class GeometryCheck(_Base):
    enabled: bool = True
    severity: Severity = Severity.ERROR
    valid: bool = True
    no_empty: bool = True
    no_missing: bool = True
    fix: bool = False  # apply shapely.make_valid and write a *.fixed output


class FuzzyConfig(_Base):
    enabled: bool = False
    predicate: str = "intersects"  # any GeoPandas/Shapely binary predicate
    min_overlap: float = 0.9  # IoU threshold for (multi)polygons
    max_distance: float = 0.0  # max centroid distance for point/line dupes (CRS units)


class DuplicatesCheck(_Base):
    enabled: bool = True
    severity: Severity = Severity.WARN
    exact: bool = True
    fuzzy: FuzzyConfig = Field(default_factory=FuzzyConfig)


class DomainRule(_Base):
    allowed: list[Any] | None = None
    min: float | None = None
    max: float | None = None
    regex: str | None = None


class AttributesCheck(_Base):
    enabled: bool = True
    severity: Severity = Severity.ERROR
    required: list[str] = Field(default_factory=list)  # columns that must exist
    not_null: list[str] = Field(default_factory=list)  # columns with zero nulls
    unique: list[str] = Field(default_factory=list)  # columns with unique values
    max_null_fraction: dict[str, float] = Field(default_factory=dict)
    domains: dict[str, DomainRule] = Field(default_factory=dict)


class TopologyCheck(_Base):
    enabled: bool = False
    severity: Severity = Severity.WARN
    no_overlaps: bool = False  # polygons should not overlap each other
    no_gaps: bool = False  # dissolved polygons should have no interior holes
    no_dangles: bool = False  # line endpoints should connect to the network
    # Tolerances are expressed in metres (data is reprojected to a metric CRS).
    min_area: float = 0.0  # ignore overlaps/gaps smaller than this (sliver noise)
    snap_tolerance: float = 0.0  # snap line endpoints within this distance for dangles


class LayerConfig(_Base):
    crs: CrsCheck = Field(default_factory=CrsCheck)
    geometry: GeometryCheck = Field(default_factory=GeometryCheck)
    duplicates: DuplicatesCheck = Field(default_factory=DuplicatesCheck)
    attributes: AttributesCheck = Field(default_factory=AttributesCheck)
    topology: TopologyCheck = Field(default_factory=TopologyCheck)


class SourceSpec(_Base):
    # File/folder source (one of ``path`` or ``connection`` is required).
    path: str | None = None
    pattern: str | None = None  # glob applied when ``path`` is a directory
    layer: str | None = None  # specific sub-layer for multi-layer formats (GPKG)
    name: str | None = None  # override the inferred layer name

    # PostGIS / SQLAlchemy source.
    connection: str | None = None  # SQLAlchemy URL, e.g. postgresql://user@host/db
    table: str | None = None  # table to read (mutually exclusive with ``query``)
    query: str | None = None  # raw SQL returning a geometry column
    geom_column: str = "geom"  # name of the geometry column to read

    @model_validator(mode="after")
    def _check_source(self) -> SourceSpec:
        if self.connection:
            if not (self.table or self.query):
                raise ValueError(
                    "a 'connection' source requires either 'table' or 'query'"
                )
            if self.table and self.query:
                raise ValueError("set only one of 'table' or 'query', not both")
        elif not self.path:
            raise ValueError("each source needs a 'path' or a 'connection'")
        return self


class ReportConfig(_Base):
    title: str | None = None
    max_issues_per_check: int = 50


class Suite(_Base):
    version: int = 1
    name: str = "geoqa suite"
    sources: list[SourceSpec] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)
    layers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    report: ReportConfig = Field(default_factory=ReportConfig)

    # Resolved at load time; not part of the YAML.
    base_dir: Path = Field(default=Path("."), exclude=True)

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported config version {v!r}; expected 1")
        return v

    def config_for_layer(self, layer_name: str) -> LayerConfig:
        """Deep-merge ``defaults`` with the per-layer override and validate it."""
        merged = copy.deepcopy(self.defaults)
        override = self.layers.get(layer_name, {})
        merged = _deep_merge(merged, override)
        return resolve_layer_model().model_validate(merged)

    def resolve_path(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (self.base_dir / p)


_layer_model_cache: tuple[int, type[LayerConfig]] | None = None


def resolve_layer_model() -> type[LayerConfig]:
    """Return a LayerConfig model extended with any plugin check config keys.

    When only the built-in checks are registered this is exactly ``LayerConfig``,
    so behaviour (and ``extra="forbid"`` typo detection) is unchanged. When
    plugins are present, an extended model adds a typed field per plugin check.
    """
    global _layer_model_cache
    from geoqa.registry import get_registry

    registry = get_registry()
    specs = registry.specs()
    cache_key = hash(tuple((s.name, id(s.config_model)) for s in specs))
    if _layer_model_cache is not None and _layer_model_cache[0] == cache_key:
        return _layer_model_cache[1]

    extra: dict[str, Any] = {
        s.name: (s.config_model, Field(default_factory=s.config_model))
        for s in specs
        if s.name not in LayerConfig.model_fields
    }
    model = (
        LayerConfig
        if not extra
        else create_model("LayerConfigExtended", __base__=LayerConfig, **extra)
    )
    _layer_model_cache = (cache_key, model)
    return model


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` (override wins for scalars)."""
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_suite(path: str | Path) -> Suite:
    """Load and validate a geoqa YAML config file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")
    suite = Suite.model_validate(raw)
    suite.base_dir = path.resolve().parent
    # Validate the defaults block eagerly so typos surface immediately.
    resolve_layer_model().model_validate(_deep_merge({}, suite.defaults))
    return suite
