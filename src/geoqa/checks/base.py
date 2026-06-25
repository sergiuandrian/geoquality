"""Helpers shared across checks."""

from __future__ import annotations

from typing import Any

from geoqa.result import CheckResult, Issue, Severity, Status


def result(
    check: str,
    layer: str,
    source: str,
    status: Status,
    message: str,
    severity: Severity = Severity.ERROR,
    n_total: int = 0,
    n_failed: int = 0,
    issues: list[Issue] | None = None,
    fixed: int = 0,
) -> CheckResult:
    return CheckResult(
        check=check,
        layer=layer,
        source=source,
        status=status,
        message=message,
        severity=severity,
        n_total=n_total,
        n_failed=n_failed,
        issues=issues or [],
        fixed=fixed,
    )


def status_for(n_failed: int, severity: Severity) -> Status:
    """Map a failure count + configured severity onto a result status."""
    if n_failed == 0:
        return Status.PASS
    return Status.WARN if severity == Severity.WARN else Status.FAIL


def build_issues(
    failed_index: Any,
    gdf: Any,
    message: str,
    id_col: str | None = None,
    limit: int = 200,
) -> list[Issue]:
    """Create ``Issue`` objects for a set of failing row indices."""
    issues: list[Issue] = []
    for idx in list(failed_index)[:limit]:
        feature_id = idx
        if id_col and id_col in gdf.columns:
            try:
                feature_id = gdf.at[idx, id_col]
            except Exception:  # noqa: BLE001
                feature_id = idx
        issues.append(Issue(message=message, feature_id=feature_id))
    return issues
