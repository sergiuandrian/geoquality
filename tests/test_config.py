from pathlib import Path

from geoqa.config import Suite, load_suite


def test_defaults_apply_and_layer_overrides():
    suite = Suite.model_validate(
        {
            "version": 1,
            "defaults": {"crs": {"required": True, "allowed_epsg": [4326]}},
            "layers": {"roads": {"crs": {"allowed_epsg": [3857]}}},
        }
    )

    roads = suite.config_for_layer("roads")
    assert roads.crs.required is True  # inherited from defaults
    assert roads.crs.allowed_epsg == [3857]  # overridden

    other = suite.config_for_layer("not_configured")
    assert other.crs.allowed_epsg == [4326]  # falls back to defaults


def test_unknown_key_is_rejected():
    import pytest

    suite = Suite.model_validate({"version": 1, "defaults": {"geometry": {"nope": True}}})
    with pytest.raises(Exception):
        suite.config_for_layer("x")


def test_bad_version_rejected():
    import pytest

    with pytest.raises(Exception):
        Suite.model_validate({"version": 2})


def test_topology_aoi_resolves_against_suite_dir(tmp_path: Path):
    aoi = tmp_path / "aoi.geojson"
    aoi.write_text(
        '{"type":"FeatureCollection","features":[]}',
        encoding="utf-8",
    )
    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        """
version: 1
sources:
  - path: data.gpkg
defaults:
  topology:
    enabled: true
    no_coverage_gaps: true
    aoi: aoi.geojson
""",
        encoding="utf-8",
    )
    suite = load_suite(cfg)
    layer_cfg = suite.config_for_layer("any")
    assert Path(layer_cfg.topology.aoi) == (tmp_path / "aoi.geojson").resolve()
