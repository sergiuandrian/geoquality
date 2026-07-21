"""Bundled domain profiles for ``geoqa init --profile``.

Profiles are static YAML templates under ``geoqa/profiles/``. They are starting
points — adjust CRS allow-lists, layer names, and attribute columns to match
your data.
"""

from __future__ import annotations

from importlib import resources

PROFILES: dict[str, str] = {
    "parcels": "Cadastre / parcel polygons (overlaps, gaps, parcel_id)",
    "roads": "Road / network lines (dangles, name, lanes)",
    "admin_boundaries": "Administrative boundary polygons (overlaps, codes)",
    "addresses": "Address / POI points (unique id, near-duplicates)",
}


def list_profiles() -> dict[str, str]:
    """Return ``{name: short description}`` for every bundled profile."""
    return dict(PROFILES)


def load_profile(name: str) -> str:
    """Return the YAML text for a bundled profile.

    Raises:
        KeyError: if ``name`` is not a known profile.
    """
    if name not in PROFILES:
        known = ", ".join(sorted(PROFILES))
        raise KeyError(f"unknown profile {name!r}; choose one of: {known}")
    root = resources.files("geoqa.profiles")
    return root.joinpath(f"{name}.yml").read_text(encoding="utf-8")
