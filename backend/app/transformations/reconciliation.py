"""Pure load-readiness and reconciliation checks for real-retail data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from app.transformations.online_retail import CURRENCY_UNKNOWN


@dataclass(frozen=True)
class LoadState:
    """Application-level state and counters for one real-retail load."""

    status: str
    rows_read: int
    rows_loaded: int
    rows_rejected: int
    completed_at: datetime | None = None


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    observed: Any
    expected: Any


@dataclass(frozen=True)
class LoadStateValidation:
    is_valid: bool
    issues: tuple[ValidationIssue, ...]


class LoadStateValidationError(ValueError):
    """Raised when callers request exception-based load-state validation."""

    def __init__(self, issues: Sequence[ValidationIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(issue.message for issue in self.issues))


def validate_load_state(
    state: LoadState,
    *,
    allow_rejected: bool = False,
    raise_on_error: bool = False,
) -> LoadStateValidation:
    """Validate whether a load state is safe to mark as completed.

    This is intentionally separate from the database DDL. The current schema
    constrains column values and identity, but does not enforce these
    cross-column or status-transition rules.
    """
    issues: list[ValidationIssue] = []

    for field_name in ("rows_read", "rows_loaded", "rows_rejected"):
        value = getattr(state, field_name)
        if value < 0:
            issues.append(
                ValidationIssue(
                    code="negative_count",
                    message=f"{field_name} cannot be negative",
                    observed=value,
                    expected=">= 0",
                )
            )

    if all(getattr(state, field) >= 0 for field in ("rows_read", "rows_loaded", "rows_rejected")):
        if state.rows_loaded + state.rows_rejected != state.rows_read:
            issues.append(
                ValidationIssue(
                    code="count_equation_mismatch",
                    message="rows_loaded + rows_rejected must equal rows_read",
                    observed={
                        "rows_loaded": state.rows_loaded,
                        "rows_rejected": state.rows_rejected,
                        "rows_read": state.rows_read,
                    },
                    expected=state.rows_read,
                )
            )

    if state.status == "completed":
        if state.completed_at is None:
            issues.append(
                ValidationIssue(
                    code="missing_completion_timestamp",
                    message="completed loads require completed_at",
                    observed=None,
                    expected="timestamp",
                )
            )
        if state.rows_rejected and not allow_rejected:
            issues.append(
                ValidationIssue(
                    code="rejected_rows_not_allowed",
                    message="completed loads cannot contain rejected rows",
                    observed=state.rows_rejected,
                    expected=0,
                )
            )
    elif state.completed_at is not None:
        issues.append(
            ValidationIssue(
                code="unexpected_completion_timestamp",
                message="only completed loads may have completed_at",
                observed=state.completed_at.isoformat(),
                expected=None,
            )
        )

    if state.status in {"failed", "incomplete"}:
        issues.append(
            ValidationIssue(
                code="non_completed_status",
                message=f"{state.status} loads cannot be represented as completed",
                observed=state.status,
                expected="completed",
            )
        )

    result = LoadStateValidation(not issues, tuple(issues))
    if raise_on_error and issues:
        raise LoadStateValidationError(issues)
    return result


@dataclass(frozen=True)
class RowSummary:
    """The facts needed to reconcile one source or transformed row."""

    source_line_identity: tuple[str, str, int]
    signed_quantity: Decimal
    currency_code: str
    is_possible_duplicate: bool = False
    is_cancellation_candidate: bool = False
    is_negative_quantity_candidate: bool = False
    is_missing_customer: bool = False
    is_missing_description: bool = False
    fabricated_values: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ReconciliationInput:
    source_row_count: int
    staged_row_count: int
    transformed_row_count: int
    rejected_row_count: int
    target_ready_row_count: int
    source_rows: tuple[RowSummary, ...]
    transformed_rows: tuple[RowSummary, ...]


@dataclass(frozen=True)
class ReconciliationCheck:
    name: str
    passed: bool
    observed: Any
    expected: Any
    message: str


@dataclass(frozen=True)
class ReconciliationReport:
    checks: tuple[ReconciliationCheck, ...]
    ready: bool


def _check(
    name: str,
    passed: bool,
    observed: Any,
    expected: Any,
    message: str,
) -> ReconciliationCheck:
    return ReconciliationCheck(name, passed, observed, expected, message)


def _identities(rows: Sequence[RowSummary]) -> list[tuple[str, str, int]]:
    return [row.source_line_identity for row in rows]


def _duplicate_count(rows: Sequence[RowSummary]) -> int:
    return sum(row.is_possible_duplicate for row in rows)


def _classification_counts(rows: Sequence[RowSummary]) -> dict[str, int]:
    return {
        "cancellation_candidates": sum(row.is_cancellation_candidate for row in rows),
        "negative_quantity_candidates": sum(row.is_negative_quantity_candidate for row in rows),
        "missing_customers": sum(row.is_missing_customer for row in rows),
        "missing_descriptions": sum(row.is_missing_description for row in rows),
        "duplicate_content_rows": _duplicate_count(rows),
    }


def reconcile(input_data: ReconciliationInput) -> ReconciliationReport:
    """Return deterministic named checks for a proposed real-retail load."""
    checks: list[ReconciliationCheck] = []
    counts = (
        input_data.source_row_count,
        input_data.staged_row_count,
        input_data.transformed_row_count,
        input_data.rejected_row_count,
        input_data.target_ready_row_count,
    )
    checks.append(
        _check(
            "nonnegative_counts",
            all(count >= 0 for count in counts),
            {
                "source": input_data.source_row_count,
                "staged": input_data.staged_row_count,
                "transformed": input_data.transformed_row_count,
                "rejected": input_data.rejected_row_count,
                "target_ready": input_data.target_ready_row_count,
            },
            "all counts >= 0",
            "all reconciliation counts must be nonnegative",
        )
    )
    checks.append(
        _check(
            "source_matches_staged",
            input_data.source_row_count == input_data.staged_row_count,
            input_data.staged_row_count,
            input_data.source_row_count,
            "source row count must equal staged row count",
        )
    )
    checks.append(
        _check(
            "staged_matches_transformed_plus_rejected",
            input_data.staged_row_count
            == input_data.transformed_row_count + input_data.rejected_row_count,
            input_data.transformed_row_count + input_data.rejected_row_count,
            input_data.staged_row_count,
            "staged rows must equal transformed plus rejected rows",
        )
    )
    checks.append(
        _check(
            "transformed_matches_target_ready",
            input_data.transformed_row_count == input_data.target_ready_row_count,
            input_data.target_ready_row_count,
            input_data.transformed_row_count,
            "transformed rows must equal target-ready rows",
        )
    )

    source_rows = input_data.source_rows
    transformed_rows = input_data.transformed_rows
    checks.append(
        _check(
            "source_summary_count_matches",
            len(source_rows) == input_data.source_row_count,
            len(source_rows),
            input_data.source_row_count,
            "source summaries must account for every source row",
        )
    )
    checks.append(
        _check(
            "transformed_summary_count_matches",
            len(transformed_rows) == input_data.transformed_row_count,
            len(transformed_rows),
            input_data.transformed_row_count,
            "transformed summaries must account for every transformed row",
        )
    )
    source_ids = _identities(source_rows)
    transformed_ids = _identities(transformed_rows)
    checks.append(
        _check(
            "source_line_identity_preserved",
            len(source_ids) == len(set(source_ids)) and source_ids == transformed_ids,
            {"source": source_ids, "transformed": transformed_ids},
            "same unique identities in the same order",
            "source-line identities must be present once and preserved",
        )
    )
    checks.append(
        _check(
            "signed_quantities_preserved",
            [row.signed_quantity for row in source_rows]
            == [row.signed_quantity for row in transformed_rows],
            [row.signed_quantity for row in transformed_rows],
            [row.signed_quantity for row in source_rows],
            "signed quantity values must be unchanged",
        )
    )
    checks.append(
        _check(
            "currency_unknown",
            all(row.currency_code == CURRENCY_UNKNOWN for row in transformed_rows),
            sorted({row.currency_code for row in transformed_rows}),
            [CURRENCY_UNKNOWN],
            "currency must remain UNKNOWN",
        )
    )
    checks.append(
        _check(
            "duplicate_content_preserved",
            _duplicate_count(source_rows) == _duplicate_count(transformed_rows),
            _duplicate_count(transformed_rows),
            _duplicate_count(source_rows),
            "duplicate-content rows must remain represented",
        )
    )
    source_classifications = _classification_counts(source_rows)
    transformed_classifications = _classification_counts(transformed_rows)
    checks.append(
        _check(
            "classifications_preserved",
            source_classifications == transformed_classifications,
            transformed_classifications,
            source_classifications,
            "quality classifications must be preserved and counted separately",
        )
    )
    checks.append(
        _check(
            "no_fabricated_values",
            all(not row.fabricated_values for row in transformed_rows),
            [row.fabricated_values for row in transformed_rows if row.fabricated_values],
            [],
            "cost, profit, store, discount, and converted currency values must not be fabricated",
        )
    )
    return ReconciliationReport(tuple(checks), all(check.passed for check in checks))


def reconcile_stream(
    *,
    source_rows: Iterable[RowSummary],
    transformed_rows: Iterable[RowSummary],
    source_row_count: int,
    staged_row_count: int,
    transformed_row_count: int,
    rejected_row_count: int,
    target_ready_row_count: int,
) -> ReconciliationReport:
    """Reconcile batches without retaining every row in memory.

    The iterables must be in source-line order. Identity sets are retained so
    duplicate lineage remains detectable, while row payloads are discarded
    after each comparison.
    """
    source_ids: set[tuple[str, str, int]] = set()
    transformed_ids: set[tuple[str, str, int]] = set()
    source_classifications = {
        "cancellation_candidates": 0,
        "negative_quantity_candidates": 0,
        "missing_customers": 0,
        "missing_descriptions": 0,
        "duplicate_content_rows": 0,
    }
    transformed_classifications = dict(source_classifications)
    source_count = transformed_count = 0
    signed_quantity_mismatches = 0
    currency_values: set[str] = set()
    fabricated_rows = 0
    source_iter = iter(source_rows)
    transformed_iter = iter(transformed_rows)

    while True:
        source = next(source_iter, None)
        transformed = next(transformed_iter, None)
        if source is None and transformed is None:
            break
        if source is not None:
            source_count += 1
            source_ids.add(source.source_line_identity)
            for key, value in _classification_counts((source,)).items():
                source_classifications[key] += value
        if transformed is not None:
            transformed_count += 1
            transformed_ids.add(transformed.source_line_identity)
            currency_values.add(transformed.currency_code)
            for key, value in _classification_counts((transformed,)).items():
                transformed_classifications[key] += value
            if transformed.fabricated_values:
                fabricated_rows += 1
        if source is None or transformed is None:
            continue
        if source.source_line_identity != transformed.source_line_identity:
            signed_quantity_mismatches += 1
        if source.signed_quantity != transformed.signed_quantity:
            signed_quantity_mismatches += 1

    checks = [
        _check(
            "nonnegative_counts",
            all(
                count >= 0
                for count in (
                    source_row_count,
                    staged_row_count,
                    transformed_row_count,
                    rejected_row_count,
                    target_ready_row_count,
                )
            ),
            {
                "source": source_row_count,
                "staged": staged_row_count,
                "transformed": transformed_row_count,
                "rejected": rejected_row_count,
                "target_ready": target_ready_row_count,
            },
            "all counts >= 0",
            "all reconciliation counts must be nonnegative",
        ),
        _check(
            "source_matches_staged",
            source_row_count == staged_row_count,
            staged_row_count,
            source_row_count,
            "source row count must equal staged row count",
        ),
        _check(
            "staged_matches_transformed_plus_rejected",
            staged_row_count == transformed_row_count + rejected_row_count,
            transformed_row_count + rejected_row_count,
            staged_row_count,
            "staged rows must equal transformed plus rejected rows",
        ),
        _check(
            "transformed_matches_target_ready",
            transformed_row_count == target_ready_row_count,
            target_ready_row_count,
            transformed_row_count,
            "transformed rows must equal target-ready rows",
        ),
        _check(
            "source_summary_count_matches",
            source_count == source_row_count,
            source_count,
            source_row_count,
            "source summaries must account for every source row",
        ),
        _check(
            "transformed_summary_count_matches",
            transformed_count == transformed_row_count,
            transformed_count,
            transformed_row_count,
            "transformed summaries must account for every transformed row",
        ),
        _check(
            "source_line_identity_preserved",
            source_count == transformed_count
            and len(source_ids) == source_count
            and len(transformed_ids) == transformed_count
            and source_ids == transformed_ids,
            {"source_unique": len(source_ids), "transformed_unique": len(transformed_ids)},
            "same unique identities",
            "source-line identities must be present once and preserved",
        ),
        _check(
            "signed_quantities_preserved",
            signed_quantity_mismatches == 0,
            signed_quantity_mismatches,
            0,
            "signed quantity values must be unchanged",
        ),
        _check(
            "currency_unknown",
            currency_values <= {CURRENCY_UNKNOWN},
            sorted(currency_values),
            [CURRENCY_UNKNOWN],
            "currency must remain UNKNOWN",
        ),
        _check(
            "duplicate_content_preserved",
            source_classifications["duplicate_content_rows"]
            == transformed_classifications["duplicate_content_rows"],
            transformed_classifications["duplicate_content_rows"],
            source_classifications["duplicate_content_rows"],
            "duplicate-content rows must remain represented",
        ),
        _check(
            "classifications_preserved",
            source_classifications == transformed_classifications,
            transformed_classifications,
            source_classifications,
            "quality classifications must be preserved and counted separately",
        ),
        _check(
            "no_fabricated_values",
            fabricated_rows == 0,
            fabricated_rows,
            0,
            "cost, profit, store, discount, and converted currency values must not be fabricated",
        ),
    ]
    return ReconciliationReport(tuple(checks), all(check.passed for check in checks))
