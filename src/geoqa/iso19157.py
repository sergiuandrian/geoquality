"""ISO 19157 data-quality element tags for geoqa checks.

These are **documentation / metadata tags** for procurement-friendly reports.
geoqa is not a certified ISO 19157 validator.
"""

from __future__ import annotations

# Check-name prefix → ISO 19157 DQ element (informative).
# More specific prefixes should be listed before shorter ones when matching.
DQ_BY_PREFIX: list[tuple[str, str]] = [
    ("attributes.domains", "DQ_DomainConsistency"),
    ("attributes.unique", "DQ_ConceptualConsistency"),
    ("attributes.required", "DQ_CompletenessOmission"),
    ("attributes.not_null", "DQ_CompletenessOmission"),
    ("attributes.max_null_fraction", "DQ_CompletenessOmission"),
    ("attributes", "DQ_CompletenessOmission"),
    ("geometry.no_missing", "DQ_CompletenessOmission"),
    ("geometry.no_empty", "DQ_CompletenessOmission"),
    ("geometry.valid", "DQ_ConceptualConsistency"),
    ("geometry.repair", "DQ_ConceptualConsistency"),
    ("geometry", "DQ_ConceptualConsistency"),
    ("duplicates", "DQ_CompletenessCommission"),
    ("topology", "DQ_TopologicalConsistency"),
    ("crs", "DQ_AbsoluteExternalPositionalAccuracy"),
    ("schema", "DQ_FormatConsistency"),
    ("metadata", "DQ_CompletenessOmission"),
]


def dq_element_for(check: str) -> str | None:
    """Return the ISO 19157 DQ element name for a check id, if known."""
    for prefix, element in DQ_BY_PREFIX:
        if check == prefix or check.startswith(prefix + ".") or check.startswith(prefix + "["):
            return element
    return None
