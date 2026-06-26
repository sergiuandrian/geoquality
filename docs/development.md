# Development

```bash
git clone https://github.com/sergiuandrian/geoquality
cd geoquality
pip install -e ".[dev]"
```

## Gates (run in CI)

```bash
ruff check src tests   # lint
mypy                   # type-check
pytest                 # tests + coverage gate (>=80%)
```

## Docs

```bash
pip install -e ".[docs]"
mkdocs serve           # live preview at http://127.0.0.1:8000
mkdocs build --strict  # what CI runs
```

See [CONTRIBUTING.md](https://github.com/sergiuandrian/geoquality/blob/main/CONTRIBUTING.md)
for the branching model and release process.
