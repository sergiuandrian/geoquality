# INSPIRE AU starter pack

**Not a certified INSPIRE validator.** This pack shows how to wire schema,
domains, topology rulesets, and metadata checks for an Administrative Units–
like layer.

```bash
# from repo root
geoqa run -c examples/packs/inspire_au/geoqa.yml
```

Files:

| Path | Role |
|------|------|
| `geoqa.yml` | Suite config |
| `schema.yml` | Column / geometry / precision schema |
| `domains/nationalLevel.yml` | Documented code list (mirrored in attributes.domains) |
| `data/au_sample.gpkg` | Tiny synthetic polygons |
| `data/metadata.xml` | Sidecar metadata |
