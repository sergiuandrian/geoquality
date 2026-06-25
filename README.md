# geoqa

**Data quality for geospatial data".**

The primitives already exist (`gdf.is_valid`, `make_valid()`, `pyproj`, PostGIS
`ST_*`, the QGIS Topology Checker). What's missing is the *orchestration*: one
tool that runs all of them over a folder of files, is configured with **YAML
(no code)**, produces a **human-readable report**, and drops into **CI / a
pre-commit hook**. That's geoqa.

| Need | Existing tooling | geoqa |
|---|---|---|
| Validate geometry | `is_valid` / `make_valid` | ✅ + auto-fix |
| Validate CRS | `gdf.crs` / `gdalinfo` | ✅ allow-list / expected EPSG |
| Find duplicates | `duplicated()` + `sjoin` | ✅ exact **and** fuzzy |
| Check topology | PostGIS / QGIS desktop | ✅ overlaps / gaps / dangles in pure Python |
| Attribute completeness | `isnull()` | ✅ required / not-null / domain rules |
| Run everything on a folder | — | ✅ |
| Human-readable report | — | ✅ HTML + console + JSON |
| Configure rules without code | — | ✅ YAML |
| CI / pre-commit integration | — | ✅ exit codes + hook |

## Install

```bash
pip install -e .
# geospatial wheels (geopandas/shapely/pyproj/pyogrio) install automatically
```

## Quickstart

```bash
# 1. Generate the starter config
geoqa init

# 2. (optional) generate messy demo data
cd examples && python make_sample_data.py && cd ..

# 3. Run it
geoqa run -c examples/geoqa.yml --html geoqa-report.html
```

The console shows a per-layer summary; `geoqa-report.html` is a standalone,
shareable report. The process exits non-zero when any `error`-severity check
fails — perfect for CI.

## Configuration

A single `geoqa.yml` drives everything. `defaults` applies to every layer;
per-layer blocks override individual keys.

```yaml
version: 1
name: "My GIS QA suite"

sources:
  - path: "data/"          # folder...
    pattern: "*.gpkg"      # ...with a glob (or point at a single file)

defaults:
  crs:
    required: true
    allowed_epsg: [4326, 3857]
  geometry:
    valid: true
    fix: false             # true -> repair with make_valid() and export
  duplicates:
    exact: true
    fuzzy: { enabled: true, predicate: intersects, min_overlap: 0.9 }

layers:
  parcels:
    attributes:
      required: [parcel_id, zone]
      unique: [parcel_id]
      domains:
        zone: { allowed: [residential, commercial, industrial] }
    topology: { enabled: true, no_overlaps: true, no_gaps: true }
  roads:
    attributes:
      domains:
        lanes: { min: 1, max: 8 }
    topology: { enabled: true, no_dangles: true }
```

Every check supports `enabled` (bool) and `severity` (`error` | `warn` |
`info`). Only `error`-severity failures fail the run. Run `geoqa list-checks`
for the full key reference.

### Checks

- **crs** — `required`, `allowed_epsg`, `expected_epsg`
- **geometry** — `valid`, `no_empty`, `no_missing`, `fix`
- **duplicates** — `exact`; `fuzzy.{enabled, predicate, min_overlap, max_distance}`
- **attributes** — `required`, `not_null`, `unique`, `max_null_fraction`, `domains.{allowed, min, max, regex}`
- **topology** — `no_overlaps`, `no_gaps`, `no_dangles`

## Auto-fixing geometry

```bash
geoqa run -c geoqa.yml --fix-output ./fixed
```

With `geometry.fix: true`, invalid geometries are repaired via
`shapely.make_valid()` and the cleaned layer is written to the output folder.

## CI / pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/your-org/geoqa
    rev: v0.1.0
    hooks:
      - id: geoqa
        args: ["run", "-c", "geoqa.yml", "--html", "geoqa-report.html"]
```

## Library use

```python
from geoqa.config import load_suite
from geoqa.engine import run_suite
from geoqa.reporting import write_html

report = run_suite(load_suite("geoqa.yml"))
write_html(report, "report.html")
print("passed" if report.passed else "failed", report.counts)
```

## Architecture

```
config (YAML + pydantic)  ->  datasource (folder -> layers)  ->  engine
        |                                                          |
        +-- resolves per-layer rules        runs checks: crs / geometry /
                                              duplicates / attributes / topology
                                                          |
                                            results  ->  reporting (console / JSON / HTML)
```

## License

Apache-2.0.
