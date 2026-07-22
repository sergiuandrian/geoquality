# Scale & ops

geoqa can validate large layers without always loading everything into RAM, and
can skip unchanged layers on re-runs. These features target **country-scale**
file / PostGIS workflows; they are opt-in.

## Chunked file validation

```yaml
sources:
  - path: big.gpkg
    chunk_size: 50000
```

When `chunk_size` is set, **chunk-safe** checks (`crs`, `geometry`,
`attributes` without `unique`) run on pyogrio row batches. Checks that need
global context (`duplicates`, `topology`) are **skipped with a warning** —
disable `chunk_size` or run a separate full-layer pass for those.

## PostGIS SQL pushdown

```yaml
sources:
  - connection: "postgresql://user@host/gis"
    table: public.parcels
    prefer_sql: true
```

With `prefer_sql: true` and a `table` (not a free-form `query`), geometry
`valid` / `no_empty` / `no_missing` run as `ST_IsValid` / `ST_IsEmpty` / null
probes in the database. If the layer config only enables those geometry checks
(no CRS/duplicates/topology/attribute rules), geoqa **does not** materialize a
GeoDataFrame.

## Tiled topology overlaps

```yaml
topology:
  enabled: true
  no_overlaps: true
  algorithm: pairwise
  tile_size: 5000        # metres in the metric CRS
  snap_tolerance: 0.5    # used as the tile overlap buffer
```

Large pairwise overlap passes can partition features into a fishnet. Each tile
is buffered by `snap_tolerance` (or `boundary_tolerance`) so pairs that straddle
tile edges are still evaluated. A buffer of `0` can miss cross-boundary pairs.

## Fingerprint cache

```yaml
cache:
  enabled: true
  dir: .geoqa/cache
```

Cache key = hash(file mtime + size + layer config fragment + geoqa version).
When a prior run for that fingerprint was all `pass`/`skip`, the layer is
skipped. Use `geoqa run --no-cache` to force a full run.

## Progress

`geoqa run` emits per-layer / per-chunk progress (Rich on a TTY; log lines
otherwise). Programmatic callers can pass a `progress` callback that accepts a
`ProgressEvent` (or a legacy `str` layer name).
