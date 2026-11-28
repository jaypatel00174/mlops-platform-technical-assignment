"""Keyset pagination: an opaque cursor wrapping the last id of the previous page."""

from __future__ import annotations

import base64
import binascii

from mlops.kernel.errors import ValidationFailure


def encode_cursor(last_id: str) -> str:
    return base64.urlsafe_b64encode(last_id.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> str | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError) as error:
        raise ValidationFailure("the cursor is not valid", field="cursor") from error
