"""Command-line interface for geoqa."""

from __future__ import annotations

import enum
import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler

from geoqa import __version__
from geoqa.config import config_json_schema, load_suite
from geoqa.engine import run_suite
from geoqa.reporting import (
    print_report,
    write_geojson_failures,
    write_html,
    write_json,
    write_junit,
)

app = typer.Typer(
    add_completion=False,
    help="geoqa - data quality for geospatial data. Validate geometry, CRS, "
    "topology, duplicates and attributes from a single YAML config.",
)
console = Console()
err = Console(stderr=True)


class FailOn(str, enum.Enum):
    """Severity threshold at which a run exits non-zero."""

    error = "error"
    warn = "warn"
    never = "never"


class LogLevel(str, enum.Enum):
    debug = "debug"
    info = "info"
    warning = "warning"
    error = "error"


def _setup_logging(level: LogLevel, quiet: bool) -> None:
    effective = logging.ERROR if quiet else getattr(logging, level.value.upper())
    logging.basicConfig(
        level=effective,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=err, show_path=False, rich_tracebacks=True)],
    )


_STARTER = """# geoqa configuration (https://github.com/sergiuandrian/geoquality)
version: 1
name: "My GIS QA suite"

# Files / folders to validate. Folders expand to every matching file.
sources:
  - path: "data/"          # a folder...
    pattern: "*.gpkg"      # ...with an optional glob
  # - path: "data/roads.shp"  # ...or a single file

# Rules applied to *every* layer. Override per layer below.
defaults:
  crs:
    required: true
    allowed_epsg: [4326, 3857]
  geometry:
    valid: true
    fix: false             # set true to auto-repair with make_valid()
  duplicates:
    exact: true
    fuzzy:
      enabled: false
      predicate: intersects
      min_overlap: 0.9     # IoU threshold for near-duplicate polygons

# Per-layer rules. Key = layer name (file stem or GeoPackage layer name).
layers:
  roads:
    attributes:
      required: [name, surface]
      not_null: [name]
      domains:
        surface:
          allowed: [asphalt, gravel, dirt]
        lanes:
          min: 1
          max: 8
    topology:
      enabled: true
      no_dangles: true
  parcels:
    topology:
      enabled: true
      no_overlaps: true
      no_gaps: true
"""


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"geoqa {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    pass


