# Domain profiles

Profiles are ready-made `geoqa.yml` templates for common GIS layers. They are
**starting points** — edit CRS allow-lists, layer names, and attribute columns
to match your data.

## Usage

```bash
geoqa init --profile parcels -p geoqa.yml
geoqa validate -c geoqa.yml
# point sources at your data, then:
geoqa run -c geoqa.yml --html report.html
```

The same YAML files live in the package (`geoqa.profiles`) and under
[`examples/profiles/`](https://github.com/sergiuandrian/geoquality/tree/develop/examples/profiles)
in the repository.

## Available profiles

| Profile | Layer focus | Highlights |
|---|---|---|
| `parcels` | Cadastre / parcel polygons | `parcel_id` unique; overlaps + gaps; fuzzy near-dupes |
| `roads` | Road / network lines | `name` required; dangles; `lanes` / `surface` domains |
| `admin_boundaries` | Administrative polygons | `name` / `code`; no overlaps |
| `addresses` | Address / POI points | unique `id`; fuzzy near-duplicates by distance |

Unknown profile names exit with code 2 and list the valid options.

## Notes

- Topology tolerances (`min_area`, `snap_tolerance`) are in **metres**;
  geographic layers are reprojected automatically (UTM, or EPSG:6933 for
  near-global extents).
- Layer keys under `layers:` must match your file stem, GeoPackage layer name,
  or PostGIS `name:` override.
- Profiles do not certify INSPIRE / national standards — they encode common
  practical QA rules.
