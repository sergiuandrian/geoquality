"""Rich console reporter."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from geoqa.result import Report, Status

_STATUS_STYLE = {
    Status.PASS: ("PASS", "bold green"),
    Status.FAIL: ("FAIL", "bold red"),
    Status.WARN: ("WARN", "bold yellow"),
    Status.ERROR: ("ERROR", "bold red"),
    Status.SKIP: ("skip", "dim"),
}


def print_report(report: Report, console: Console | None = None, verbose: bool = False) -> None:
    console = console or Console()

    for layer in report.layers:
        label, style = _STATUS_STYLE[layer.status]
        header = Text.assemble(
            (f"{layer.layer}  ", "bold cyan"),
            (f"[{label}]", style),
            (f"\n{layer.source}", "dim"),
            (
                f"\n{layer.n_features} features"
                + (f" · {layer.geometry_type}" if layer.geometry_type else "")
                + (f" · {layer.crs}" if layer.crs else ""),
                "dim",
            ),
        )
        table = Table(show_header=True, header_style="bold", expand=True)
        table.add_column("Check", no_wrap=True)
        table.add_column("Status", no_wrap=True)
        table.add_column("Result")

        for r in layer.results:
            if not verbose and r.status == Status.SKIP:
                continue
            slabel, sstyle = _STATUS_STYLE[r.status]
            table.add_row(r.check, Text(slabel, style=sstyle), r.message)

        console.print(Panel(table, title=header, border_style=style))

    _print_summary(report, console)


def _print_summary(report: Report, console: Console) -> None:
    counts = report.counts
    summary = Text.assemble(
        ("Suite: ", "bold"), (f"{report.suite_name}\n", ""),
        (f"{counts['pass']} passed  ", "green"),
        (f"{counts['fail']} failed  ", "red"),
        (f"{counts['warn']} warnings  ", "yellow"),
        (f"{counts['error']} errors  ", "red"),
        (f"{counts['skip']} skipped", "dim"),
    )
    overall = "PASSED" if report.passed else "FAILED"
    style = "bold green" if report.passed else "bold red"
    console.print(Panel(summary, title=Text(overall, style=style), border_style=style))
