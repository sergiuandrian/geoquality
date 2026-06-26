# Checks

Run `geoqa list-checks` for the live reference (including any installed plugins).

## crs
`required`, `allowed_epsg`, `expected_epsg`

Flags undefined CRS and CRS outside an allow-list / not matching an expected EPSG.

## geometry
`valid`, `no_empty`, `no_missing`, `fix`

Validates geometry with GEOS. With `fix: true`, invalid geometries are repaired
via `shapely.make_valid()` and the cleaned layer is written to `--fix-output`.

## duplicates
`exact`; `fuzzy.{enabled, predicate, min_overlap, max_distance}`

Exact duplicates use normalized WKB; fuzzy duplicates use a spatial join plus a
per-pair IoU (polygons) or distance (points/lines) test in a metric CRS.

## attributes
`required`, `not_null`, `unique`, `max_null_fraction`, `domains.{allowed, min, max, regex}`

Attribute completeness, uniqueness and domain rules.

## topology
`no_overlaps`, `no_gaps`, `no_dangles`, `min_area`, `snap_tolerance`

Overlaps, interior gaps, and dangling line endpoints. Tolerances are in metres;
geographic layers are auto-reprojected to a local UTM zone for metric math.

!!! tip
    Every check also supports `enabled` and `severity` (`error` | `warn` | `info`).
