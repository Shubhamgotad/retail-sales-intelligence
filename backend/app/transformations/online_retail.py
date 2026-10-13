"""Pure, deterministic transformations for staged Online Retail II rows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

POLICY_VERSION = "online-retail-v1-unknown-currency-signed-quantity"
CURRENCY_UNKNOWN = "UNKNOWN"


class TransformationError(ValueError):
    """A staged row cannot be transformed without silent coercion."""


@dataclass(frozen=True)
class RealRetailSalesLine:
    staging_row_id: int | None
    source_sha256: str
    worksheet_name: str
    worksheet_row_number: int
    source_invoice: str | None
    invoice_datetime: datetime | None
    source_product_key: str | None
    source_customer_key: str | None
    source_description: str | None
    source_country: str | None
    signed_quantity: Decimal | None
    source_price: Decimal | None
    currency_code: str
    is_cancellation_candidate: bool
    is_negative_quantity_candidate: bool
    is_zero_price: bool
    is_negative_price: bool
    is_missing_customer: bool
    is_missing_description: bool
    is_possible_duplicate: bool
    source_content_hash: str | None


def normalize_source_key(value: Any) -> str | None:
    """Normalize external identifiers without changing their identity.

    Whitespace is trimmed, internal whitespace is collapsed, and identifiers
    are upper-cased. Numeric customer IDs emitted as ``123.0`` are normalized
    to ``123`` because Excel commonly represents them as floating values.
    """
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.upper()


def _required_text(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if value is None or not str(value).strip():
        raise TransformationError(f"{field} is required for a staged source row")
    return str(value).strip()


def _decimal(row: dict[str, Any], field: str) -> Decimal | None:
    value = row.get(field)
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise TransformationError(f"{field} is not numeric: {value!r}") from error


def _datetime(row: dict[str, Any]) -> datetime | None:
    value = row.get("parsed_invoice_date")
    if isinstance(value, datetime):
        return value
    value = row.get("source_date_value")
    if value is None or not str(value).strip():
        raise TransformationError("InvoiceDate is required and cannot be empty")
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as error:
        raise TransformationError(f"InvoiceDate is malformed: {value!r}") from error


def transform_staged_row(row: dict[str, Any]) -> RealRetailSalesLine:
    """Transform one staging payload without calculating revenue, net sales, or profit."""
    source_sha256 = _required_text(row, "source_sha256")
    worksheet_name = _required_text(row, "worksheet_name")
    staging_row_id = row.get("staging_row_id")
    if staging_row_id is not None and (
        not isinstance(staging_row_id, int) or staging_row_id < 1
    ):
        raise TransformationError("staging_row_id must be a positive integer when provided")
    worksheet_row_number = row.get("worksheet_row_number")
    if not isinstance(worksheet_row_number, int) or worksheet_row_number < 2:
        raise TransformationError("worksheet_row_number must be an integer >= 2")

    invoice = normalize_source_key(row.get("source_invoice_value"))
    product_key = normalize_source_key(row.get("source_stock_code"))
    customer_key = normalize_source_key(row.get("original_customer_value"))
    try:
        quantity = _decimal(row, "original_quantity")
    except TransformationError as error:
        raise TransformationError(str(error).replace("original_quantity", "Quantity", 1)) from error
    try:
        price = _decimal(row, "original_price")
    except TransformationError as error:
        raise TransformationError(str(error).replace("original_price", "Price", 1)) from error
    if quantity is None:
        raise TransformationError("Quantity is required and must be numeric")
    if price is None:
        raise TransformationError("Price is required and must be numeric")

    description = row.get("original_description")
    description = str(description).strip() if description is not None else None
    if description == "":
        description = None
    country = row.get("original_country")
    country = str(country).strip() if country is not None else None
    invoice_datetime = _datetime(row)

    return RealRetailSalesLine(
        staging_row_id=staging_row_id,
        source_sha256=source_sha256,
        worksheet_name=worksheet_name,
        worksheet_row_number=worksheet_row_number,
        source_invoice=invoice,
        invoice_datetime=invoice_datetime,
        source_product_key=product_key,
        source_customer_key=customer_key,
        source_description=description,
        source_country=country,
        signed_quantity=quantity,
        source_price=price,
        currency_code=CURRENCY_UNKNOWN,
        is_cancellation_candidate=bool(invoice and invoice.startswith("C")),
        is_negative_quantity_candidate=quantity < 0,
        is_zero_price=price == 0,
        is_negative_price=price < 0,
        is_missing_customer=customer_key is None,
        is_missing_description=description is None,
        is_possible_duplicate=bool(row.get("is_possible_duplicate_content", False)),
        source_content_hash=row.get("source_content_hash"),
    )


def source_line_identity(record: RealRetailSalesLine) -> tuple[str, str, int]:
    """Return the exact identity used by the isolated sales-line uniqueness constraint."""
    return (
        record.source_sha256,
        record.worksheet_name,
        record.worksheet_row_number,
    )


def source_content_hash(values: dict[str, Any]) -> str:
    """Provide a stable hash for a source row when staging does not supply one."""
    ordered = (
        "source_invoice_value",
        "source_stock_code",
        "original_description",
        "source_date_value",
        "original_quantity",
        "original_price",
        "original_customer_value",
        "original_country",
    )
    raw = "\x1f".join("" if values.get(key) is None else str(values[key]) for key in ordered)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
