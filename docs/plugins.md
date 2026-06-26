# Custom checks (plugins)

The built-in checks run through a registry, and third parties can add checks
**without forking** via the `geoqa.checks` entry point group.

## 1. Write the check

```python
# my_pkg/checks.py
from pydantic import BaseModel

from geoqa.registry import CheckSpec
from geoqa.result import CheckResult, Status


class MyConfig(BaseModel):
    enabled: bool = True
    threshold: float = 1.0


def run(gdf, layer, source, cfg) -> list[CheckResult]:
    # ... inspect gdf, build results ...
    return [
        CheckResult(
            check="my_check", layer=layer, source=source,
            status=Status.PASS, message="ok",
        )
    ]


SPEC = CheckSpec("my_check", run, MyConfig, order=60, description="My rule")
```

A check is a callable `run(gdf, layer, source, cfg) -> list[CheckResult]` paired
with a pydantic config model. The model's name (`spec.name`) becomes the YAML key.

## 2. Advertise it

```toml
# your plugin's pyproject.toml
[project.entry-points."geoqa.checks"]
my_check = "my_pkg.checks:SPEC"
```

## 3. Use it

Once installed, the check appears in `geoqa list-checks` and its config key is
accepted under `defaults`/`layers`:

```yaml
defaults:
  my_check:
    threshold: 2.0
```

!!! note
    A broken plugin is logged and skipped — it never crashes a run. Plugins may
    also override a built-in by registering the same name.
