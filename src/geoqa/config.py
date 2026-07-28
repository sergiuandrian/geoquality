"""Configuration schema and loading.

The whole point of geoqa is that rules are configured in YAML, *without code*.
This module defines the schema (validated with pydantic) and the merge logic
that lets a ``defaults`` block apply to every layer while per-layer blocks
override individual keys.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Literal

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


class PostgisWriteConfig(_Base):
    """Optional PostGIS write-back target for the repair pipeline."""

    connection: str | None = None
    table: str | None = None
    id_column: str = "id"
    geom_column: str = "geom"
    dry_run: bool = True  # must set false + CLI confirm to mutate


class RepairConfig(_Base):
    """Geometry repair pipeline (beyond a single make_valid pass)."""

    enabled: bool = False
    make_valid: bool = True
    # Tolerances are metres after to_metric reprojection.
    snap_tolerance: float = 0.0
    drop_slivers_area: float = 0.0
    dissolve_duplicates: bool = False
    # none = in-memory only; file = GeoPackage sidecar; postgis = UPDATE (dry_run default)
    write_mode: str = "file"
    postgis: PostgisWriteConfig = Field(default_factory=PostgisWriteConfig)

    @field_validator("write_mode")
    @classmethod
    def _check_write_mode(cls, v: str) -> str:
        allowed = {"none", "file", "postgis"}
        if v not in allowed:
            raise ValueError(f"write_mode must be one of {sorted(allowed)}, got {v!r}")
        return v

    @model_validator(mode="after")
    def _no_dissolve_with_postgis(self) -> RepairConfig:
        """PostGIS write-back only UPDATEs geometries; it cannot DELETE dissolved rows."""
        if self.dissolve_duplicates and self.write_mode == "postgis":
            raise ValueError(
                "dissolve_duplicates cannot be used with write_mode=postgis "
                "(UPDATE cannot remove orphan duplicate rows); "
                "use write_mode=file or none"
            )
        return self


class GeometryCheck(_Base):
    enabled: bool = True
    severity: Severity = Severity.ERROR
    valid: bool = True
    no_empty: bool = True
    no_missing: bool = True
    fix: bool = False  # legacy: make_valid during the check + *.fixed.gpkg via --fix-output
    repair: RepairConfig = Field(default_factory=RepairConfig)


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
    # Named alias expands to flag defaults (explicit keys still win). See RULESETS.
    ruleset: str | None = None
    no_overlaps: bool = False  # polygons should not overlap each other
    no_gaps: bool = False  # dissolve-union interior holes (heuristic)
    no_coverage_gaps: bool = False  # AOI/extent minus union (coverage gaps)
    no_dangles: bool = False  # line endpoints should connect to the network
    coincident_edges: bool = False  # almost-adjacent / ragged shared boundaries
    coverage_area_ratio: bool = False  # fast layer self-overlap metric
    # Tolerances are expressed in metres (data is reprojected to a metric CRS).
    min_area: float = 0.0  # ignore overlaps/gaps smaller than this (sliver noise)
    snap_tolerance: float = 0.0  # snap line endpoints within this distance for dangles
    boundary_tolerance: float = 0.0  # coincident-edge ε (0 → snap_tolerance or 0.01)
    # Overlap algorithm: auto uses pairwise below pairwise_threshold, else coverage metric.
    algorithm: Literal["auto", "pairwise", "coverage"] = "auto"
    pairwise_threshold: int = 5_000
    max_pairs: int = 100_000  # cap pairwise intersection evaluations
    # Fishnet tile edge length (metres) for large pairwise overlap passes; 0 = off.
    # Tiles use an overlap buffer of ``snap_tolerance`` (or boundary_tolerance).
    tile_size: float = 0.0
    # Coverage / dangle AOI (path to polygon file, or bbox in source CRS).
    aoi: str | None = None
    aoi_bbox: list[float] | None = None  # [minx, miny, maxx, maxy] in source CRS
    ignore_boundary: bool = False  # degree-1 endpoints on AOI/extent edge are OK
    min_degree: int = 2  # endpoints with fewer connections than this are dangles

    @model_validator(mode="before")
    @classmethod
    def _expand_ruleset(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        name = data.get("ruleset")
        if not name:
            return data
        from geoqa.topology_rulesets import RULESETS, expand_ruleset

        if name not in RULESETS:
            raise ValueError(
                f"unknown topology.ruleset {name!r}; "
                f"choose one of {sorted(RULESETS)}"
            )
        return expand_ruleset(name, data)


class ColumnSchema(_Base):
    """One column in a schema conformance check."""

    type: str | None = None  # string | number | integer | boolean
    required: bool = False
    min: float | None = None
    max: float | None = None


class GeometrySchema(_Base):
    types: list[str] = Field(default_factory=list)  # Polygon, Point, …
    srid: int | None = None


class PrecisionSchema(_Base):
    max_decimal_places: int | None = None
    max_xy_resolution: float | None = None  # metres in metric CRS (optional)


class SchemaCheck(_Base):
    enabled: bool = False
    severity: Severity = Severity.ERROR
    # Inline schema, or path to a YAML/JSON schema file (relative to suite base_dir).
    path: str | None = None
    columns: dict[str, ColumnSchema] = Field(default_factory=dict)
    geometry: GeometrySchema = Field(default_factory=GeometrySchema)
    precision: PrecisionSchema = Field(default_factory=PrecisionSchema)


class MetadataCheck(_Base):
    """Lightweight metadata completeness (not a full ISO 19115 validator)."""

    enabled: bool = False
    severity: Severity = Severity.WARN
    # Sidecar relative to the layer source file, or absolute path.
    sidecar: str = "metadata.xml"
    required_keys: list[str] = Field(
        default_factory=lambda: ["title", "crs", "date", "lineage"]
    )


class LayerConfig(_Base):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    crs: CrsCheck = Field(default_factory=CrsCheck)
    geometry: GeometryCheck = Field(default_factory=GeometryCheck)
    duplicates: DuplicatesCheck = Field(default_factory=DuplicatesCheck)
    attributes: AttributesCheck = Field(default_factory=AttributesCheck)
    topology: TopologyCheck = Field(default_factory=TopologyCheck)
    # YAML key remains ``schema:`` (alias); attr avoids clashing with BaseModel.schema.
    layer_schema: SchemaCheck = Field(
        default_factory=SchemaCheck, alias="schema"
    )
    metadata: MetadataCheck = Field(default_factory=MetadataCheck)


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

    # Scale / ops (Phase F).
    chunk_size: int | None = None  # row batches for chunk-safe checks (files)
    prefer_sql: bool = False  # push cheap geometry checks to PostGIS when possible

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
        if self.chunk_size is not None and self.chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")
        return self


class CacheConfig(_Base):
    enabled: bool = False
    dir: str = ".geoqa/cache"


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
    cache: CacheConfig = Field(default_factory=CacheConfig)

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
        cfg = resolve_layer_model().model_validate(merged)
        # Resolve relative paths against the suite YAML directory.
        if cfg.layer_schema.path:
            resolved = self.resolve_path(cfg.layer_schema.path)
            cfg.layer_schema = cfg.layer_schema.model_copy(update={"path": str(resolved)})
        if cfg.topology.aoi:
            resolved_aoi = self.resolve_path(cfg.topology.aoi)
            cfg.topology = cfg.topology.model_copy(update={"aoi": str(resolved_aoi)})
        return cfg

    def resolve_path(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (self.base_dir / p)


def check_config_for(layer_cfg: Any, check_name: str) -> Any:
    """Return the config object for a registry check name on a layer config.

    Built-in ``schema`` checks use the ``layer_schema`` field (YAML key ``schema``)
    to avoid clashing with pydantic's ``BaseModel.schema``.
    """
    if check_name == "schema":
        return getattr(layer_cfg, "layer_schema", None)
    return getattr(layer_cfg, check_name, None)


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

    extra: dict[str, Any] = {}
    for s in specs:
        if s.name in LayerConfig.model_fields:
            continue
        # Built-in schema check uses ``layer_schema`` (YAML alias ``schema``).
        if s.name == "schema" and "layer_schema" in LayerConfig.model_fields:
            continue
        extra[s.name] = (s.config_model, Field(default_factory=s.config_model))
    model = (
        LayerConfig
        if not extra
        else create_model("LayerConfigExtended", __base__=LayerConfig, **extra)
    )
    _layer_model_cache = (cache_key, model)
    return model


def config_json_schema() -> dict[str, Any]:
    """JSON Schema for a ``geoqa.yml`` file (built-in + plugin check keys).

    Useful for editor autocomplete/validation. ``defaults`` and each entry under
    ``layers`` are typed as the resolved layer-config model so check keys are
    described precisely.
    """
    layer_model = resolve_layer_model()
    schema_model = create_model(
        "GeoqaConfig",
        __base__=_Base,
        version=(int, Field(default=1)),
        name=(str, Field(default="geoqa suite")),
        sources=(list[SourceSpec], Field(default_factory=list)),
        defaults=(layer_model, Field(default_factory=layer_model)),
        layers=(dict[str, layer_model], Field(default_factory=dict)),  # type: ignore[valid-type]
        report=(ReportConfig, Field(default_factory=ReportConfig)),
        cache=(CacheConfig, Field(default_factory=CacheConfig)),
    )
    schema = schema_model.model_json_schema()
    schema["title"] = "geoqa configuration"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


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
