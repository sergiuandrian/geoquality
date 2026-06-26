"""Result and report data model.

These are plain dataclasses (not pydantic) so checks can build them cheaply and
reporters can serialize them without coupling to the config layer.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class Status(str, enum.Enum):
    """Outcome of a single check."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    ERROR = "error"  # the check itself blew up
    SKIP = "skip"  # not applicable / not configured

    @property
    def is_problem(self) -> bool:
        return self in (Status.FAIL, Status.WARN, Status.ERROR)


class Severity(str, enum.Enum):
    """How a failing check should be treated by the runner / exit code."""

    ERROR = "error"
    WARN = "warn"
    INFO = "info"


@dataclass
class Issue:
    """A single offending feature / problem found by a check."""

    message: str
    feature_id: Any = None  # index value or id column
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "feature_id": _jsonify(self.feature_id),
            "detail": {k: _jsonify(v) for k, v in self.detail.items()},
        }


@dataclass
class CheckResult:
    """Outcome of running one check against one layer."""

    check: str
    layer: str
    source: str
    status: Status
    message: str
    severity: Severity = Severity.ERROR
    n_total: int = 0
    n_failed: int = 0
    issues: list[Issue] = field(default_factory=list)
    duration_s: float = 0.0
    fixed: int = 0  # how many features were auto-fixed

    @property
    def ok(self) -> bool:
        return self.status in (Status.PASS, Status.SKIP)

    def to_dict(self, max_issues: int = 50) -> dict[str, Any]:
        return {
            "check": self.check,
            "layer": self.layer,
            "source": self.source,
            "status": self.status.value,
            "severity": self.severity.value,
            "message": self.message,
            "n_total": self.n_total,
            "n_failed": self.n_failed,
            "fixed": self.fixed,
            "duration_s": round(self.duration_s, 4),
            "issues": [i.to_dict() for i in self.issues[:max_issues]],
            "issues_truncated": max(0, len(self.issues) - max_issues),
        }


@dataclass
class LayerReport:
    """All check results for a single layer."""

    layer: str
    source: str
    n_features: int = 0
    geometry_type: str | None = None
    crs: str | None = None
    results: list[CheckResult] = field(default_factory=list)

    @property
    def status(self) -> Status:
        statuses = {r.status for r in self.results}
        if Status.ERROR in statuses:
            return Status.ERROR
        if Status.FAIL in statuses:
            return Status.FAIL
        if Status.WARN in statuses:
            return Status.WARN
        return Status.PASS

    def to_dict(self, max_issues: int = 50) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "source": self.source,
            "n_features": self.n_features,
            "geometry_type": self.geometry_type,
            "crs": self.crs,
            "status": self.status.value,
            "results": [r.to_dict(max_issues=max_issues) for r in self.results],
        }


@dataclass
class Report:
    """Top-level report for an entire run."""

    suite_name: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    layers: list[LayerReport] = field(default_factory=list)

    @property
    def all_results(self) -> list[CheckResult]:
        return [r for layer in self.layers for r in layer.results]

    @property
    def counts(self) -> dict[str, int]:
        c = {s.value: 0 for s in Status}
        for r in self.all_results:
            c[r.status.value] += 1
        return c

    def has_failures(self, threshold: str = "error") -> bool:
        """Whether the run should be considered failing at the given threshold.

        - ``"error"`` (default): any FAIL (error-severity check) or ERROR (crash).
        - ``"warn"``: additionally treat WARN (warn-severity check) as failing.
        - ``"never"``: never fail (report-only).
        """
        if threshold == "never":
            return False
        bad = {Status.FAIL, Status.ERROR}
        if threshold == "warn":
            bad = {Status.WARN, Status.FAIL, Status.ERROR}
        return any(r.status in bad for r in self.all_results)

    @property
    def passed(self) -> bool:
        """True when there are no error-severity failures or crashes."""
        return not self.has_failures("error")

    def to_dict(self, max_issues: int = 50) -> dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "passed": self.passed,
            "counts": self.counts,
            "layers": [layer.to_dict(max_issues=max_issues) for layer in self.layers],
        }


def _jsonify(value: Any) -> Any:
    """Best-effort conversion of numpy / pandas scalars to JSON-native types."""
    if value is None:
        return None
    if isinstance(value, (str, bool, int, float)):
        return value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:  # noqa: BLE001
            pass
    return str(value)
