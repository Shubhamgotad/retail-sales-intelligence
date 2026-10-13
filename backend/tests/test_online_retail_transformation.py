from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.transformations.online_retail import (
    CURRENCY_UNKNOWN,
    POLICY_VERSION,
    TransformationError,
    source_line_identity,
    transform_staged_row,
)


def make_row(**overrides):
    row = {
        "source_sha256": "a" * 64,
        "worksheet_name": "Year 2010-2011",
        "worksheet_row_number": 2,
        "source_invoice_value": " c10001 ",
        "source_stock_code": " abc 123 ",
        "source_date_value": "2010-12-01T08:30:00",
        "original_quantity": -2,
        "original_price": -1.5,
        "original_customer_value": "123.0",
        "original_country": "United Kingdom",
        "original_description": None,
        "is_possible_duplicate_content": True,
        "source_content_hash": "b" * 64,
    }
    row.update(overrides)
    return row


def test_valid_transformation_preserves_signed_values_and_flags():
    result = transform_staged_row(make_row())

    assert result.source_invoice == "C10001"
    assert result.source_product_key == "ABC 123"
    assert result.source_customer_key == "123"
    assert result.invoice_datetime == datetime(2010, 12, 1, 8, 30)
    assert result.signed_quantity == Decimal("-2")
    assert result.source_price == Decimal("-1.5")
    assert result.is_cancellation_candidate is True
    assert result.is_negative_quantity_candidate is True
    assert result.is_negative_price is True
    assert result.is_zero_price is False
    assert result.is_missing_customer is False
    assert result.is_missing_description is True
    assert result.is_possible_duplicate is True
    assert result.currency_code == CURRENCY_UNKNOWN
    assert POLICY_VERSION == "online-retail-v1-unknown-currency-signed-quantity"


def test_normalization_and_line_identity_are_stable():
    first = transform_staged_row(make_row())
    second = transform_staged_row(make_row())
    assert first == second
    assert source_line_identity(first) == ("a" * 64, "Year 2010-2011", 2)


def test_zero_price_and_missing_customer_are_flagged():
    result = transform_staged_row(
        make_row(
            source_invoice_value="10001",
            original_quantity=3,
            original_price=0,
            original_customer_value=None,
            original_description="Item",
        )
    )
    assert result.is_cancellation_candidate is False
    assert result.is_negative_quantity_candidate is False
    assert result.is_zero_price is True
    assert result.is_negative_price is False
    assert result.is_missing_customer is True
    assert result.is_missing_description is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_date_value", "not-a-date", "InvoiceDate is malformed"),
        ("original_quantity", "not-a-number", "Quantity is not numeric"),
        ("original_price", "not-a-number", "Price is not numeric"),
    ],
)
def test_malformed_values_raise_clear_errors(field, value, message):
    with pytest.raises(TransformationError, match=message):
        transform_staged_row(make_row(**{field: value}))


def test_missing_lineage_is_rejected_without_fabrication():
    with pytest.raises(TransformationError, match="worksheet_row_number"):
        transform_staged_row(make_row(worksheet_row_number=None))


def test_optional_staging_row_id_is_preserved_and_validated():
    result = transform_staged_row(make_row(staging_row_id=42))
    assert result.staging_row_id == 42
    with pytest.raises(TransformationError, match="staging_row_id"):
        transform_staged_row(make_row(staging_row_id=0))


def test_duplicate_flag_does_not_remove_or_change_the_row():
    duplicate = transform_staged_row(make_row(is_possible_duplicate_content=True))
    ordinary = transform_staged_row(make_row(is_possible_duplicate_content=False))
    assert duplicate.is_possible_duplicate is True
    assert ordinary.is_possible_duplicate is False
    assert duplicate.signed_quantity == ordinary.signed_quantity


def test_isolated_schema_ddl_is_idempotent_by_inspection_only():
    ddl = (
        Path(__file__).resolve().parents[2] / "database" / "real_retail_schema.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS real_retail" in ddl
    assert "CREATE TABLE IF NOT EXISTS real_retail.loads" in ddl
    assert "CREATE TABLE IF NOT EXISTS real_retail.sales_lines" in ddl
    assert "UNIQUE (source_sha256, worksheet_name, worksheet_row_number)" in ddl
    assert "currency_code = 'UNKNOWN'" in ddl
    assert "quantity > 0" not in ddl
