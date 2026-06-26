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

Every check supports `enabled` (bool) and `severity` (`error` | `warn` | `info`).
Only `error`-severity failures fail the run (tune with `--fail-on`).

## Validation & autocomplete

- `geoqa validate -c geoqa.yml` checks the schema and every per-layer rule.
- `geoqa schema -o geoqa.schema.json` emits a JSON Schema (covering built-in and
  plugin check keys) for editor autocomplete/validation.

Unknown keys are rejected (`extra = forbid`) so typos surface immediately.
