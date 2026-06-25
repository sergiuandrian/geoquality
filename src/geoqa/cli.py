"""Command-line interface for geoqa."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from geoqa import __version__
from geoqa.config import load_suite
from geoqa.engine import run_suite
from geoqa.reporting import print_report, write_html, write_json

app = typer.Typer(
    add_completion=False,
    help="geoqa - data quality for geospatial data. Validate geometry, CRS, "
    "topology, duplicates and attributes from a single YAML config.",
)
console = Console()
err = Console(stderr=True)

_STARTER = """# geoqa configuration (https://github.com/your-org/geoqa)
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
    fix_output: Path | None = typer.Option(
        None, "--fix-output", help="Directory to write auto-repaired layers (requires geometry.fix)."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show skipped checks too."),
    no_fail: bool = typer.Option(
        False, "--no-fail", help="Always exit 0, even when checks fail (report only)."
    ),
) -> None:
    """Run all configured checks and report results."""
    try:
        suite = load_suite(config)
    except Exception as exc:  # noqa: BLE001
        err.print(f"[bold red]Config error:[/] {exc}")
        raise typer.Exit(code=2) from exc

    with console.status("[bold]Running checks..."):
        report = run_suite(
            suite,
            fix_output_dir=fix_output,
            progress=lambda name: console.log(f"checking {name}"),
        )

    print_report(report, console=console, verbose=verbose)

    if json_out:
        write_json(report, json_out)
        console.print(f"[dim]JSON report -> {json_out}[/]")
    if html:
        write_html(report, html, title=suite.report.title or suite.name)
        console.print(f"[dim]HTML report -> {html}[/]")

    if not report.passed and not no_fail:
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
    console.print(f"[green]Wrote starter config →[/] {path}")


@app.command("list-checks")
def list_checks() -> None:
    """List the available checks and their configuration keys."""
    rows = {
        "crs": "required, allowed_epsg, expected_epsg",
        "geometry": "valid, no_empty, no_missing, fix",
        "duplicates": "exact, fuzzy.{enabled,predicate,min_overlap,max_distance}",
        "attributes": "required, not_null, unique, max_null_fraction, domains.{allowed,min,max,regex}",
        "topology": "no_overlaps, no_gaps, no_dangles",
    }
    from rich.table import Table

    table = Table(title="geoqa checks", show_lines=True)
    table.add_column("Check", style="bold cyan")
    table.add_column("Config keys")
    for name, keys in rows.items():
        table.add_row(name, keys)
    console.print(table)
    console.print(
        "[dim]Every check also supports [bold]enabled[/] and [bold]severity[/] "
        "(error | warn | info).[/]"
    )


if __name__ == "__main__":
    app()
