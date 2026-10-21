"""Pure validation for future effective-dated product unit-cost records."""

from __future__ import annotations

import csv
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from io import StringIO
import re
from typing import Any

from app.transformations.online_retail import normalize_source_key

CSV_FIELDS = (
    "stock_code",
    "unit_cost",
    "currency_code",
    "effective_from",
    "effective_to",
    "source_reference",
)

# ISO 4217 alphabetic currency codes; XXX is deliberately excluded because it
# denotes no currency rather than an explicit monetary denomination.
ISO_4217_CURRENCY_CODES = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB
BRL BSD BTN BOV BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP
CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ
GYD HKD HNL HRK HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF
KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU
MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR
PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN
SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYU UYI UYW
UZS VES VND VUV WST XAF XAG XAU XBA XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT
XSU XTS XUA YER ZAR ZMW ZWL
""".split())

_DECIMAL_PATTERN = re.compile(r"^\+?(?:\d+(?:\.\d*)?|\.\d+)$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ProductCostValidationError(ValueError):
    """A cost record is incomplete, malformed, or conflicts with another record."""


@dataclass(frozen=True)
class ProductUnitCost:
    stock_code: str
    unit_cost: Decimal
    currency_code: str
    effective_from: date
    effective_to: date | None
    source_reference: str


@dataclass(frozen=True)
class RejectedProductCostRow:
    row_number: int
    stock_code: str | None
    reason: str


@dataclass(frozen=True)
class ProductCostCsvReport:
    accepted: tuple[tuple[int, ProductUnitCost], ...]
    rejected: tuple[RejectedProductCostRow, ...]
    warnings: tuple[str, ...] = ()


def _required_text(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if value is None or not str(value).strip():
        raise ProductCostValidationError(f"{field} is required")
    return str(value).strip()


def _cost_decimal(value: Any) -> Decimal:
    if isinstance(value, float):
        raise ProductCostValidationError(
            "unit_cost must be supplied as an exact decimal string, not a float"
        )
    text = str(value).strip() if value is not None else ""
    if not text or not _DECIMAL_PATTERN.fullmatch(text):
        raise ProductCostValidationError(
            "unit_cost must be a non-negative decimal without exponent notation"
        )
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise ProductCostValidationError("unit_cost is not a valid decimal") from error
    if not amount.is_finite() or amount < 0:
        raise ProductCostValidationError("unit_cost must be non-negative")
    return amount


def _iso_date(value: Any, field: str, *, optional: bool = False) -> date | None:
    if value is None or not str(value).strip():
        if optional:
            return None
        raise ProductCostValidationError(f"{field} is required")
    text = str(value).strip()
    if not _DATE_PATTERN.fullmatch(text):
        raise ProductCostValidationError(f"{field} must use YYYY-MM-DD format")
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise ProductCostValidationError(f"{field} is not a valid calendar date") from error


def validate_product_cost_record(
    record: Mapping[str, Any],
    known_product_keys: Collection[str],
) -> ProductUnitCost:
    """Validate and normalize one header-mapped CSV record without I/O."""
    missing_headers = set(CSV_FIELDS) - record.keys()
    if missing_headers:
        raise ProductCostValidationError(
            f"Missing required field(s): {', '.join(sorted(missing_headers))}"
        )

    raw_stock_code = _required_text(record, "stock_code")
    stock_code = normalize_source_key(raw_stock_code)
    known_keys = {normalize_source_key(key) for key in known_product_keys}
    if stock_code is None or stock_code not in known_keys:
        raise ProductCostValidationError(
            f"stock_code {raw_stock_code!r} does not match a real-retail product identifier"
        )

    unit_cost = _cost_decimal(record.get("unit_cost"))
    fixed_text = format(unit_cost, "f")
    integral, _, fractional = fixed_text.partition(".")
    integral_digits = len(integral.lstrip("0")) or 1
    fractional_digits = len(fractional.rstrip("0"))
    if integral_digits > 12 or fractional_digits > 6:
        raise ProductCostValidationError(
            "unit_cost exceeds the target NUMERIC(18, 6) precision"
        )
    currency_code = _required_text(record, "currency_code").upper()
    if currency_code not in ISO_4217_CURRENCY_CODES:
        raise ProductCostValidationError(
            "currency_code must be a recognized ISO 4217 alphabetic currency code"
        )
    effective_from = _iso_date(record.get("effective_from"), "effective_from")
    effective_to = _iso_date(record.get("effective_to"), "effective_to", optional=True)
    if effective_from is None:
        raise ProductCostValidationError("effective_from is required")
    if effective_to is not None and effective_to < effective_from:
        raise ProductCostValidationError(
            "effective_to must not precede effective_from"
        )
    source_reference = _required_text(record, "source_reference")
    return ProductUnitCost(
        stock_code=stock_code,
        unit_cost=unit_cost,
        currency_code=currency_code,
        effective_from=effective_from,
        effective_to=effective_to,
        source_reference=source_reference,
    )


def _period_overlaps(
    left: ProductUnitCost,
    right: ProductUnitCost,
) -> bool:
    return (
        left.stock_code == right.stock_code
        and left.currency_code == right.currency_code
        and (left.effective_to is None or left.effective_to >= right.effective_from)
        and (right.effective_to is None or right.effective_to >= left.effective_from)
    )


def validate_product_cost_csv(
    csv_text: str,
    known_product_keys: Collection[str],
    *,
    existing_costs: Sequence[ProductUnitCost] = (),
    currency_mismatch_policy: str,
    source_currency: str = "GBP",
) -> ProductCostCsvReport:
    """Parse and validate a CSV, retaining accepted and row-numbered rejected records."""
    if currency_mismatch_policy not in {"reject", "keep-separate"}:
        raise ValueError("currency_mismatch_policy must be 'reject' or 'keep-separate'")
    reader = csv.DictReader(StringIO(csv_text, newline=""), strict=True)
    if reader.fieldnames is None:
        raise ProductCostValidationError("CSV header is missing")
    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ProductCostValidationError("CSV header contains duplicate column names")
    missing_headers = set(CSV_FIELDS) - set(reader.fieldnames)
    unexpected_headers = set(reader.fieldnames) - set(CSV_FIELDS)
    if missing_headers or unexpected_headers:
        details = []
        if missing_headers:
            details.append(f"missing: {', '.join(sorted(missing_headers))}")
        if unexpected_headers:
            details.append(f"unexpected: {', '.join(sorted(unexpected_headers))}")
        raise ProductCostValidationError("Invalid CSV header (" + "; ".join(details) + ")")

    normalized_source_currency = source_currency.upper()
    accepted: list[tuple[int, ProductUnitCost]] = []
    rejected: list[RejectedProductCostRow] = []
    warnings: list[str] = []
    seen_records: list[tuple[int, ProductUnitCost]] = []
    for row_number, raw_record in enumerate(reader, start=2):
        raw_stock_code = raw_record.get("stock_code")
        stock_code = raw_stock_code.strip() if raw_stock_code else None
        if None in raw_record:
            rejected.append(
                RejectedProductCostRow(row_number, stock_code, "row has more fields than the CSV header")
            )
            continue
        try:
            record = validate_product_cost_record(raw_record, known_product_keys)
            if record.currency_code != normalized_source_currency:
                if currency_mismatch_policy == "reject":
                    raise ProductCostValidationError(
                        f"currency {record.currency_code} differs from source currency "
                        f"{normalized_source_currency}; no conversion is performed"
                    )
                warnings.append(
                    f"Row {row_number}: {record.currency_code} cost retained separately "
                    f"from {normalized_source_currency} sales; no conversion is performed."
                )
            prior_periods = [
                ("an existing warehouse record", prior)
                for prior in existing_costs
                if prior.stock_code == record.stock_code
                and prior.currency_code == record.currency_code
                and _period_overlaps(prior, record)
            ]
            prior_periods.extend(
                (f"CSV row {prior_row}", prior)
                for prior_row, prior in seen_records
                if prior.stock_code == record.stock_code
                and prior.currency_code == record.currency_code
                and _period_overlaps(prior, record)
            )
            if prior_periods:
                prior_label, prior = prior_periods[0]
                if prior.unit_cost == record.unit_cost and (
                    prior.effective_from == record.effective_from
                    and prior.effective_to == record.effective_to
                ):
                    reason = (
                        "duplicate product/currency/effective-period record "
                        f"already exists in {prior_label}"
                    )
                else:
                    reason = (
                        "overlapping effective period for product/currency "
                        f"(conflicts with {prior_label})"
                    )
                raise ProductCostValidationError(reason)
            seen_records.append((row_number, record))
            accepted.append((row_number, record))
        except ProductCostValidationError as error:
            rejected.append(
                RejectedProductCostRow(row_number, stock_code, str(error))
            )
    if reader.line_num < 1:
        raise ProductCostValidationError("CSV could not be parsed")
    return ProductCostCsvReport(
        accepted=tuple(accepted),
        rejected=tuple(rejected),
        warnings=tuple(warnings),
    )


def validate_product_cost_records(
    records: Sequence[Mapping[str, Any]],
    known_product_keys: Collection[str],
) -> list[ProductUnitCost]:
    """Validate records and reject conflicting overlapping product/currency periods."""
    validated = [
        validate_product_cost_record(record, known_product_keys)
        for record in records
    ]
    ordered = sorted(
        enumerate(validated, start=1),
        key=lambda item: (
            item[1].stock_code,
            item[1].currency_code,
            item[1].effective_from,
        ),
    )
    active: list[tuple[int, ProductUnitCost]] = []
    for row_number, current in ordered:
        active = [
            (prior_row, prior)
            for prior_row, prior in active
            if prior.stock_code == current.stock_code
            and prior.currency_code == current.currency_code
            and (prior.effective_to is None or prior.effective_to >= current.effective_from)
        ]
        for prior_row, prior in active:
            if prior.unit_cost != current.unit_cost:
                raise ProductCostValidationError(
                    "Conflicting overlapping effective periods for "
                    f"{current.stock_code}/{current.currency_code} "
                    f"(CSV rows {prior_row} and {row_number})"
                )
        active.append((row_number, current))
    return validated
