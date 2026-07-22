# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-07-22

### Added
- Schema / ISO / packs: `schema` and `metadata` checks, ISO 19157 `dq_element`
  tags on results, topology `ruleset` aliases, and
  `examples/packs/inspire_au` starter pack (not a certified INSPIRE validator).
- Scale & ops: `chunk_size` file batches, PostGIS `prefer_sql` geometry
  pushdown, topology `tile_size` fishnet overlaps, fingerprint `cache`, and
  richer progress events (`--no-cache`).
- Topology upgrade: vectorized pairwise overlaps, `coverage_area_ratio`,
  `no_coverage_gaps` (AOI/`aoi_bbox`), network dangles with `ignore_boundary`
  + lon/lat details, and `coincident_edges` (almost-adjacent / ragged bounds).
- Geometry **repair pipeline** (`geometry.repair`): make_valid, snap,
  drop_slivers, dissolve_duplicates, file / PostGIS write-back (dry-run
  default), `--repair-audit`, and `--i-know-what-im-doing` for live UPDATEs.

### Changed
- Package version: **0.6.0** (still Development Status **Beta**; not 1.0).
- Pre-commit / CI docs pin examples to `v0.6.0`.
- Docs label dissolve-hole `no_gaps` as a heuristic; prefer `no_coverage_gaps`
  with an explicit AOI for parcel/admin coverage.
- Parcels / roads / admin profiles enable the new topology flags.

## [0.5.0] - 2026-07-21

### Added
- Domain **profiles** for parcels, roads, admin boundaries, and addresses
  (`geoqa init --profile <name>`; also under `examples/profiles/`).
- `Issue.row_index` so attribute failures (unique/domain) appear on the HTML map
  and in GeoJSON failure exports.
- PostGIS table-name quoting for schema-qualified / mixed-case identifiers;
  SQLAlchemy engines are disposed after each load.
- PyPI-first install docs; release checklist in `CONTRIBUTING.md`.

### Changed
- Package version and metadata: **0.5.0**, Development Status **Beta**.
- Pre-commit / CI docs pin examples to `v0.5.0`.
- Changelog: items previously listed under Unreleased that shipped in 0.4.0
  remain documented under that release.

### Fixed
- Attribute unique/domain offenders were missing from map/GeoJSON collection
  when `feature_id` was an attribute value rather than a row index.
- Raw PostGIS `SELECT * FROM {table}` broke on schema-qualified names and did
  not release connection pools.

## [0.4.0] - 2026-06-26

### Added
- **Plugin registry** (`geoqa.registry`): third-party checks are discovered via
  the `geoqa.checks` entry point group; the engine dispatches through it.
- **PostGIS / SQLAlchemy** data source (`connection` + `table`/`query`,
  `geom_column`) with credential redaction; optional `[postgis]` extra.
- **Per-layer parallelism** via `geoqa run --workers N`.
- **JUnit XML** reporter (`--junit`) for CI-native test reporting.
- **GeoJSON-of-failures** export (`--geojson-out`) — offending features per layer.
- **JSON Schema** for `geoqa.yml` via `geoqa schema`.
- Interactive **Leaflet map** of offending features embedded in the HTML report.
- PyPI publish workflow on tag push (`build` + Trusted Publishing).
- `CHANGELOG.md` and `CONTRIBUTING.md`.
- MkDocs Material documentation site and a GitHub Pages deploy workflow.
- README badges (CI, release, license, Python versions).
- Global equal-area CRS fallback (EPSG:6933) in `to_metric` for near-global
  extents (avoids GEOS NaN/Inf crashes on world-spanning data).

### Changed
- `LayerConfig` is resolved dynamically so plugin check keys validate while
  built-ins keep `extra="forbid"` typo detection.
- CI bumped to `actions/checkout@v7` and `actions/upload-artifact@v7`.

### Fixed
- Attribute regex domains crashed on nulls (`pd.NA`); numeric min/max domains
  silently passed non-numeric present values.

## [0.2.0] - 2026-06-26

### Added
- CRS-aware geometry math: topology and fuzzy-duplicate checks reproject
  geographic layers to a metric UTM CRS before area/distance/IoU.
- Configurable topology tolerances (`min_area`, `snap_tolerance`).
- `--fail-on {error,warn,never}` exit-code threshold and `geoqa validate`.
- Structured logging (`--log-level`, `--quiet`).
- Test suite with an 80%+ coverage gate, plus mypy and pip-audit CI jobs.

### Fixed
- Windows console crash from a non-ASCII character in `geoqa init`.
- `ReportConfig.max_issues_per_check` is now wired through the reporters.
- Mixed-geometry handling in fuzzy duplicate detection.

## [0.1.0] - 2026-06-26

### Added
- Initial MVP: YAML-configured checks for CRS, geometry, duplicates, attributes
  and topology over folders of geospatial files, with console/JSON/HTML reports
  and a pre-commit hook.

[Unreleased]: https://github.com/sergiuandrian/geoquality/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/sergiuandrian/geoquality/releases/tag/v0.6.0
[0.5.0]: https://github.com/sergiuandrian/geoquality/releases/tag/v0.5.0
[0.4.0]: https://github.com/sergiuandrian/geoquality/releases/tag/v0.4.0
[0.2.0]: https://github.com/sergiuandrian/geoquality/releases/tag/v0.2.0
[0.1.0]: https://github.com/sergiuandrian/geoquality/releases/tag/v0.1.0
