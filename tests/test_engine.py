"""Tests for the orchestration engine."""

from __future__ import annotations

from pathlib import Path

import pytest

gpd = pytest.importorskip("geopandas")

from geoqa.config import load_suite  # noqa: E402
from geoqa.engine import run_suite  # noqa: E402
from geoqa.result import Status  # noqa: E402


def _results_by_check(report, layer_name):
    layer = next(layer for layer in report.layers if layer.layer == layer_name)
    return {r.check: r for r in layer.results}


def test_run_suite_end_to_end(suite_file: Path):
    suite = load_suite(suite_file)
    report = run_suite(suite)

    assert report.finished_at is not None
    assert {layer.layer for layer in report.layers} == {"parcels", "roads"}
    assert report.has_failures("error") is True  # invalid geometry + dup id

    parcels = _results_by_check(report, "parcels")
    assert parcels["geometry.valid"].status == Status.FAIL
    assert parcels["attributes.unique[parcel_id]"].status == Status.FAIL
    assert parcels["topology.no_overlaps"].status == Status.WARN

    roads = _results_by_check(report, "roads")
    assert roads["attributes.not_null[name]"].status == Status.FAIL


def test_progress_callback_invoked(suite_file: Path):
    seen: list[str] = []
    run_suite(load_suite(suite_file), progress=seen.append)
    assert {"parcels", "roads"} <= set(seen)


def test_parallel_matches_sequential(suite_file: Path):
    suite = load_suite(suite_file)
    seq = run_suite(suite, workers=1)
    par = run_suite(suite, workers=2)

    # Same layers, same order, same per-check verdicts.
    assert [layer.layer for layer in seq.layers] == [layer.layer for layer in par.layers]
    seq_map = {(layer.layer, r.check): r.status for layer in seq.layers for r in layer.results}
    par_map = {(layer.layer, r.check): r.status for layer in par.layers for r in layer.results}
    assert seq_map == par_map


def test_parallel_progress_invoked(suite_file: Path):
    seen: list[str] = []
    run_suite(load_suite(suite_file), workers=2, progress=seen.append)
    assert {"parcels", "roads"} <= set(seen)


def test_load_error_becomes_error_result(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "broken.geojson").write_text("nonsense", encoding="utf-8")
    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        f'version: 1\nsources:\n  - path: "{(d / "broken.geojson").as_posix()}"\n',
        encoding="utf-8",
    )
    report = run_suite(load_suite(cfg))
    assert len(report.layers) == 1
    statuses = {r.status for r in report.layers[0].results}
    assert Status.ERROR in statuses


def test_geometry_fix_writes_output(tmp_path: Path):
    from conftest import make_parcels

    d = tmp_path / "data"
    d.mkdir()
    make_parcels().to_file(d / "parcels.gpkg", driver="GPKG")
    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        f'version: 1\nsources:\n  - path: "{d.as_posix()}"\n'
        "defaults:\n  geometry:\n    fix: true\n",
        encoding="utf-8",
    )
    out = tmp_path / "fixed"
    report = run_suite(load_suite(cfg), fix_output_dir=out)

    assert (out / "parcels.fixed.gpkg").exists()
    parcels = next(layer for layer in report.layers if layer.layer == "parcels")
    valid = next(r for r in parcels.results if r.check == "geometry.valid")
    assert valid.fixed >= 1
