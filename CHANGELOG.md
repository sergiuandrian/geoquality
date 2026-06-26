# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- PyPI publish workflow on tag push (`build` + Trusted Publishing).
- `CHANGELOG.md` and `CONTRIBUTING.md`.
- MkDocs Material documentation site and a GitHub Pages deploy workflow.
- README badges (CI, release, license, Python versions).

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

### Changed
- `LayerConfig` is resolved dynamically so plugin check keys validate while
  built-ins keep `extra="forbid"` typo detection.
- CI bumped to `actions/checkout@v7` and `actions/upload-artifact@v7`.

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

[Unreleased]: https://github.com/sergiuandrian/geoquality/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/sergiuandrian/geoquality/releases/tag/v0.4.0
[0.2.0]: https://github.com/sergiuandrian/geoquality/releases/tag/v0.2.0
[0.1.0]: https://github.com/sergiuandrian/geoquality/releases/tag/v0.1.0
