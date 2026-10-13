from datetime import datetime
from decimal import Decimal

import pytest

from app.transformations.online_retail import CURRENCY_UNKNOWN
from app.transformations.reconciliation import (
    LoadState,
    LoadStateValidationError,
    ReconciliationInput,
    RowSummary,
    reconcile,
    reconcile_stream,
    validate_load_state,
)


def row(
    identity: tuple[str, str, int],
    quantity: str = "2",
    **overrides,
) -> RowSummary:
    values = {
        "source_line_identity": identity,
        "signed_quantity": Decimal(quantity),
        "currency_code": CURRENCY_UNKNOWN,
        "is_possible_duplicate": False,
        "is_cancellation_candidate": False,
        "is_negative_quantity_candidate": quantity.startswith("-"),
        "is_missing_customer": False,
        "is_missing_description": False,
        "fabricated_values": None,
    }
    values.update(overrides)
    return RowSummary(**values)


def clean_input(**overrides) -> ReconciliationInput:
    rows = (
        row(("a" * 64, "Year 2009-2010", 2), "2"),
        row(
            ("a" * 64, "Year 2010-2011", 2),
            "-1",
            is_possible_duplicate=True,
            is_cancellation_candidate=True,
            is_missing_customer=True,
            is_missing_description=True,
        ),
    )
    values = {
        "source_row_count": 2,
        "staged_row_count": 2,
        "transformed_row_count": 2,
        "rejected_row_count": 0,
        "target_ready_row_count": 2,
        "source_rows": rows,
        "transformed_rows": rows,
    }
    values.update(overrides)
    return ReconciliationInput(**values)


def test_clean_reconciliation_is_ready_and_deterministic():
    first = reconcile(clean_input())
    second = reconcile(clean_input())

    assert first.ready is True
    assert first == second
    assert all(check.passed for check in first.checks)


def test_stream_reconciliation_matches_clean_result_without_row_storage():
    data = clean_input()
    report = reconcile_stream(
        source_rows=iter(data.source_rows),
        transformed_rows=iter(data.transformed_rows),
        source_row_count=data.source_row_count,
        staged_row_count=data.staged_row_count,
        transformed_row_count=data.transformed_row_count,
        rejected_row_count=data.rejected_row_count,
        target_ready_row_count=data.target_ready_row_count,
    )
    assert report.ready is True
    assert {check.name for check in report.checks} >= {
        "source_line_identity_preserved",
        "signed_quantities_preserved",
        "classifications_preserved",
    }


@pytest.mark.parametrize(
    "field",
    [
        "source_row_count",
        "staged_row_count",
        "transformed_row_count",
        "rejected_row_count",
        "target_ready_row_count",
    ],
)
def test_negative_counts_fail(field):
    report = reconcile(clean_input(**{field: -1}))
    assert report.ready is False
    assert report.checks[0].name == "nonnegative_counts"
    assert report.checks[0].passed is False


@pytest.mark.parametrize(
    "overrides,check_name",
    [
        ({"staged_row_count": 1}, "source_matches_staged"),
        ({"rejected_row_count": 1}, "staged_matches_transformed_plus_rejected"),
        ({"target_ready_row_count": 1}, "transformed_matches_target_ready"),
    ],
)
def test_count_equations_fail_independently(overrides, check_name):
    report = reconcile(clean_input(**overrides))
    assert report.ready is False
    assert next(check for check in report.checks if check.name == check_name).passed is False


def test_signed_quantities_missing_values_and_classifications_are_preserved():
    source = clean_input().source_rows
    transformed = (
        source[0],
        row(
            source[1].source_line_identity,
            "-1",
            is_possible_duplicate=True,
            is_cancellation_candidate=True,
            is_missing_customer=True,
            is_missing_description=True,
        ),
    )
    report = reconcile(clean_input(transformed_rows=transformed))
    assert report.ready is True
    assert next(
        check for check in report.checks if check.name == "signed_quantities_preserved"
    ).passed


