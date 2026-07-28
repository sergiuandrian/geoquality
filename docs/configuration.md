# Configuration

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
    repair:                # prefer this over legacy fix: true
      enabled: false
      make_valid: true
      write_mode: file
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
    topology:
      enabled: true
      no_overlaps: true
      no_coverage_gaps: true
      # aoi_bbox: [minx, miny, maxx, maxy]   # source CRS
  roads:
    attributes:
      domains:
        lanes: { min: 1, max: 8 }
    topology: { enabled: true, no_dangles: true }
```

Every check supports `enabled` (bool) and `severity` (`error` | `warn` | `info`).
Only `error`-severity failures fail the run (tune with `--fail-on`).

Prefer **`no_coverage_gaps` + AOI** for polygon coverage. The dissolve-hole
heuristic `no_gaps` is weaker — see [Checks](checks.md). Prefer
**`geometry.repair`** over legacy `geometry.fix` — see [Repair](repair.md).

## Validation & autocomplete

- `geoqa validate -c geoqa.yml` checks the schema and every per-layer rule.
- `geoqa schema -o geoqa.schema.json` emits a JSON Schema (covering built-in and
  plugin check keys) for editor autocomplete/validation.

Unknown keys are rejected (`extra = forbid`) so typos surface immediately.
