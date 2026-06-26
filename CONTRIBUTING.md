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

1. Open a PR `develop -> main`; ensure CI is green.
2. Bump the version in `pyproject.toml` and `src/geoqa/__init__.py`, and move the
   `CHANGELOG.md` `Unreleased` items under the new version.
3. Merge, then tag: `git tag -a vX.Y.Z -m "geoqa vX.Y.Z" && git push origin vX.Y.Z`.
4. The **Release** workflow builds the sdist/wheel and publishes to PyPI via
   Trusted Publishing; create the GitHub release with notes from the changelog.

## Code style

- Keep changes focused; match the surrounding style.
- Comments explain *why*, not *what*.
- Public functions get type hints (checked by mypy).
