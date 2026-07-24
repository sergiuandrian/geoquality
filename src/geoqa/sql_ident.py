"""SQL identifier quoting and connection URL redaction."""

from __future__ import annotations


def quote_table(table: str) -> str:
    """Quote a schema-qualified table name for PostgreSQL (``"schema"."table"``).

    Rejects empty parts and characters that would break out of a quoted
    identifier. Mixed-case and reserved-word names are preserved by quoting.
    """
    parts = [p.strip().strip('"') for p in table.split(".")]
    if not parts or len(parts) > 2 or any(not p for p in parts):
        raise ValueError(f"invalid table identifier: {table!r}")
    for part in parts:
        if any(c in part for c in ';--"\'\\') or "\x00" in part:
            raise ValueError(f"invalid table identifier: {table!r}")
    return ".".join(f'"{p}"' for p in parts)


def redact(url: str) -> str:
    """Hide credentials in a connection URL before it lands in a report."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.split("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return url


# Backward-compatible private aliases (used by older call sites / tests).
_quote_table = quote_table
_redact = redact