def test_duplicate_identity_fails_without_removing_duplicate_content_rows():
    duplicate_identity = ("a" * 64, "Year 2009-2010", 2)
    rows = (
        row(duplicate_identity),
        row(duplicate_identity, "3", is_possible_duplicate=True),
    )
    report = reconcile(clean_input(source_rows=rows, transformed_rows=rows))
    assert report.ready is False
    check = next(check for check in report.checks if check.name == "source_line_identity_preserved")
    assert check.passed is False
    assert next(
        check for check in report.checks if check.name == "duplicate_content_preserved"
    ).passed


def test_missing_transformed_identity_fails():
    transformed = (clean_input().transformed_rows[0],)
    report = reconcile(clean_input(transformed_rows=transformed))
    assert report.ready is False
    assert next(
        check for check in report.checks if check.name == "source_line_identity_preserved"
    ).passed is False


def test_non_unknown_currency_fails():
    transformed = (
        row(("a" * 64, "Year 2009-2010", 2), currency_code="GBP"),
    )
    report = reconcile(
        clean_input(
            source_row_count=1,
            staged_row_count=1,
            transformed_row_count=1,
            target_ready_row_count=1,
            source_rows=transformed,
            transformed_rows=transformed,
        )
    )
    assert report.ready is False
    assert next(check for check in report.checks if check.name == "currency_unknown").passed is False


def test_fabricated_values_fail():
    transformed = (
        row(
            ("a" * 64, "Year 2009-2010", 2),
            fabricated_values={"profit": 10},
        ),
    )
    report = reconcile(
        clean_input(
            source_row_count=1,
            staged_row_count=1,
            transformed_row_count=1,
            target_ready_row_count=1,
            source_rows=transformed,
            transformed_rows=transformed,
        )
    )
    assert report.ready is False
    assert next(check for check in report.checks if check.name == "no_fabricated_values").passed is False


def test_completed_load_requires_timestamp_and_reject_policy():
    missing_timestamp = validate_load_state(
        LoadState("completed", rows_read=2, rows_loaded=2, rows_rejected=0)
    )
    assert missing_timestamp.is_valid is False
    assert missing_timestamp.issues[0].code == "missing_completion_timestamp"

    rejected = validate_load_state(
        LoadState(
            "completed",
            rows_read=2,
            rows_loaded=1,
            rows_rejected=1,
            completed_at=datetime(2026, 1, 1),
        )
    )
    assert rejected.is_valid is False
    assert any(issue.code == "rejected_rows_not_allowed" for issue in rejected.issues)
    assert validate_load_state(
        LoadState(
            "completed",
            rows_read=2,
            rows_loaded=1,
            rows_rejected=1,
            completed_at=datetime(2026, 1, 1),
        ),
        allow_rejected=True,
    ).is_valid is True


@pytest.mark.parametrize("status", ["failed", "incomplete"])
def test_failed_or_incomplete_load_cannot_be_completed(status):
    result = validate_load_state(
        LoadState(status, rows_read=0, rows_loaded=0, rows_rejected=0)
    )
    assert result.is_valid is False
    assert any(issue.code == "non_completed_status" for issue in result.issues)


def test_count_mismatch_and_negative_count_are_structured_errors():
    result = validate_load_state(
        LoadState("completed", rows_read=-1, rows_loaded=0, rows_rejected=0)
    )
    assert result.is_valid is False
    assert {issue.code for issue in result.issues} == {
        "negative_count",
        "missing_completion_timestamp",
    }

    with pytest.raises(LoadStateValidationError, match="rows_read cannot be negative"):
        validate_load_state(
            LoadState("completed", rows_read=-1, rows_loaded=0, rows_rejected=0),
            raise_on_error=True,
        )


def test_non_completed_timestamp_is_rejected():
    result = validate_load_state(
        LoadState(
            "in_progress",
            rows_read=0,
            rows_loaded=0,
            rows_rejected=0,
            completed_at=datetime(2026, 1, 1),
        )
    )
    assert result.is_valid is False
    assert any(issue.code == "unexpected_completion_timestamp" for issue in result.issues)
