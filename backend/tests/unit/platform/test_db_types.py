from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import sqlite
from sqlalchemy.exc import IntegrityError

from mlops.platform.db import UTCDateTime, integrity_kind

DIALECT = sqlite.dialect()


def test_aware_datetimes_are_normalised_to_utc_on_the_way_in() -> None:
    plus_two = timezone(timedelta(hours=2))
    value = datetime(2026, 1, 15, 14, 0, tzinfo=plus_two)

    stored = UTCDateTime().process_bind_param(value, DIALECT)

    assert stored == datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    assert stored is not None
    assert stored.utcoffset() == timedelta(0)


def test_naive_datetimes_are_refused_on_the_way_in() -> None:
    with pytest.raises(ValueError, match="naive"):
        UTCDateTime().process_bind_param(datetime(2026, 1, 15, 12, 0), DIALECT)


def test_none_passes_through_in_both_directions() -> None:
    assert UTCDateTime().process_bind_param(None, DIALECT) is None
    assert UTCDateTime().process_result_value(None, DIALECT) is None


def test_naive_values_from_sqlite_come_back_as_utc() -> None:
    restored = UTCDateTime().process_result_value(datetime(2026, 1, 15, 12, 0), DIALECT)

    assert restored == datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    assert restored is not None
    assert restored.tzinfo is UTC


def test_aware_values_from_postgres_are_converted_to_utc() -> None:
    plus_two = timezone(timedelta(hours=2))

    restored = UTCDateTime().process_result_value(
        datetime(2026, 1, 15, 14, 0, tzinfo=plus_two), DIALECT
    )

    assert restored == datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


class _FakeDriverError(Exception):
    def __init__(self, message: str, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def _integrity_error(message: str, sqlstate: str | None = None) -> IntegrityError:
    return IntegrityError("INSERT ...", {}, _FakeDriverError(message, sqlstate))


@pytest.mark.parametrize(
    ("message", "sqlstate", "expected"),
    [
        ("duplicate key value violates unique constraint", "23505", "unique"),
        ("insert or update violates foreign key constraint", "23503", "foreign_key"),
        ("UNIQUE constraint failed: model.id", None, "unique"),
        ("FOREIGN KEY constraint failed", None, "foreign_key"),
        ("CHECK constraint failed: stage_valid", None, "other"),
        ("null value in column violates not-null constraint", "23502", "other"),
    ],
)
def test_integrity_errors_are_classified_for_postgres_and_sqlite(
    message: str, sqlstate: str | None, expected: str
) -> None:
    assert integrity_kind(_integrity_error(message, sqlstate)) == expected
