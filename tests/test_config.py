from geoqa.config import Suite


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
