# Quickstart

```bash
# 1. Generate the starter config
geoqa init

# 2. (optional) generate messy demo data
cd examples && python make_sample_data.py && cd ..

# 3. Run it
geoqa run -c examples/geoqa.yml --html geoqa-report.html
```

The console shows a per-layer summary; `geoqa-report.html` is a standalone,
shareable report with an interactive map of any offending features. The process
exits non-zero when any `error`-severity check fails — perfect for CI.

## Common commands

```bash
geoqa run -c geoqa.yml          # run all checks
geoqa validate -c geoqa.yml     # validate the config only
geoqa list-checks               # list available checks (built-in + plugins)
geoqa schema -o geoqa.schema.json   # JSON Schema for editor autocomplete
geoqa init                      # write a starter geoqa.yml
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
