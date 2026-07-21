"""Tests for bundled domain profiles."""

from __future__ import annotations

import pytest

from geoqa.config import load_suite
from geoqa.profiles import PROFILES, list_profiles, load_profile


def test_list_profiles_matches_catalog():
    assert set(list_profiles()) == set(PROFILES)
    assert {"parcels", "roads", "admin_boundaries", "addresses"} <= set(PROFILES)


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_each_profile_loads_and_validates(name: str, tmp_path):
    text = load_profile(name)
    assert "version: 1" in text
    path = tmp_path / f"{name}.yml"
    path.write_text(text, encoding="utf-8")
    suite = load_suite(path)
    assert suite.sources
    # Merged defaults + each layer override must validate.
    for layer_name in suite.layers:
        suite.config_for_layer(layer_name)


def test_load_profile_unknown_raises():
    with pytest.raises(KeyError, match="unknown profile"):
        load_profile("nope")
