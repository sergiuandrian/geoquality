"""Tests for Phase G: schema, metadata, ISO 19157 tags, topology rulesets, packs."""

from __future__ import annotations

from pathlib import Path

import pytest

gpd = pytest.importorskip("geopandas")
from shapely.geometry import Point, box  # noqa: E402

from geoqa.checks import metadata, schema, topology  # noqa: E402
from geoqa.config import (  # noqa: E402
    MetadataCheck,
    SchemaCheck,
    TopologyCheck,
    load_suite,
)
from geoqa.engine import run_suite  # noqa: E402
from geoqa.iso19157 import dq_element_for  # noqa: E402
from geoqa.result import Status  # noqa: E402
from geoqa.topology_rulesets import RULESETS, expand_ruleset  # noqa: E402


def _by_check(results):
    return {r.check: r for r in results}


def test_dq_element_mapping():
    assert dq_element_for("topology.no_overlaps") == "DQ_TopologicalConsistency"
    assert dq_element_for("attributes.domains[zone]") == "DQ_DomainConsistency"
    assert dq_element_for("schema.columns") == "DQ_FormatConsistency"
    assert dq_element_for("crs.required") == "DQ_AbsoluteExternalPositionalAccuracy"


def test_check_result_includes_dq_element():
    gdf = gpd.GeoDataFrame({"geometry": [box(0, 0, 1, 1)]}, crs="EPSG:3857")
    res = topology.run(
        gdf, "l", "s", TopologyCheck(enabled=True, no_overlaps=True)
    )[0]
    assert res.dq_element == "DQ_TopologicalConsistency"
    assert res.to_dict()["dq_element"] == "DQ_TopologicalConsistency"


def test_schema_missing_column():
    gdf = gpd.GeoDataFrame({"geometry": [box(0, 0, 1, 1)]}, crs="EPSG:4326")
    cfg = SchemaCheck(
        enabled=True,
        columns={"parcel_id": {"type": "string", "required": True}},
    )
    res = _by_check(schema.run(gdf, "l", "s", cfg))
    assert res["schema.columns"].status == Status.FAIL
    assert "parcel_id" in res["schema.columns"].issues[0].message


def test_schema_wrong_geom_type():
    gdf = gpd.GeoDataFrame({"geometry": [Point(0, 0)]}, crs="EPSG:4326")
    cfg = SchemaCheck(
        enabled=True,
        geometry={"types": ["Polygon", "MultiPolygon"], "srid": 4326},
    )
    res = _by_check(schema.run(gdf, "l", "s", cfg))
    assert res["schema.geometry"].status == Status.FAIL


def test_schema_excess_precision():
    # Point with many decimal places in geographic CRS.
    gdf = gpd.GeoDataFrame(
        {"geometry": [Point(1.123456789, 2.123456789)]}, crs="EPSG:4326"
    )
    cfg = SchemaCheck(enabled=True, precision={"max_decimal_places": 3})
    res = _by_check(schema.run(gdf, "l", "s", cfg))
    assert res["schema.precision"].status == Status.FAIL


def test_schema_ok():
    gdf = gpd.GeoDataFrame(
        {"parcel_id": ["A"], "geometry": [box(0, 0, 1, 1)]}, crs="EPSG:4326"
    )
    cfg = SchemaCheck(
        enabled=True,
        columns={"parcel_id": {"type": "string", "required": True}},
        geometry={"types": ["Polygon"], "srid": 4326},
        precision={"max_decimal_places": 7},
    )
    res = _by_check(schema.run(gdf, "l", "s", cfg))
    assert res["schema.columns"].status == Status.PASS
    assert res["schema.geometry"].status == Status.PASS
    assert res["schema.precision"].status == Status.PASS


def test_schema_missing_external_path_is_error(tmp_path: Path):
    gdf = gpd.GeoDataFrame({"geometry": [box(0, 0, 1, 1)]}, crs="EPSG:4326")
    cfg = SchemaCheck(enabled=True, path=str(tmp_path / "does-not-exist.yml"))
    res = schema.run(gdf, "l", "s", cfg)
    assert len(res) == 1
    assert res[0].check == "schema"
    assert res[0].status == Status.ERROR
    assert "not found" in res[0].message


def test_metadata_missing_sidecar(tmp_path: Path):
    gpkg = tmp_path / "x.gpkg"
    gpd.GeoDataFrame({"geometry": [box(0, 0, 1, 1)]}, crs="EPSG:3857").to_file(
        gpkg, driver="GPKG"
    )
    gdf = gpd.read_file(gpkg)
    res = _by_check(metadata.run(
        gdf, "l", str(gpkg), MetadataCheck(enabled=True, sidecar="metadata.xml")
    ))
    assert res["metadata.presence"].status == Status.WARN


def test_metadata_required_keys(tmp_path: Path):
    gpkg = tmp_path / "x.gpkg"
    gpd.GeoDataFrame({"geometry": [box(0, 0, 1, 1)]}, crs="EPSG:3857").to_file(
        gpkg, driver="GPKG"
    )
    (tmp_path / "metadata.xml").write_text(
        "<metadata><title>T</title><crs>EPSG:1</crs></metadata>", encoding="utf-8"
    )
    gdf = gpd.read_file(gpkg)
    res = _by_check(metadata.run(
        gdf, "l", str(gpkg),
        MetadataCheck(enabled=True, required_keys=["title", "crs", "date", "lineage"]),
    ))
    assert res["metadata.keys"].status == Status.WARN
    assert res["metadata.keys"].n_failed == 2


def test_topology_ruleset_expands():
    cfg = TopologyCheck.model_validate({"ruleset": "cadastre_coverage", "min_area": 0.5})
    assert cfg.enabled is True
    assert cfg.no_overlaps is True
    assert cfg.no_coverage_gaps is True
    assert cfg.coincident_edges is True
    assert cfg.min_area == 0.5
    assert cfg.ruleset == "cadastre_coverage"


def test_topology_ruleset_unknown():
    with pytest.raises(Exception):
        TopologyCheck.model_validate({"ruleset": "not_a_real_ruleset"})


def test_expand_ruleset_explicit_wins():
    out = expand_ruleset("network", {"ruleset": "network", "ignore_boundary": False})
    assert out["no_dangles"] is True
    assert out["ignore_boundary"] is False
    assert "cadastre_coverage" in RULESETS


def test_inspire_au_pack_runs():
    root = Path(__file__).resolve().parents[1]
    cfg = root / "examples" / "packs" / "inspire_au" / "geoqa.yml"
    assert cfg.is_file()
    report = run_suite(load_suite(cfg))
    assert report.layers
    by = {r.check: r for r in report.layers[0].results}
    assert "schema.columns" in by
    assert by["schema.columns"].status == Status.PASS
    assert "metadata.keys" in by
    assert by["metadata.keys"].status == Status.PASS
    # Topology ruleset expands; two touching boxes should not overlap.
    assert "topology.no_overlaps" in by
