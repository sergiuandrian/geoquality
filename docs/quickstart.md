# Quickstart

```bash
pip install geoqa

# 1. Generate a starter config (or pick a domain profile)
geoqa init
# geoqa init --profile parcels

# 2. (optional) generate messy demo data
cd examples && python make_sample_data.py && cd ..

# 3. Run it
geoqa run -c examples/geoqa.yml --html geoqa-report.html
```

The console shows a per-layer summary; `geoqa-report.html` is a standalone,
shareable report with an interactive map of any offending features. The process
exits non-zero when any `error`-severity check fails — perfect for CI.

Starter configs prefer **`geometry.repair`** (off by default) and
**`no_coverage_gaps`** for parcels — set an `aoi` / `aoi_bbox` for reliable
coverage. See [Repair](repair.md) and [Checks](checks.md).

## Domain profiles

Skip inventing rules from scratch:

```bash
geoqa init --profile parcels -p geoqa.yml
geoqa init --profile roads
geoqa init --profile admin_boundaries
geoqa init --profile addresses
```

See [Profiles](profiles.md) for what each pack checks.

## Common commands

```bash
geoqa run -c geoqa.yml          # run all checks
geoqa validate -c geoqa.yml     # validate the config only
geoqa list-checks               # list available checks (built-in + plugins)
geoqa schema -o geoqa.schema.json   # JSON Schema for editor autocomplete
geoqa init                      # write a starter geoqa.yml
geoqa init --profile parcels    # write a domain profile
```

## Library use

```python
from geoqa.config import load_suite
from geoqa.engine import run_suite
from geoqa.reporting import write_html

report = run_suite(load_suite("geoqa.yml"))
write_html(report, "report.html")
print("passed" if report.passed else "failed", report.counts)
```
