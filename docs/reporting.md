# Reports & integrations

`geoqa run` can emit several formats in one pass:

```bash
geoqa run -c geoqa.yml \
  --html report.html \        # standalone HTML (with an interactive map)
  --json report.json \        # machine-readable
  --junit junit.xml \         # CI-native test report
  --geojson-out ./failures    # GeoJSON of offending features (open in QGIS)
```

## HTML

A self-contained report with a per-layer table and, when failures exist, an
interactive **Leaflet map** highlighting offending features in red (click a
feature to see which checks it failed). Leaflet + OSM tiles load from a CDN; the
rest of the report works offline. Disable the map with `write_html(include_map=False)`.

## JSON

The full machine-readable report. `report.max_issues_per_check` caps the number
of example issues serialized per check.

## JUnit XML

Each layer becomes a `<testsuite>` and each check a `<testcase>`: FAIL → failure,
ERROR → error, SKIP → skipped, WARN → `system-out`. Point your CI's test
reporter at the file to surface geoqa results alongside unit tests.

## GeoJSON failures

Writes one `<layer>.failures.geojson` (WGS84) per layer, each feature tagged with
the checks it failed, so you can open the offenders directly in QGIS.
