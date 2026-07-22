# Contributing to geoqa

Thanks for your interest in improving geoqa! This guide covers local setup, the
branching model, quality gates, and how releases are cut.

## Local setup

```bash
git clone https://github.com/sergiuandrian/geoquality
cd geoquality
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

End users should install from PyPI (`pip install geoqa`), not an editable tree.

## Quality gates

All of these run in CI and must pass before merge:

```bash
ruff check src tests     # lint
mypy                     # type-check
pytest                   # tests + coverage gate (>=80%)
```

Please add tests for new behaviour and keep coverage at or above the gate.

## Branching model

- `main` — release-ready. Tagged releases are cut from here.
- `develop` — integration branch; feature branches merge here first.
- `feature/<name>` — one branch per change, off `develop`.

```bash
git checkout develop && git pull
git checkout -b feature/my-change
# ...commit...
git push -u origin feature/my-change
# open a PR into develop
```

CI runs on every PR and on pushes to `main`/`develop`.

## Adding a check (plugin)

Checks are discovered through the `geoqa.checks` entry point group, so you can
ship one from your own package without forking. See `docs/plugins.md` for a
complete example.

## Releasing

Checklist for cutting a version (example: `v0.6.0`):

1. On `develop`, ensure CI is green and the working tree is clean.
2. Bump the version in **both** `pyproject.toml` and `src/geoqa/__init__.py`.
3. Move `CHANGELOG.md` `[Unreleased]` items under `## [X.Y.Z] - YYYY-MM-DD`,
   and update the compare links at the bottom of the file.
4. Update pinned examples that mention a tag (`examples/.pre-commit-config.yaml`,
   `docs/ci.md`, README pre-commit block) to `vX.Y.Z`.
5. Open a PR `develop -> main`; merge when CI is green.
6. From `main`:  
   `git tag -a vX.Y.Z -m "geoqa vX.Y.Z" && git push origin vX.Y.Z`
7. Confirm the **Release** GitHub Actions workflow:
   - Builds sdist/wheel and runs `twine check`
   - Publishes to PyPI via Trusted Publishing (environment `pypi`)
8. Create the GitHub Release with notes copied from the changelog.
9. Smoke-test: `pip install geoqa==X.Y.Z` in a clean venv and run `geoqa --version`.

If Trusted Publishing is not configured yet, set up a PyPI pending publisher for
this repo’s `Release` workflow before the first tag publish.

## Code style

- Keep changes focused; match the surrounding style.
- Comments explain *why*, not *what*.
- Public functions get type hints (checked by mypy).
