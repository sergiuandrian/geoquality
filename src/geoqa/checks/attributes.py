"""Attribute completeness and domain (value) checks."""

from __future__ import annotations

import re

import geopandas as gpd
import pandas as pd

from geoqa.checks.base import build_issues, result, status_for
from geoqa.config import AttributesCheck
from geoqa.result import CheckResult, Issue, Status

CHECK = "attributes"


def run(gdf: gpd.GeoDataFrame, layer: str, source: str, cfg: AttributesCheck) -> list[CheckResult]:
    if not cfg.enabled:
        return []

    results: list[CheckResult] = []
    n_total = len(gdf)
    cols = set(gdf.columns)

    if cfg.required:
        missing = [c for c in cfg.required if c not in cols]
        results.append(
            result(
                CHECK + ".required", layer, source,
                status_for(len(missing), cfg.severity),
                "All required columns present."
                if not missing
                else f"Missing required column(s): {', '.join(missing)}.",
                severity=cfg.severity, n_total=n_total, n_failed=len(missing),
                issues=[Issue(message=f"missing column '{c}'") for c in missing],
            )
        )

    for col in cfg.not_null:
        if col not in cols:
            results.append(_missing_col(col, layer, source, cfg))
            continue
        null_mask = gdf[col].isna()
        n = int(null_mask.sum())
        results.append(
            result(
                f"{CHECK}.not_null[{col}]", layer, source,
                status_for(n, cfg.severity),
                f"Column '{col}' has no nulls." if n == 0 else f"Column '{col}' has {n} null value(s).",
                severity=cfg.severity, n_total=n_total, n_failed=n,
                issues=build_issues(gdf.index[null_mask], gdf, f"null in '{col}'"),
            )
        )

    for col, max_frac in cfg.max_null_fraction.items():
        if col not in cols:
            results.append(_missing_col(col, layer, source, cfg))
            continue
        frac = float(gdf[col].isna().mean()) if n_total else 0.0
        over = frac > max_frac
        results.append(
            result(
                f"{CHECK}.completeness[{col}]", layer, source,
                Status.PASS if not over else (Status.WARN if cfg.severity.value == "warn" else Status.FAIL),
                f"Column '{col}' null fraction {frac:.1%} (limit {max_frac:.0%}).",
                severity=cfg.severity, n_total=n_total,
                n_failed=int(gdf[col].isna().sum()) if over else 0,
            )
        )

    for col in cfg.unique:
        if col not in cols:
            results.append(_missing_col(col, layer, source, cfg))
            continue
        dup_mask = gdf[col].duplicated(keep=False) & gdf[col].notna()
        n_extra = int((gdf[col].duplicated(keep="first") & gdf[col].notna()).sum())
        results.append(
            result(
                f"{CHECK}.unique[{col}]", layer, source,
                status_for(n_extra, cfg.severity),
                f"Column '{col}' values are unique."
                if n_extra == 0
                else f"Column '{col}' has {n_extra} duplicate value(s).",
                severity=cfg.severity, n_total=n_total, n_failed=n_extra,
                issues=build_issues(gdf.index[dup_mask], gdf, f"duplicate value in '{col}'", id_col=col),
            )
        )

    for col, rule in cfg.domains.items():
        if col not in cols:
            results.append(_missing_col(col, layer, source, cfg))
            continue
        results.append(_domain(gdf, col, rule, layer, source, cfg, n_total))

    return results


def _missing_col(col: str, layer: str, source: str, cfg: AttributesCheck) -> CheckResult:
    return result(
        f"{CHECK}.missing[{col}]", layer, source,
        Status.WARN if cfg.severity.value == "warn" else Status.FAIL,
        f"Configured column '{col}' does not exist in this layer.",
        severity=cfg.severity, n_failed=1,
        issues=[Issue(message=f"column '{col}' not found")],
    )


def _domain(gdf, col, rule, layer, source, cfg, n_total) -> CheckResult:
    series = gdf[col]
    present = series.notna()
    bad = pd.Series(False, index=series.index)
    parts: list[str] = []

    if rule.allowed is not None:
        allowed = set(rule.allowed)
        bad |= present & ~series.isin(allowed)
        parts.append(f"allowed={rule.allowed}")
    if rule.min is not None or rule.max is not None:
        numeric = pd.to_numeric(series, errors="coerce")
        # A present value that cannot be parsed as a number violates a numeric domain.
        bad |= present & numeric.isna()
        if rule.min is not None:
            bad |= present & (numeric < rule.min)
            parts.append(f"min={rule.min}")
        if rule.max is not None:
            bad |= present & (numeric > rule.max)
            parts.append(f"max={rule.max}")
    if rule.regex is not None:
        pattern = re.compile(rule.regex)
        # ``na_action="ignore"`` keeps NA out of the callable (so it never sees
        # pd.NA); nulls are not the regex's concern and are masked out by ``present``.
        matched = series.astype("string").map(
            lambda v: bool(pattern.fullmatch(v)), na_action="ignore"
        )
        bad |= present & ~matched.fillna(True).astype(bool)
        parts.append(f"regex={rule.regex!r}")

    n = int(bad.sum())
    rule_str = ", ".join(parts) or "no constraints"
    return result(
        f"{CHECK}.domain[{col}]", layer, source,
        status_for(n, cfg.severity),
        f"Column '{col}' conforms to domain ({rule_str})."
        if n == 0
        else f"Column '{col}' has {n} value(s) outside domain ({rule_str}).",
        severity=cfg.severity, n_total=n_total, n_failed=n,
        issues=build_issues(gdf.index[bad], gdf, f"out-of-domain value in '{col}'", id_col=col),
    )