@app.command()
def run(
    config: Path = typer.Option(
        Path("geoqa.yml"), "--config", "-c", help="Path to the geoqa YAML config."
    ),
    html: Path | None = typer.Option(None, "--html", help="Write an HTML report to this path."),
    json_out: Path | None = typer.Option(
        None, "--json", help="Write a JSON report to this path."
    ),
    junit_out: Path | None = typer.Option(
        None, "--junit", help="Write a JUnit XML report (CI-native test reporting)."
    ),
    geojson_out: Path | None = typer.Option(
        None, "--geojson-out", help="Directory to write GeoJSON of offending features."
    ),
    fix_output: Path | None = typer.Option(
        None, "--fix-output", help="Directory to write auto-repaired layers (requires geometry.fix)."
    ),
    fail_on: FailOn = typer.Option(
        FailOn.error, "--fail-on", case_sensitive=False,
        help="Severity that makes the run exit non-zero: error, warn, or never.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show skipped checks too."),
    no_fail: bool = typer.Option(
        False, "--no-fail", help="Alias for --fail-on never (report only, always exit 0)."
    ),
    log_level: LogLevel = typer.Option(
        LogLevel.warning, "--log-level", case_sensitive=False, help="Logging verbosity."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress all but error logs."),
    workers: int = typer.Option(
        1, "--workers", "-j", min=1, help="Validate this many layers in parallel."
    ),
) -> None:
    """Run all configured checks and report results."""
    _setup_logging(log_level, quiet)

    try:
        suite = load_suite(config)
    except Exception as exc:  # noqa: BLE001
        err.print(f"[bold red]Config error:[/] {exc}")
        raise typer.Exit(code=2) from exc

    with console.status("[bold]Running checks..."):
        report = run_suite(
            suite,
            fix_output_dir=fix_output,
            progress=lambda name: logging.getLogger("geoqa").info("checking %s", name),
            workers=workers,
            collect_failures=geojson_out is not None or html is not None,
        )

    print_report(report, console=console, verbose=verbose)

    max_issues = suite.report.max_issues_per_check
    if json_out:
        write_json(report, json_out, max_issues=max_issues)
        console.print(f"[dim]JSON report -> {json_out}[/]")
    if html:
        write_html(report, html, title=suite.report.title or suite.name, max_issues=max_issues)
        console.print(f"[dim]HTML report -> {html}[/]")
    if junit_out:
        write_junit(report, junit_out)
        console.print(f"[dim]JUnit report -> {junit_out}[/]")
    if geojson_out:
        written = write_geojson_failures(report, geojson_out)
        console.print(f"[dim]GeoJSON failures -> {len(written)} file(s) in {geojson_out}[/]")

    threshold = "never" if no_fail else fail_on.value
    if report.has_failures(threshold):
        raise typer.Exit(code=1)


@app.command()
def init(
    path: Path = typer.Option(Path("geoqa.yml"), "--path", "-p", help="Where to write the config."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing file."),
) -> None:
    """Write a starter geoqa.yml you can edit."""
    if path.exists() and not force:
        err.print(f"[yellow]{path} already exists. Use --force to overwrite.[/]")
        raise typer.Exit(code=1)
    path.write_text(_STARTER, encoding="utf-8")
    console.print(f"[green]Wrote starter config ->[/] {path}")


@app.command()
def validate(
    config: Path = typer.Option(
        Path("geoqa.yml"), "--config", "-c", help="Path to the geoqa YAML config."
    ),
) -> None:
    """Validate a geoqa config (schema + every per-layer rule) without running checks."""
    try:
        suite = load_suite(config)
    except Exception as exc:  # noqa: BLE001
        err.print(f"[bold red]Invalid config:[/] {exc}")
        raise typer.Exit(code=2) from exc

    # Re-validate the merged config for each explicitly configured layer so typos
    # in per-layer overrides surface here rather than mid-run.
    problems: list[str] = []
    for layer_name in suite.layers:
        try:
            suite.config_for_layer(layer_name)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"layer '{layer_name}': {exc}")

    if problems:
        for p in problems:
            err.print(f"[bold red]x[/] {p}")
        raise typer.Exit(code=2)

    console.print(
        f"[green]Config OK[/] - {len(suite.sources)} source(s), "
        f"{len(suite.layers)} layer override(s)."
    )


@app.command()
def schema(
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write the JSON Schema here (default: stdout)."
    ),
) -> None:
    """Emit a JSON Schema for geoqa.yml (editor autocomplete / validation)."""
    import json

    doc = json.dumps(config_json_schema(), indent=2, ensure_ascii=False)
    if output:
        output.write_text(doc, encoding="utf-8")
        console.print(f"[green]Wrote JSON Schema ->[/] {output}")
    else:
        print(doc)


@app.command("list-checks")
def list_checks() -> None:
    """List the available checks (built-in and plugins) and their config keys."""
    from rich.table import Table

    from geoqa.registry import ENTRY_POINT_GROUP, get_registry

    builtin = {"crs", "geometry", "duplicates", "attributes", "topology"}
    table = Table(title="geoqa checks", show_lines=True)
    table.add_column("Check", style="bold cyan")
    table.add_column("Source", style="dim")
    table.add_column("Config keys")
    for spec in get_registry().specs():
        origin = "built-in" if spec.name in builtin else "plugin"
        table.add_row(spec.name, origin, ", ".join(spec.keys()))
    console.print(table)
    console.print(
        "[dim]Every check also supports [bold]enabled[/] and [bold]severity[/] "
        "(error | warn | info).[/]"
    )
    console.print(
        f"[dim]Add your own checks via the [bold]{ENTRY_POINT_GROUP}[/] entry point group.[/]"
    )


if __name__ == "__main__":
    app()
