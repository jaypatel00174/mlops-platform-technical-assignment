"""Optimistic concurrency at the API boundary: ETag and If-Match over the row_version counter."""

from __future__ import annotations

from mlops.kernel.errors import StaleVersion


def etag_for(row_version: int) -> str:
    return f'W/"{row_version}"'


def ensure_unchanged(if_match: str | None, row_version: int) -> None:
    """Raise StaleVersion if the caller's If-Match no longer matches the stored version.

    No header means the caller did not ask for the check. `*` matches anything that exists.
    """
    if if_match is None or if_match.strip() == "*":
        return
    wanted = [part.strip() for part in if_match.split(",")]
    if etag_for(row_version) not in wanted:
        raise StaleVersion(
            "the resource changed since you read it; reload it and try again",
            current_etag=etag_for(row_version),
            if_match=if_match,
        )
