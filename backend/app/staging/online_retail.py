"""Read, validate, and optionally stage Online Retail II source rows.

This module never writes to production warehouse tables. Database writes are
isolated to the ``stg_online_retail_*`` tables and require an explicit caller
choice to use ``--write`` in the CLI.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Protocol

import openpyxl
from sqlalchemy import text
from sqlalchemy.engine import Engine

SOURCE_COLUMNS = (
    "Invoice",
    "StockCode",
    "Description",
    "Quantity",
    "InvoiceDate",
    "Price",
    "Customer ID",
    "Country",
)
STAGING_DDL = """
CREATE TABLE IF NOT EXISTS stg_online_retail_loads (
    load_id BIGSERIAL PRIMARY KEY,
    source_filename TEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL,
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    rows_read BIGINT NOT NULL DEFAULT 0,
    rows_staged BIGINT NOT NULL DEFAULT 0,
    error_count BIGINT NOT NULL DEFAULT 0,
    report_path TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS stg_online_retail_source_rows (
    staging_row_id BIGSERIAL PRIMARY KEY,
    load_id BIGINT NOT NULL REFERENCES stg_online_retail_loads(load_id),
    source_filename TEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    worksheet_name TEXT NOT NULL,
    worksheet_row_number INTEGER NOT NULL,
    source_invoice_value TEXT,
    source_stock_code TEXT,
    source_date_value TEXT,
    original_quantity NUMERIC(18, 6),
    original_price NUMERIC(18, 6),
    original_customer_value TEXT,
    original_country TEXT,
    original_description TEXT,
    parsed_invoice_date TIMESTAMP,
    is_cancellation_invoice BOOLEAN NOT NULL,
    is_potential_return BOOLEAN NOT NULL,
    is_negative_price_anomaly BOOLEAN NOT NULL,
    is_zero_price_line BOOLEAN NOT NULL,
    is_missing_customer_id BOOLEAN NOT NULL,
    is_missing_description BOOLEAN NOT NULL,
    is_possible_duplicate_content BOOLEAN NOT NULL,
    quality_classifications JSONB NOT NULL,
    source_content_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_stg_online_retail_source_line
        UNIQUE (source_sha256, worksheet_name, worksheet_row_number)
);

CREATE INDEX IF NOT EXISTS idx_stg_online_retail_load
    ON stg_online_retail_source_rows(load_id);
CREATE INDEX IF NOT EXISTS idx_stg_online_retail_invoice
    ON stg_online_retail_source_rows(source_invoice_value);
"""


class StagingRepository(Protocol):
    def start_load(self, metadata: dict[str, Any]) -> int: ...

    def insert_rows(self, rows: list[dict[str, Any]]) -> int: ...

    def finish_load(self, load_id: int, values: dict[str, Any]) -> None: ...


@dataclass
class ValidationSummary:
    source_filename: str
    source_sha256: str
    rows_read: int = 0
    rows_staged: int = 0
    rejected_rows: int = 0
    error_messages: list[str] = field(default_factory=list)
    by_sheet: Counter[str] = field(default_factory=Counter)
    missing: Counter[str] = field(default_factory=Counter)
    distinct_invoices: set[str] = field(default_factory=set)
    distinct_stock_codes: set[str] = field(default_factory=set)
    cancellation_rows: int = 0
    potential_return_rows: int = 0
    negative_price_rows: int = 0
    zero_price_rows: int = 0
    duplicate_content_rows: int = 0
    quantity_totals: Counter[str] = field(default_factory=Counter)
    monetary_totals: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_filename": self.source_filename,
            "source_sha256": self.source_sha256,
            "rows_read": self.rows_read,
            "rows_staged": self.rows_staged,
            "rejected_rows": self.rejected_rows,
            "error_count": len(self.error_messages),
            "by_sheet": dict(self.by_sheet),
            "missing": dict(self.missing),
            "distinct_invoices": len(self.distinct_invoices),
            "distinct_stock_codes": len(self.distinct_stock_codes),
            "cancellation_rows": self.cancellation_rows,
            "potential_return_rows": self.potential_return_rows,
            "negative_price_rows": self.negative_price_rows,
            "zero_price_rows": self.zero_price_rows,
            "duplicate_content_rows": self.duplicate_content_rows,
            "quantity_totals": {key: str(value) for key, value in self.quantity_totals.items()},
            "monetary_totals": {key: str(value) for key, value in self.monetary_totals.items()},
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _source_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _content_hash(values: dict[str, Any]) -> str:
    content = "\x1f".join(
        "" if values.get(key) is None else str(values[key])
        for key in SOURCE_COLUMNS
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _row_from_values(
    values: dict[str, Any],
    *,
    filename: str,
    checksum: str,
    sheet: str,
    worksheet_row_number: int,
    duplicate: bool,
) -> dict[str, Any]:
    invoice = _text(values["Invoice"])
    stock_code = _text(values["StockCode"])
    description = _text(values["Description"])
    customer = _text(values["Customer ID"])
    quantity = _decimal(values["Quantity"])
    price = _decimal(values["Price"])
    cancellation = bool(invoice and invoice.upper().startswith("C"))
    potential_return = bool(quantity is not None and quantity < 0)
    negative_price = bool(price is not None and price < 0)
    zero_price = bool(price is not None and price == 0)
    classifications: list[str] = []
    if cancellation:
        classifications.append("cancellation_invoice")
    if potential_return:
        classifications.append("potential_return")
    if negative_price:
        classifications.append("negative_price_anomaly")
    if zero_price:
        classifications.append("zero_price_line")
    if customer is None:
        classifications.append("missing_customer_id")
    if description is None:
        classifications.append("missing_description")
    if duplicate:
        classifications.append("possible_duplicate_content")
    parsed_date = values["InvoiceDate"] if isinstance(values["InvoiceDate"], datetime) else None
    return {
        "source_filename": filename,
        "source_sha256": checksum,
        "worksheet_name": sheet,
        "worksheet_row_number": worksheet_row_number,
        "source_invoice_value": invoice,
        "source_stock_code": stock_code,
        "source_date_value": _source_date(values["InvoiceDate"]),
        "original_quantity": quantity,
        "original_price": price,
        "original_customer_value": customer,
        "original_country": _text(values["Country"]),
        "original_description": description,
        "parsed_invoice_date": parsed_date,
        "is_cancellation_invoice": cancellation,
        "is_potential_return": potential_return,
        "is_negative_price_anomaly": negative_price,
        "is_zero_price_line": zero_price,
        "is_missing_customer_id": customer is None,
        "is_missing_description": description is None,
        "is_possible_duplicate_content": duplicate,
        "quality_classifications": json.dumps(classifications),
        "source_content_hash": _content_hash(values),
    }


def _iter_source_rows(path: Path) -> Iterable[tuple[str, int, dict[str, Any]]]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for worksheet in workbook.worksheets:
            rows = worksheet.iter_rows(values_only=True)
            header = tuple(next(rows))
            if header != SOURCE_COLUMNS:
                continue
            for row_number, row in enumerate(rows, start=2):
                yield worksheet.title, row_number, dict(zip(SOURCE_COLUMNS, row))
    finally:
        workbook.close()


def _validate_headers(path: Path) -> list[str]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        transaction_sheets = []
        for worksheet in workbook.worksheets:
            header = tuple(next(worksheet.iter_rows(values_only=True)))
            if header == SOURCE_COLUMNS:
                transaction_sheets.append(worksheet.title)
        if not transaction_sheets:
            raise ValueError(
                "No worksheet contains the exact Online Retail II columns: "
                + ", ".join(SOURCE_COLUMNS)
            )
        return transaction_sheets
    finally:
        workbook.close()


def _content_counts(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for _, _, values in _iter_source_rows(path):
        counts[_content_hash(values)] += 1
    return counts


def _update_summary(summary: ValidationSummary, row: dict[str, Any]) -> None:
    summary.by_sheet[row["worksheet_name"]] += 1
    invoice = row["source_invoice_value"]
    stock_code = row["source_stock_code"]
    if invoice is None:
        summary.missing["Invoice"] += 1
    else:
        summary.distinct_invoices.add(invoice)
    if stock_code is None:
        summary.missing["StockCode"] += 1
    else:
        summary.distinct_stock_codes.add(stock_code)
    for field, label in (
        ("original_description", "Description"),
        ("original_customer_value", "Customer ID"),
        ("original_quantity", "Quantity"),
        ("original_price", "Price"),
        ("source_date_value", "InvoiceDate"),
        ("original_country", "Country"),
    ):
        if row[field] is None:
            summary.missing[label] += 1
    quantity = row["original_quantity"]
    price = row["original_price"]
    amount = (quantity * price) if quantity is not None and price is not None else Decimal(0)
    if row["is_cancellation_invoice"]:
        summary.cancellation_rows += 1
    if row["is_potential_return"]:
        summary.potential_return_rows += 1
    if row["is_negative_price_anomaly"]:
        summary.negative_price_rows += 1
    if row["is_zero_price_line"]:
        summary.zero_price_rows += 1
    if row["is_possible_duplicate_content"]:
        summary.duplicate_content_rows += 1
    category = "all_rows"
    if row["is_cancellation_invoice"]:
        category = "cancellation_invoice"
    elif row["is_potential_return"]:
        category = "potential_return"
    elif row["is_negative_price_anomaly"]:
        category = "negative_price_anomaly"
    elif row["is_zero_price_line"]:
        category = "zero_price_line"
    else:
        category = "positive_nonzero_price"
    summary.quantity_totals[category] += quantity or Decimal(0)
    summary.monetary_totals[category] += amount
    summary.quantity_totals["all_rows"] += quantity or Decimal(0)
    summary.monetary_totals["all_rows"] += amount


def _scan_workbook(
    path: Path,
    summary: ValidationSummary,
    counts: Counter[str],
    row_handler: Any = None,
) -> None:
    for sheet, row_number, values in _iter_source_rows(path):
        summary.rows_read += 1
        row = _row_from_values(
            values,
            filename=summary.source_filename,
            checksum=summary.source_sha256,
            sheet=sheet,
            worksheet_row_number=row_number,
            duplicate=counts[_content_hash(values)] > 1,
        )
        _update_summary(summary, row)
        if row_handler is not None:
            row_handler(row)


def validate_workbook(path: str | Path) -> tuple[ValidationSummary, list[dict[str, Any]]]:
    """Read all transaction sheets and return a summary plus row payloads.

    This convenience function is intended for small fixtures and callers that
    explicitly need row payloads. The CLI uses the streaming scanner below so
    the full workbook is not held in memory.
    """
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {source_path}")
    sheets = _validate_headers(source_path)
    checksum = sha256_file(source_path)
    summary = ValidationSummary(source_path.name, checksum)
    counts = _content_counts(source_path)
    payloads: list[dict[str, Any]] = []
    _scan_workbook(source_path, summary, counts, payloads.append)
    return summary, payloads


def create_staging_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        for statement in STAGING_DDL.split(";"):
            if statement.strip():
                connection.execute(text(statement))


class SqlAlchemyStagingRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def start_load(self, metadata: dict[str, Any]) -> int:
        with self.engine.begin() as connection:
            return int(connection.execute(text("""
                INSERT INTO stg_online_retail_loads
                    (source_filename, source_sha256, status, dry_run, report_path)
                VALUES
                    (:source_filename, :source_sha256, 'running', :dry_run, :report_path)
                RETURNING load_id
            """), metadata).scalar_one())

    def insert_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        columns = list(rows[0])
        statement = text(f"""
            INSERT INTO stg_online_retail_source_rows
                ({', '.join(columns)})
            VALUES
                ({', '.join(':' + column for column in columns)})
            ON CONFLICT (source_sha256, worksheet_name, worksheet_row_number)
            DO NOTHING
        """)
        with self.engine.begin() as connection:
            inserted = 0
            for row in rows:
                result = connection.execute(statement, row)
                inserted += result.rowcount or 0
            return inserted

    def finish_load(self, load_id: int, values: dict[str, Any]) -> None:
        values = {**values, "load_id": load_id}
        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE stg_online_retail_loads
                SET completed_at = CURRENT_TIMESTAMP,
                    status = :status,
                    rows_read = :rows_read,
                    rows_staged = :rows_staged,
                    error_count = :error_count,
                    error_message = :error_message
                WHERE load_id = :load_id
            """), values)


