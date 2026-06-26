"""Check plugin registry.

geoqa ships a handful of built-in checks (crs, geometry, duplicates, attributes,
topology), but the product promise is *configure custom rules without forking*.
This module turns the check set into a registry that:

* registers the built-ins in a fixed execution order, and
* discovers third-party checks advertised through the ``geoqa.checks`` entry
  point group, so a plugin package can add a check just by depending on geoqa.

A plugin exposes a :class:`CheckSpec` (or a zero-arg callable returning one) via
``pyproject.toml``::

    [project.entry-points."geoqa.checks"]
    my_check = "my_pkg.checks:SPEC"

Each check is a callable ``run(gdf, layer, source, cfg) -> list[CheckResult]``
paired with a pydantic config model whose field name (``spec.name``) becomes the
YAML key under ``defaults``/``layers``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

    from geoqa.result import CheckResult

logger = logging.getLogger("geoqa")

ENTRY_POINT_GROUP = "geoqa.checks"

CheckRunner = Callable[..., "list[CheckResult]"]


@dataclass(frozen=True)
class CheckSpec:
    """Metadata binding a check's config key, runner and config model."""

    name: str
    runner: CheckRunner
    config_model: type[BaseModel]
    order: int = 100  # lower runs first
    description: str = ""
    config_keys: tuple[str, ...] = field(default=())

    def keys(self) -> tuple[str, ...]:
        """Config keys to advertise (falls back to the model's fields)."""
        if self.config_keys:
            return self.config_keys
        fields = tuple(self.config_model.model_fields)
        return tuple(k for k in fields if k not in ("enabled", "severity"))


class CheckRegistry:
    """An ordered collection of :class:`CheckSpec`, keyed by name."""

    def __init__(self) -> None:
        self._checks: dict[str, CheckSpec] = {}

    def register(self, spec: CheckSpec) -> None:
        if spec.name in self._checks:
            logger.debug("overriding already-registered check %r", spec.name)
        self._checks[spec.name] = spec

    def get(self, name: str) -> CheckSpec | None:
        return self._checks.get(name)

    def names(self) -> list[str]:
        return [s.name for s in self.specs()]

    def specs(self) -> list[CheckSpec]:
        """All registered checks, ordered by (order, name)."""
        return sorted(self._checks.values(), key=lambda s: (s.order, s.name))


def _builtin_specs() -> list[CheckSpec]:
    """The checks that ship with geoqa, registered in code (not via entry points)."""
    from geoqa.checks import attributes, crs, duplicates, geometry, topology
    from geoqa.config import (
        AttributesCheck,
        CrsCheck,
        DuplicatesCheck,
        GeometryCheck,
        TopologyCheck,
    )

    return [
        CheckSpec("crs", crs.run, CrsCheck, order=10,
                  description="Coordinate reference system is defined and allowed"),
        CheckSpec("geometry", geometry.run, GeometryCheck, order=20,
                  description="Geometries are valid, non-empty and present"),
        CheckSpec("duplicates", duplicates.run, DuplicatesCheck, order=30,
                  description="No exact (or fuzzy) duplicate geometries"),
        CheckSpec("attributes", attributes.run, AttributesCheck, order=40,
                  description="Attribute completeness, uniqueness and domains"),
        CheckSpec("topology", topology.run, TopologyCheck, order=50,
                  description="No overlaps, gaps or dangling line endpoints"),
    ]


def _entry_point_specs() -> list[CheckSpec]:
    """Discover third-party checks from the ``geoqa.checks`` entry point group."""
    specs: list[CheckSpec] = []
    try:
        entries = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001 - never let discovery break a run
        logger.exception("failed to enumerate %r entry points", ENTRY_POINT_GROUP)
        return specs

    for ep in entries:
        try:
            obj = ep.load()
            spec = obj() if callable(obj) and not isinstance(obj, CheckSpec) else obj
            if not isinstance(spec, CheckSpec):
                logger.warning(
                    "entry point %r did not provide a CheckSpec (got %r); skipping",
                    ep.name, type(spec).__name__,
                )
                continue
            specs.append(spec)
            logger.debug("loaded plugin check %r from %s", spec.name, ep.value)
        except Exception:  # noqa: BLE001 - a broken plugin must not crash geoqa
            logger.exception("failed to load check plugin %r", ep.name)
    return specs


_default_registry: CheckRegistry | None = None


def get_registry() -> CheckRegistry:
    """Return the process-wide registry (built-ins + discovered plugins)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = build_registry()
    return _default_registry


def build_registry(*, include_plugins: bool = True) -> CheckRegistry:
    """Build a fresh registry. Plugins override built-ins of the same name."""
    registry = CheckRegistry()
    for spec in _builtin_specs():
        registry.register(spec)
    if include_plugins:
        for spec in _entry_point_specs():
            registry.register(spec)
    return registry


def reset_registry() -> None:
    """Clear the cached registry (used by tests after registering fakes)."""
    global _default_registry
    _default_registry = None
