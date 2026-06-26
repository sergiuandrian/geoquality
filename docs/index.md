# geoqa

**Data quality for geospatial data — Great Expectations for GIS.**

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
| Check topology | PostGIS / QGIS desktop | ✅ overlaps / gaps / dangles |
| Attribute completeness | `isnull()` | ✅ required / not-null / domain |
| Run on a folder / PostGIS | — | ✅ |
| Human-readable report | — | ✅ HTML (+ map) / console / JSON |
| Machine output | — | ✅ JSON / JUnit XML / GeoJSON |
| Configure without code | — | ✅ YAML |
| Extend without forking | — | ✅ plugin entry points |

## Install

```bash
pip install "geoqa @ git+https://github.com/sergiuandrian/geoquality@v0.4.0"
# with PostGIS support:
pip install "geoqa[postgis] @ git+https://github.com/sergiuandrian/geoquality@v0.4.0"
```

See [Quickstart](quickstart.md) to run your first suite.
