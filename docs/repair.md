# Geometry repair

geoqa can **repair** geometries after reporting problems (detect-then-repair).
The safe default is still a **sidecar file** — the source PostGIS database is
never mutated unless you explicitly opt in. Repair runs **after** all checks in
the same `geoqa run`, so topology/duplicates still see the original geometries
unless you enable `then_recheck` or re-run against the fixed output.

## Legacy: `geometry.fix`

```yaml
geometry:
  valid: true
  fix: true
```

```bash
geoqa run -c geoqa.yml --fix-output ./fixed
```

Invalid geometries are repaired with `shapely.make_valid()` and written to
`./fixed/<layer>.fixed.gpkg`.

## Pipeline: `geometry.repair`

```yaml
geometry:
  valid: true
  repair:
    enabled: true
    make_valid: true
    snap_tolerance: 0.1          # metres (after metric reprojection)
    drop_slivers_area: 0.5       # m² — drop polygon parts below this area
    dissolve_duplicates: true    # keep first of exact-duplicate WKB groups
    write_mode: file             # none | file | postgis
    then_recheck: false          # re-run geometry (+ topology) as *.after_repair
    postgis:
      connection: "postgresql+psycopg://user@host/db"
      table: "public.parcels"
      id_column: id
      geom_column: geom
      dry_run: true              # default — log only, no UPDATE
```

```bash
geoqa run -c geoqa.yml \
  --fix-output ./fixed \
  --repair-audit ./repair-audit.json
```

### Ops (in order)

| Op | Config key | Effect |
|---|---|---|
| `make_valid` | `make_valid` | `shapely.make_valid` on invalid geoms |
| `snap` | `snap_tolerance` | `set_precision` grid in metres |
| `drop_slivers` | `drop_slivers_area` | remove small polygon parts |
| `dissolve_duplicates` | `dissolve_duplicates` | drop exact duplicate geometries (keep first) |

Tolerances are always interpreted in **metres** via `to_metric` (UTM or
EPSG:6933 for near-global data).

### Write modes

- **`none`** — repair geometries in memory for write-back / audit only.
  **Checks in the same run still see the unrepaired layer** (repair runs after
  the check loop) unless `then_recheck: true`. Use a second pass or inspect the
  repaired sidecar if you need validation against fixed geometries without
  recheck.
- **`file`** — GeoPackage under `--fix-output` (required, else a warning).
- **`postgis`** — `UPDATE` by `id_column`. **`dry_run: true` by default.**
  Live writes need both `dry_run: false` **and** CLI
  `--i-know-what-im-doing`. Live writes require a resolvable EPSG CRS
  (SRID 0 is refused) and verify each UPDATE via `rowcount`.
  **`dissolve_duplicates` cannot be combined with `write_mode: postgis`**
  (UPDATE cannot delete orphan rows); use `file` or `none` instead.
  When the target column is typed single-part (`POLYGON`, …) but repaired
  geometries include Multi*/collections, geoqa emits
  `geometry.repair.postgis.column_type` **WARN** (dry-run and live) so you can
  widen the column before a live UPDATE fails.

### `then_recheck`

When `then_recheck: true`, geoqa re-runs **geometry** and (if enabled)
**topology** on the repaired layer and appends results named
`*.after_repair` (e.g. `geometry.valid.after_repair`). Pre-repair results are
kept so you can see both the original failures and the post-repair state.
Default is `false`.

### Audit trail

`--repair-audit path.json` writes one list of actions per layer (`op`,
`row_index`, `note`, optional before/after geometry hashes).

## Limitations

- `make_valid` can change geometry type (e.g. Polygon → MultiPolygon /
  GeometryCollection).
- PostGIS write-back updates geometry only (no attribute merge, no row
  deletes). Wrong `id_column` values are reported as write failures.
  `make_valid` may promote Polygon → MultiPolygon; typed PostGIS columns
  (`geometry(Polygon, …)`) can reject the UPDATE — prefer a generic
  `geometry` column or MultiPolygon for write-back targets.
- Snap / sliver ops reproject to a metric CRS and back; expect tiny
  coordinate drift.
