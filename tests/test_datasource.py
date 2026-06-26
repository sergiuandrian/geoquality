"""Tests for loading files/folders into layers."""

from __future__ import annotations

from pathlib import Path

import pytest

gpd = pytest.importorskip("geopandas")

from conftest import make_parcels  # noqa: E402

from geoqa.config import load_suite  # noqa: E402
from geoqa.datasource import iter_layers  # noqa: E402


def _suite(tmp_path: Path, body: str):
    p = tmp_path / "geoqa.yml"
    p.write_text(body, encoding="utf-8")
    return load_suite(p)


def test_folder_expands_to_layers(data_dir: Path):
    suite = _suite(
        data_dir.parent,
        f'version: 1\nsources:\n  - path: "{data_dir.as_posix()}"\n',
    )
    layers = {layer.name: layer for layer in iter_layers(suite)}
    assert {"parcels", "roads"} <= set(layers)
    assert layers["parcels"].error is None
    assert layers["parcels"].gdf is not None
    assert len(layers["parcels"].gdf) == 4


def test_single_file_source(data_dir: Path):
    target = data_dir / "parcels.gpkg"
    suite = _suite(
        data_dir.parent,
        f'version: 1\nsources:\n  - path: "{target.as_posix()}"\n',
    )
    layers = list(iter_layers(suite))
    assert len(layers) == 1
    assert layers[0].name == "parcels"


def test_pattern_filters_files(data_dir: Path):
    suite = _suite(
        data_dir.parent,
        f'version: 1\nsources:\n  - path: "{data_dir.as_posix()}"\n    pattern: "*.geojson"\n',
    )
    names = {layer.name for layer in iter_layers(suite)}
    assert names == {"roads"}


def test_missing_path_yields_error_layer(tmp_path: Path):
    suite = _suite(
        tmp_path,
        'version: 1\nsources:\n  - path: "does_not_exist"\n',
    )
    layers = list(iter_layers(suite))
    assert len(layers) == 1
    assert layers[0].error is not None
    assert "no files matched" in layers[0].error


def test_name_override(data_dir: Path):
    target = data_dir / "parcels.gpkg"
    suite = _suite(
        data_dir.parent,
        f'version: 1\nsources:\n  - path: "{target.as_posix()}"\n    name: custom\n',
    )
    layers = list(iter_layers(suite))
    assert layers[0].name == "custom"


def test_parquet_source(tmp_path: Path):
    pytest.importorskip("pyarrow")
    d = tmp_path / "data"
    d.mkdir()
    make_parcels().to_parquet(d / "parcels.parquet")
    suite = _suite(
        tmp_path,
        f'version: 1\nsources:\n  - path: "{(d / "parcels.parquet").as_posix()}"\n',
    )
    layers = list(iter_layers(suite))
    assert len(layers) == 1
    assert layers[0].gdf is not None and len(layers[0].gdf) == 4


def test_unreadable_file_reports_error(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    bad = d / "broken.geojson"
    bad.write_text("this is not valid geojson", encoding="utf-8")
    suite = _suite(
        tmp_path,
        f'version: 1\nsources:\n  - path: "{bad.as_posix()}"\n',
    )
    layers = list(iter_layers(suite))
    assert len(layers) == 1
    assert layers[0].error is not None
