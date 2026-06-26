# CI / pre-commit

## pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/sergiuandrian/geoquality
    rev: v0.4.0
    hooks:
      - id: geoqa
        args: ["run", "-c", "geoqa.yml", "--html", "geoqa-report.html"]
```

## Exit codes

`geoqa run` exits non-zero when checks fail, controlled by
`--fail-on {error,warn,never}` (default `error`):

| Threshold | Run fails on |
|---|---|
| `error` | any FAIL (error-severity) or ERROR (a check that crashed) |
| `warn` | additionally any WARN |
| `never` | never (report-only; always exit 0) |

## GitHub Actions

```yaml
- run: pip install "geoqa @ git+https://github.com/sergiuandrian/geoquality@v0.4.0"
- run: geoqa run -c geoqa.yml --junit geoqa-junit.xml --html geoqa-report.html
- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: geoqa-report
    path: geoqa-report.html
```

Point your test reporter at `geoqa-junit.xml` to surface results in the run summary.
