"""Tests for the check plugin registry and dynamic layer config."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from geoqa import config, registry
from geoqa.registry import CheckSpec
from geoqa.result import CheckResult, Severity, Status


class GreetCheck(BaseModel):
    enabled: bool = True
    severity: Severity = Severity.WARN
    word: str = "hi"


def greet_run(gdf, layer, source, cfg) -> list[CheckResult]:
    return [
        CheckResult(check="greet", layer=layer, source=source,
                    status=Status.PASS, message=cfg.word, severity=cfg.severity)
    ]


GREET_SPEC = CheckSpec("greet", greet_run, GreetCheck, order=99, description="Greeter")


@pytest.fixture
def with_plugin():
    """Install a fake plugin check into the process registry for one test."""
    reg = registry.build_registry(include_plugins=False)
    reg.register(GREET_SPEC)
    registry._default_registry = reg
    config._layer_model_cache = None
    yield
    registry.reset_registry()
    config._layer_model_cache = None


def test_builtin_order_and_names():
    reg = registry.build_registry(include_plugins=False)
    assert reg.names() == ["crs", "geometry", "duplicates", "attributes", "topology"]
    assert reg.get("crs") is not None
    assert reg.get("nope") is None


def test_spec_keys_excludes_enabled_severity():
    assert "enabled" not in GREET_SPEC.keys()
    assert "severity" not in GREET_SPEC.keys()
    assert "word" in GREET_SPEC.keys()


def test_register_overrides_same_name():
    reg = registry.build_registry(include_plugins=False)
    replacement = CheckSpec("crs", greet_run, GreetCheck, order=1)
    reg.register(replacement)
    assert reg.get("crs").runner is greet_run


def test_entry_point_discovery(monkeypatch):
    class _EP:
        def __init__(self, name, value, loader):
            self.name, self.value, self._loader = name, value, loader

        def load(self):
            return self._loader()

    def _raise():
        raise RuntimeError("broken plugin")

    eps = [
        _EP("greet", "pkg:SPEC", lambda: GREET_SPEC),
        _EP("notspec", "pkg:thing", lambda: 123),  # not a CheckSpec -> skipped
        _EP("boom", "pkg:boom", _raise),  # raises -> logged, skipped
    ]
    monkeypatch.setattr(registry.metadata, "entry_points", lambda group=None: eps)
    specs = registry._entry_point_specs()
    assert [s.name for s in specs] == ["greet"]


def test_plugin_runs_end_to_end(with_plugin, data_dir, tmp_path):
    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        f'version: 1\nsources:\n  - path: "{data_dir.as_posix()}"\n'
        'defaults:\n  greet:\n    word: "yo"\n',
        encoding="utf-8",
    )
    suite = config.load_suite(cfg)
    from geoqa.engine import run_suite

    report = run_suite(suite)
    greets = [r for layer in report.layers for r in layer.results if r.check == "greet"]
    assert greets and all(r.message == "yo" for r in greets)


def test_dynamic_config_rejects_unknown_key(tmp_path):
    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        'version: 1\nsources: []\ndefaults:\n  bogus:\n    x: 1\n', encoding="utf-8"
    )
    with pytest.raises(Exception):
        config.load_suite(cfg)


def test_plugin_key_accepted_only_when_registered(with_plugin, tmp_path):
    # With the plugin registered, the previously-unknown 'greet' key validates.
    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        'version: 1\nsources: []\ndefaults:\n  greet:\n    word: "ok"\n',
        encoding="utf-8",
    )
    suite = config.load_suite(cfg)
    layer_cfg = suite.config_for_layer("anything")
    assert layer_cfg.greet.word == "ok"