def stage_workbook(
    path: str | Path,
    *,
    repository: StagingRepository | None = None,
    write: bool = False,
    report_path: str | Path | None = None,
) -> tuple[ValidationSummary, int | None]:
    """Validate and optionally write one workbook to isolated staging tables."""
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {source_path}")
    _validate_headers(source_path)
    summary = ValidationSummary(source_path.name, sha256_file(source_path))
    counts = _content_counts(source_path)
    load_id: int | None = None
    if write:
        if repository is None:
            raise ValueError("A staging repository is required when write=True.")
        load_id = repository.start_load({
            "source_filename": summary.source_filename,
            "source_sha256": summary.source_sha256,
            "dry_run": False,
            "report_path": str(report_path) if report_path else None,
        })
        pending: list[dict[str, Any]] = []

        def stage_batch(row: dict[str, Any]) -> None:
            row["load_id"] = load_id
            pending.append(row)
            if len(pending) >= 2000:
                summary.rows_staged += repository.insert_rows(pending)
                pending.clear()

        try:
            _scan_workbook(source_path, summary, counts, stage_batch)
            if pending:
                summary.rows_staged += repository.insert_rows(pending)
            repository.finish_load(load_id, {
                "status": "completed",
                "rows_read": summary.rows_read,
                "rows_staged": summary.rows_staged,
                "error_count": summary.rejected_rows,
                "error_message": None,
            })
        except Exception as error:
            summary.error_messages.append(str(error))
            try:
                repository.finish_load(load_id, {
                    "status": "failed",
                    "rows_read": summary.rows_read,
                    "rows_staged": summary.rows_staged,
                    "error_count": summary.rejected_rows + 1,
                    "error_message": str(error)[:1000],
                })
            finally:
                raise
    else:
        _scan_workbook(source_path, summary, counts)
    return summary, load_id


