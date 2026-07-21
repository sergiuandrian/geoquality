"""Progress events for long-running suite / check execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ProgressEvent:
    """One progress update during ``run_suite``.

    ``phase`` is typically ``layer``, ``check``, ``chunk``, or ``cache``.
    ``fraction`` is in ``[0, 1]`` when known.
    """

    layer: str
    phase: str = "layer"
    check: str | None = None
    fraction: float | None = None
    message: str = ""

    def as_legacy(self) -> str:
        """Layer name only — matches the historical progress callback contract."""
        return self.layer


ProgressCb = Callable[[Any], None] | None


def emit(progress: ProgressCb, event: ProgressEvent) -> None:
    """Invoke ``progress`` with a ``ProgressEvent``, falling back to layer name."""
    if progress is None:
        return
    try:
        progress(event)
    except TypeError:
        progress(event.as_legacy())
