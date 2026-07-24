# Checks

Run `geoqa list-checks` for the live reference (including any installed plugins).

## crs
`required`, `allowed_epsg`, `expected_epsg`

Flags undefined CRS and CRS outside an allow-list / not matching an expected EPSG.

## geometry
`valid`, `no_empty`, `no_missing`, `fix`, `repair`

Validates geometry with GEOS. Prefer `geometry.repair` for snap / sliver /
dissolve pipelines (see [Repair](repair.md)). Legacy `fix: true` still runs
`shapely.make_valid()` and writes a cleaned layer to `--fix-output`.

## duplicates
`exact`; `fuzzy.{enabled, predicate, min_overlap, max_distance}`

Exact duplicates use normalized WKB; fuzzy duplicates use a spatial join plus a
per-pair IoU (polygons) or distance (points/lines) test in a metric CRS.

## attributes
`required`, `not_null`, `unique`, `max_null_fraction`, `domains.{allowed, min, max, regex}`

Attribute completeness, uniqueness and domain rules.

## topology

Coverage / network topology checks — **not** a cadastral-certified topology
engine. Tolerances are in metres; geographic layers are auto-reprojected to a
local UTM zone (or EPSG:6933 for near-global extents).

Named **rulesets** expand common flag sets: `cadastre_coverage`, `network`,
`admin_coverage` (see [Schema & packs](schema.md)).

### Flags

| Flag | Meaning |
|------|---------|
| `no_overlaps` | Polygons must not overlap (see algorithm below) |
| `coverage_area_ratio` | Fast layer self-overlap metric (`sum(area) / area(union)`) |
| `no_gaps` | **Heuristic:** interior holes of the *dissolved* union |
| `no_coverage_gaps` | AOI (or `total_bounds`) minus union — true coverage gaps |
| `no_dangles` | Line endpoints with degree &lt; `min_degree` (default 2) |
| `coincident_edges` | Almost-adjacent neighbours or ragged shared boundaries |
| `min_area` | Ignore overlap/gap parts smaller than this (m²) |
| `snap_tolerance` | Endpoint snap grid for dangles (m) |
| `boundary_tolerance` | ε for coincident-edge search (m; 0 → snap or 0.01) |
| `algorithm` | `auto` \| `pairwise` \| `coverage` for `no_overlaps` |
| `pairwise_threshold` | `auto` switches to coverage metric above this feature count |
| `max_pairs` | Cap on pairwise intersection evaluations |
| `aoi` / `aoi_bbox` | Coverage / boundary AOI (file path, or bbox in **source CRS**) |
| `ignore_boundary` | Degree-1 endpoints on AOI/extent edge are allowed |
| `tile_size` | Fishnet tile edge (m) for pairwise overlaps; buffer = snap/boundary tol |

### Heuristics vs coverage (read this)

- **`no_gaps`** only looks for *interior rings* after `unary_union`. It does
  **not** detect a missing parcel that leaves a hole against an external AOI,
  and it can miss gaps that open to the exterior. Prefer **`no_coverage_gaps`**
  with an explicit `aoi` or `aoi_bbox` for parcel/admin coverage.
- **`no_coverage_gaps` without AOI** uses the layer `total_bounds` rectangle.
  Sparse or coastal layers will report large exterior “gaps” — that is expected.
  Without an explicit `aoi` / `aoi_bbox`, geoqa returns **WARN** even when zero
  gaps are found (inconclusive), so CI does not treat total_bounds as a real AOI.
- **`no_overlaps` + `algorithm: auto`**: pairwise (vectorized spatial index) below
  `pairwise_threshold` (default 5000); above that, a fast coverage excess-area
  metric (same idea as `coverage_area_ratio`). Pairwise results name offenders;
  coverage only reports layer-level excess.
- **`no_dangles`** counts snapped endpoint degree. With `ignore_boundary: true`,
  endpoints within `snap_tolerance` of the AOI (or extent) boundary are treated
  as legitimate network ends. Issue details include `lon`/`lat` (EPSG:4326) when
  reprojection succeeds.
- **`coincident_edges`**: flags pairs that are within ε but do not touch
  (`almost_adjacent`), and touching pairs whose *near* boundary Hausdorff exceeds
  ε (`ragged_boundary`). Perfect shared edges pass.

```yaml
topology:
  enabled: true
  severity: error
  no_overlaps: true
  algorithm: auto
  no_coverage_gaps: true
  aoi_bbox: [0, 0, 1000, 1000]   # in source CRS before metric reprojection
  coincident_edges: true
  boundary_tolerance: 0.5
  min_area: 0.5
```

## schema
`columns`, `geometry.types` / `srid`, `precision`, optional `path` to external YAML

Column presence/types, geometry type allow-list, CRS EPSG, coordinate precision.
See [Schema & packs](schema.md).

## metadata
`sidecar`, `required_keys`

Lightweight sidecar completeness (XML tags or `key: value` lines) — not ISO 19115.

ISO 19157 DQ element tags on results are documented in [ISO 19157](iso19157.md).

!!! tip
    Every check also supports `enabled` and `severity` (`error` | `warn` | `info`).