def render_validation_report(summary: ValidationSummary, *, load_id: int | None, write: bool) -> str:
    data = summary.as_dict()
    lines = [
        "# Online Retail II staging validation report",
        "",
        "This report contains aggregate diagnostics only. Raw customer values and customer IDs are not included.",
        "",
        "## Load metadata",
        "",
        f"- Mode: {'staging write' if write else 'validation-only dry run'}",
        f"- Load ID: {load_id if load_id is not None else 'not assigned (dry run)'}",
        f"- Source filename: `{data['source_filename']}`",
        f"- Source SHA-256: `{data['source_sha256']}`",
        f"- Currency: unconfirmed; source monetary values were not converted",
        "",
        "## Counts",
        "",
        f"- Rows read: {data['rows_read']:,}",
        f"- Rows staged: {data['rows_staged']:,}",
        f"- Rejected/error rows: {data['rejected_rows']:,}",
        f"- Distinct invoices: {data['distinct_invoices']:,}",
        f"- Distinct stock codes: {data['distinct_stock_codes']:,}",
        f"- Possible duplicate-content rows: {data['duplicate_content_rows']:,}",
        f"- Cancellation-invoice rows: {data['cancellation_rows']:,}",
        f"- Potential-return rows (negative quantity): {data['potential_return_rows']:,}",
        f"- Negative-price rows: {data['negative_price_rows']:,}",
        f"- Zero-price rows: {data['zero_price_rows']:,}",
        "",
        "## Rows by worksheet",
        "",
        *[f"- `{sheet}`: {count:,}" for sheet, count in data["by_sheet"].items()],
        "",
        "## Missing values",
        "",
        *[f"- `{column}`: {count:,}" for column, count in data["missing"].items()],
        "",
        "## Quantity and monetary totals",
        "",
        "Monetary totals are `Quantity × Price` in GBP, as specified by the UCI Online Retail II documentation for `UnitPrice`; no conversion is applied (https://archive.ics.uci.edu/dataset/502/online+retail+ii).",
        "The inclusion categories are mutually exclusive for reporting, using precedence: cancellation invoice,",
        "potential return, negative-price anomaly, zero-price line, then positive nonzero price.",
        "",
        "| Inclusion category | Quantity total | Monetary total |",
        "|---|---:|---:|",
    ]
    categories = sorted(set(data["quantity_totals"]) | set(data["monetary_totals"]))
    lines.extend(
        f"| `{category}` | {data['quantity_totals'].get(category, '0')} | "
        f"{data['monetary_totals'].get(category, '0')} |"
        for category in categories
    )
    lines.extend([
        "",
        "## Limitations",
        "",
        "- Negative quantities are classified as potential returns, not confirmed returns.",
        "- Cancellation invoices and potential returns are retained in staging.",
        "- Product cost, profit, store, customer identity, and currency conversion are not created.",
        "- Staging rows are not production `dim_*` or `fact_sales` rows.",
    ])
    return "\n".join(lines) + "\n"
