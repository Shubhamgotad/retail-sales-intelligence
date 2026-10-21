from datetime import date
from decimal import Decimal

import pytest

from app.transformations.product_costs import (
    ProductCostValidationError,
    ProductUnitCost,
    validate_product_cost_record,
    validate_product_cost_records,
)

PRODUCT_KEYS = {"00123", "ABC-1"}


def cost_record(**overrides):
    record = {
        "stock_code": "00123",
        "unit_cost": "12.3400",
        "currency_code": "GBP",
        "effective_from": "2010-12-01",
        "effective_to": "2011-06-30",
        "source_reference": "supplier-invoice-2026-0042.pdf",
    }
    record.update(overrides)
    return record


def test_valid_effective_dated_cost_record_preserves_exact_decimal_and_product_key():
    result = validate_product_cost_record(cost_record(), PRODUCT_KEYS)
    assert result == ProductUnitCost(
        stock_code="00123",
        unit_cost=Decimal("12.3400"),
        currency_code="GBP",
        effective_from=date(2010, 12, 1),
        effective_to=date(2011, 6, 30),
        source_reference="supplier-invoice-2026-0042.pdf",
    )


def test_valid_record_allows_open_ended_effective_period_and_normalizes_key():
    result = validate_product_cost_record(
        cost_record(stock_code=" abc-1 ", effective_to=""),
        PRODUCT_KEYS,
    )
    assert result.stock_code == "ABC-1"
    assert result.effective_to is None


@pytest.mark.parametrize(
    "field",
    ["stock_code", "unit_cost", "currency_code", "effective_from", "source_reference"],
)
def test_required_fields_must_be_present_and_nonblank(field):
    with pytest.raises(ProductCostValidationError, match=field):
        validate_product_cost_record(cost_record(**{field: "  "}), PRODUCT_KEYS)


def test_missing_csv_headers_are_reported():
    record = cost_record()
    del record["source_reference"]
    with pytest.raises(ProductCostValidationError, match="Missing required field"):
        validate_product_cost_record(record, PRODUCT_KEYS)


def test_effective_to_column_is_required_even_when_its_value_is_optional():
    record = cost_record()
    del record["effective_to"]
    with pytest.raises(ProductCostValidationError, match="effective_to"):
        validate_product_cost_record(record, PRODUCT_KEYS)


def test_unknown_stock_code_is_rejected():
    with pytest.raises(ProductCostValidationError, match="does not match"):
        validate_product_cost_record(cost_record(stock_code="NOT-IN-SOURCE"), PRODUCT_KEYS)


@pytest.mark.parametrize("value", ["", "1.2.3", "NaN", "Infinity", "1e2", 12.34])
def test_invalid_or_float_rounded_unit_cost_is_rejected(value):
    with pytest.raises(ProductCostValidationError, match="unit_cost"):
        validate_product_cost_record(cost_record(unit_cost=value), PRODUCT_KEYS)


@pytest.mark.parametrize("value", ["-0.01", "-1"])
def test_negative_unit_cost_is_rejected(value):
    with pytest.raises(ProductCostValidationError, match="non-negative"):
        validate_product_cost_record(cost_record(unit_cost=value), PRODUCT_KEYS)


@pytest.mark.parametrize("value", ["", "US dollars", "ZZZ", "GB"])
def test_currency_must_be_an_explicit_recognized_iso_code(value):
    with pytest.raises(ProductCostValidationError, match="currency_code"):
        validate_product_cost_record(cost_record(currency_code=value), PRODUCT_KEYS)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("effective_from", "2011-02-30"),
        ("effective_from", "20110101"),
        ("effective_to", "tomorrow"),
    ],
)
def test_malformed_or_impossible_effective_dates_are_rejected(field, value):
    with pytest.raises(ProductCostValidationError, match=field):
        validate_product_cost_record(cost_record(**{field: value}), PRODUCT_KEYS)


def test_effective_end_must_not_precede_start():
    with pytest.raises(ProductCostValidationError, match="must not precede"):
        validate_product_cost_record(
            cost_record(effective_from="2011-06-30", effective_to="2011-06-29"),
            PRODUCT_KEYS,
        )


def test_cost_period_validator_supports_multiple_years_without_current_date_assumptions():
    result = validate_product_cost_record(
        cost_record(
            effective_from="2009-12-01",
            effective_to="2011-12-31",
        ),
        PRODUCT_KEYS,
    )
    assert result.effective_from == date(2009, 12, 1)
    assert result.effective_to == date(2011, 12, 31)


def test_conflicting_overlapping_periods_for_same_product_and_currency_are_rejected():
    records = [
        cost_record(effective_from="2010-12-01", effective_to="2011-06-30"),
        cost_record(
            unit_cost="13.00",
            effective_from="2011-06-30",
            effective_to="2012-01-01",
        ),
    ]
    with pytest.raises(ProductCostValidationError, match="Conflicting overlapping"):
        validate_product_cost_records(records, PRODUCT_KEYS)


def test_nonconflicting_periods_products_and_currencies_are_accepted():
    records = [
        cost_record(effective_from="2010-12-01", effective_to="2011-06-29"),
        cost_record(
            unit_cost="13.00",
            effective_from="2011-06-30",
            effective_to="2012-01-01",
        ),
        cost_record(
            unit_cost="15.00",
            currency_code="USD",
            effective_from="2011-01-01",
            effective_to="2012-01-01",
        ),
        cost_record(
            stock_code="ABC-1",
            effective_from="2010-12-01",
            effective_to="2012-01-01",
        ),
    ]
    validated = validate_product_cost_records(records, PRODUCT_KEYS)
    assert len(validated) == len(records)


def test_identical_cost_period_overlap_is_not_a_cost_conflict():
    records = [
        cost_record(effective_from="2010-12-01", effective_to="2011-06-30"),
        cost_record(effective_from="2011-01-01", effective_to="2011-07-01"),
    ]
    assert len(validate_product_cost_records(records, PRODUCT_KEYS)) == 2
