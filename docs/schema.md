# Schema, metadata & packs

## Schema conformance

```yaml
schema:
  enabled: true
  columns:
    nationalCode: { type: string, required: true }
    area_m2: { type: number, min: 0 }
  geometry:
    types: [Polygon, MultiPolygon]
    srid: 4326
  precision:
    max_decimal_places: 7
```

Or load an external file:

```yaml
schema:
  enabled: true
  path: schema/au.yml
```

Validates column presence/types, geometry type allow-list, CRS EPSG, and
coordinate precision (decimal places and/or metric resolution).

## Metadata completeness

```yaml
metadata:
  enabled: true
  sidecar: metadata.xml
  required_keys: [title, crs, date, lineage]
```

Looks for a sidecar next to the layer file. Keys are matched against XML tag
names and `key: value` / `key=value` lines. This is **not** a full ISO 19115
validator.

## Topology rulesets

```yaml
topology:
  ruleset: cadastre_coverage   # expands flags; explicit keys still win
  min_area: 0.5
```

Built-in aliases: `cadastre_coverage`, `network`, `admin_coverage`.

## Domain packs

Starter packs live under `examples/packs/`. They are **illustrative**, not
certified theme validators.

```bash
geoqa run -c examples/packs/inspire_au/geoqa.yml
```
