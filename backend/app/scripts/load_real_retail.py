"""Load one completed Online Retail staging load into disposable real_retail."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, URL, make_url

from app.core.config import settings
from app.staging.online_retail import STAGING_DDL
from app.transformations.online_retail import (
    CURRENCY_UNKNOWN,
    POLICY_VERSION,
    transform_staged_row,
)
from app.transformations.reconciliation import RowSummary, reconcile_stream

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REAL_DDL_PATH = PROJECT_ROOT / "database" / "real_retail_schema.sql"
SOURCE_FILENAME = "online_retail_II.xlsx"
DESTINATION_DATABASE = "retail_sales_test_disposable"
DESTINATION_ENV = "REAL_RETAIL_TEST_DATABASE_URL"
BATCH_SIZE = 2000

SOURCE_COLUMNS = (
    "staging_row_id",
    "load_id",
    "source_filename",
    "source_sha256",
    "worksheet_name",
    "worksheet_row_number",
    "source_invoice_value",
    "source_stock_code",
    "source_date_value",
    "original_quantity",
    "original_price",
    "original_customer_value",
    "original_country",
    "original_description",
    "parsed_invoice_date",
    "is_cancellation_invoice",
    "is_potential_return",
    "is_negative_price_anomaly",
    "is_zero_price_line",
    "is_missing_customer_id",
    "is_missing_description",
    "is_possible_duplicate_content",
    "quality_classifications",
    "source_content_hash",
    "created_at",
)


def split_sql_statements(sql: str) -> list[str]:
    """Split SQL outside comments and quoted or dollar-quoted text."""
    statements: list[str] = []
    start = 0
    index = 0
    quote: str | None = None
    dollar_tag: str | None = None
    while index < len(sql):
        if dollar_tag:
            if sql.startswith(dollar_tag, index):
                index += len(dollar_tag)
                dollar_tag = None
            else:
                index += 1
            continue
        if quote:
            if sql[index] == "\\" and quote == "'":
                index += 2
            elif sql[index] == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                else:
                    quote = None
                    index += 1
            else:
                index += 1
            continue
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            comment_end = len(sql) if end == -1 else end + 1
            if not sql[start:index].strip():
                start = comment_end
            index = comment_end
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end == -1:
                raise ValueError("Unterminated SQL block comment")
            comment_end = end + 2
            if not sql[start:index].strip():
                start = comment_end
            index = comment_end
            continue
        if sql[index] in {"'", '"'}:
            quote = sql[index]
            index += 1
            continue
        if sql[index] == "$":
            end = sql.find("$", index + 1)
            if end != -1:
                candidate = sql[index : end + 1]
                inner = candidate[1:-1]
                if candidate == "$$" or (
                    inner and inner[0].isalpha() and inner.replace("_", "a").isalnum()
                ):
                    dollar_tag = candidate
                    index = end + 1
                    continue
        if sql[index] == ";":
            statement = sql[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1
    if quote or dollar_tag:
        raise ValueError("Unterminated SQL quoted content")
    trailing = sql[start:].strip()
    if trailing:
        statements.append(trailing)
    return statements


def validate_destination_url(raw_url: str) -> URL:
    parsed = make_url(raw_url)
    if parsed.database != DESTINATION_DATABASE:
        raise ValueError(
            f"{DESTINATION_ENV} must target {DESTINATION_DATABASE!r}; refusing to write"
        )
    if (parsed.host or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"{DESTINATION_ENV} must target a local PostgreSQL host")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, str):
        try:
            return json.dumps(json.loads(value))
        except json.JSONDecodeError:
            try:
                return json.dumps(ast.literal_eval(value))
            except (ValueError, SyntaxError) as error:
                raise ValueError("Invalid staging quality classification JSON") from error
    raise ValueError("Unsupported staging quality classification value")


def _read_source_load(connection: Connection, load_id: int) -> dict[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT load_id, status, source_filename, source_sha256, started_at,
                   rows_read, rows_staged, error_count, completed_at
            FROM stg_online_retail_loads
            WHERE load_id = :load_id
            """
        ),
        {"load_id": load_id},
    ).mappings().one_or_none()
    if row is None:
        raise ValueError(f"Staging load {load_id} does not exist")
    if row["status"] != "completed" or row["completed_at"] is None:
        raise ValueError(f"Staging load {load_id} is not completed")
    if not row["source_sha256"]:
        raise ValueError(f"Staging load {load_id} has no source checksum")
    actual_count = connection.execute(
        text(
            "SELECT COUNT(*) FROM stg_online_retail_source_rows WHERE load_id = :load_id"
        ),
        {"load_id": load_id},
    ).scalar_one()
    if actual_count != row["rows_staged"] or actual_count != row["rows_read"]:
        raise ValueError(
            f"Staging load {load_id} count mismatch: metadata "
            f"read={row['rows_read']} staged={row['rows_staged']}, actual={actual_count}"
        )
    return dict(row)


def _source_rows(connection: Connection, load_id: int) -> Iterable[dict[str, Any]]:
    result = connection.execution_options(stream_results=True).execute(
        text(
            """
            SELECT staging_row_id, load_id, source_filename, source_sha256,
                   worksheet_name, worksheet_row_number, source_invoice_value,
                   source_stock_code, source_date_value, original_quantity,
                   original_price, original_customer_value, original_country,
                   original_description, parsed_invoice_date,
                   is_cancellation_invoice, is_potential_return,
                   is_negative_price_anomaly, is_zero_price_line,
                   is_missing_customer_id, is_missing_description,
                   is_possible_duplicate_content, quality_classifications,
                   source_content_hash, created_at
            FROM stg_online_retail_source_rows
            WHERE load_id = :load_id
            ORDER BY staging_row_id
            """
        ),
        {"load_id": load_id},
    ).mappings()
    try:
        while True:
            batch = result.fetchmany(BATCH_SIZE)
            if not batch:
                break
            yield from batch
    finally:
        result.close()


def _row_summary(record: dict[str, Any], transformed: Any | None = None) -> RowSummary:
    if transformed is None:
        return RowSummary(
            source_line_identity=(
                record["source_sha256"],
                record["worksheet_name"],
                record["worksheet_row_number"],
            ),
            signed_quantity=record["original_quantity"],
            currency_code=CURRENCY_UNKNOWN,
            is_possible_duplicate=record["is_possible_duplicate_content"],
            is_cancellation_candidate=record["is_cancellation_invoice"],
            is_negative_quantity_candidate=record["is_potential_return"],
            is_missing_customer=record["is_missing_customer_id"],
            is_missing_description=record["is_missing_description"],
        )
    return RowSummary(
        source_line_identity=(
            transformed.source_sha256,
            transformed.worksheet_name,
            transformed.worksheet_row_number,
        ),
        signed_quantity=transformed.signed_quantity,
        currency_code=transformed.currency_code,
        is_possible_duplicate=transformed.is_possible_duplicate,
        is_cancellation_candidate=transformed.is_cancellation_candidate,
        is_negative_quantity_candidate=transformed.is_negative_quantity_candidate,
        is_missing_customer=transformed.is_missing_customer,
        is_missing_description=transformed.is_missing_description,
    )


def _quality_metrics(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    metrics: Counter[str] = Counter()
    quantity_total = Decimal(0)
    monetary_total = Decimal(0)
    products: dict[str, tuple[str | None, bool]] = {}
    customers: set[str] = set()
    for record in records:
        transformed = transform_staged_row(record)
        metrics["rows"] += 1
        metrics["cancellation"] += int(transformed.is_cancellation_candidate)
        metrics["negative_quantity"] += int(transformed.is_negative_quantity_candidate)
        metrics["negative_price"] += int(transformed.is_negative_price)
        metrics["zero_price"] += int(transformed.is_zero_price)
        metrics["missing_customer"] += int(transformed.is_missing_customer)
        metrics["missing_description"] += int(transformed.is_missing_description)
        metrics["duplicate"] += int(transformed.is_possible_duplicate)
        quantity_total += transformed.signed_quantity or Decimal(0)
        monetary_total += (transformed.signed_quantity or Decimal(0)) * (
            transformed.source_price or Decimal(0)
        )
        if transformed.source_product_key is not None:
            existing = products.get(transformed.source_product_key)
            if existing is None or (existing[0] is None and transformed.source_description):
                products[transformed.source_product_key] = (
                    transformed.source_description,
                    transformed.is_missing_description,
                )
        if transformed.source_customer_key is not None:
            customers.add(transformed.source_customer_key)
    return {
        **metrics,
        "quantity_total": quantity_total,
        "monetary_total": monetary_total,
        "products": products,
        "customers": customers,
    }


def _destination_objects(connection: Connection) -> set[tuple[str, str]]:
    rows = connection.execute(
        text(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE (table_schema = 'real_retail'
                   AND table_name IN ('loads', 'source_products', 'source_customers', 'sales_lines'))
               OR (table_schema = 'public'
                   AND table_name IN ('stg_online_retail_loads', 'stg_online_retail_source_rows'))
            """
        )
    ).all()
    return {(row[0], row[1]) for row in rows}


def _apply_ddl(connection: Connection) -> None:
    for statement in split_sql_statements(STAGING_DDL):
        connection.execute(text(statement))
    for statement in split_sql_statements(REAL_DDL_PATH.read_text(encoding="utf-8")):
        connection.execute(text(statement))


def _set_sequences(connection: Connection) -> None:
    connection.execute(
        text(
            """
            SELECT setval(pg_get_serial_sequence('stg_online_retail_loads', 'load_id'),
                          COALESCE(MAX(load_id), 1), true)
            FROM stg_online_retail_loads
            """
        )
    )
    connection.execute(
        text(
            """
            SELECT setval(pg_get_serial_sequence('stg_online_retail_source_rows', 'staging_row_id'),
                          COALESCE(MAX(staging_row_id), 1), true)
            FROM stg_online_retail_source_rows
            """
        )
    )
    connection.execute(
        text(
            """
            SELECT setval(pg_get_serial_sequence('real_retail.loads', 'real_load_id'),
                          COALESCE(MAX(real_load_id), 1), true)
            FROM real_retail.loads
            """
        )
    )
    connection.execute(
        text(
            """
            SELECT setval(pg_get_serial_sequence('real_retail.sales_lines', 'real_sales_line_id'),
                          COALESCE(MAX(real_sales_line_id), 1), true)
            FROM real_retail.sales_lines
            """
        )
    )


def _db_checks(
    connection: Connection,
    source: dict[str, Any],
    source_metrics: dict[str, Any],
    real_load_id: int,
) -> list[str]:
    failures: list[str] = []
    aggregate = connection.execute(
        text(
            """
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT (source_sha256, worksheet_name, worksheet_row_number)) AS identities,
                   COALESCE(SUM(signed_quantity), 0) AS quantity_total,
                   COALESCE(SUM(signed_quantity * source_price), 0) AS monetary_total,
                   COUNT(*) FILTER (WHERE is_cancellation_candidate) AS cancellation,
                   COUNT(*) FILTER (WHERE is_negative_quantity_candidate) AS negative_quantity,
                   COUNT(*) FILTER (WHERE is_negative_price) AS negative_price,
                   COUNT(*) FILTER (WHERE is_zero_price) AS zero_price,
                   COUNT(*) FILTER (WHERE is_missing_customer) AS missing_customer,
                   COUNT(*) FILTER (WHERE is_missing_description) AS missing_description,
                   COUNT(*) FILTER (WHERE is_possible_duplicate) AS duplicate,
                   COUNT(*) FILTER (WHERE currency_code <> 'UNKNOWN') AS non_unknown_currency,
                   COUNT(*) FILTER (WHERE source_product_key IS NOT NULL
                                      AND NOT EXISTS (
                                          SELECT 1 FROM real_retail.source_products p
                                          WHERE p.source_product_key = sales_lines.source_product_key
                                      )) AS invalid_products,
                   COUNT(*) FILTER (WHERE source_customer_key IS NOT NULL
                                      AND NOT EXISTS (
                                          SELECT 1 FROM real_retail.source_customers c
                                          WHERE c.source_customer_key = sales_lines.source_customer_key
                                      )) AS invalid_customers,
                   COUNT(*) FILTER (WHERE NOT EXISTS (
                                      SELECT 1 FROM real_retail.loads l
                                      WHERE l.real_load_id = sales_lines.real_load_id
                                      AND l.real_load_id = :real_load_id
                                  )) AS invalid_loads
            FROM real_retail.sales_lines
            WHERE real_load_id = :real_load_id
            """
        ),
        {"real_load_id": real_load_id},
    ).mappings().one()
    expected = {
        "rows": source["rows_read"],
        "identities": source["rows_read"],
        "quantity_total": source_metrics["quantity_total"],
        "monetary_total": source_metrics["monetary_total"],
        "cancellation": source_metrics["cancellation"],
        "negative_quantity": source_metrics["negative_quantity"],
        "negative_price": source_metrics["negative_price"],
        "zero_price": source_metrics["zero_price"],
        "missing_customer": source_metrics["missing_customer"],
        "missing_description": source_metrics["missing_description"],
        "duplicate": source_metrics["duplicate"],
        "non_unknown_currency": 0,
        "invalid_products": 0,
        "invalid_customers": 0,
        "invalid_loads": 0,
    }
    for key, value in expected.items():
        if aggregate[key] != value:
            failures.append(f"{key}: expected {value!r}, observed {aggregate[key]!r}")
    missing = connection.execute(
        text(
            """
            SELECT COUNT(*) FROM stg_online_retail_source_rows s
            WHERE s.load_id = :staging_load_id
              AND NOT EXISTS (
                SELECT 1 FROM real_retail.sales_lines r
                WHERE r.real_load_id = :real_load_id
                  AND r.source_sha256 = s.source_sha256
                  AND r.worksheet_name = s.worksheet_name
                  AND r.worksheet_row_number = s.worksheet_row_number
              )
            """
        ),
        {"staging_load_id": source["load_id"], "real_load_id": real_load_id},
    ).scalar_one()
    extra = connection.execute(
        text(
            """
            SELECT COUNT(*) FROM real_retail.sales_lines r
            WHERE r.real_load_id = :real_load_id
              AND NOT EXISTS (
                SELECT 1 FROM stg_online_retail_source_rows s
                WHERE s.load_id = :staging_load_id
                  AND s.source_sha256 = r.source_sha256
                  AND s.worksheet_name = r.worksheet_name
                  AND s.worksheet_row_number = r.worksheet_row_number
              )
            """
        ),
        {"staging_load_id": source["load_id"], "real_load_id": real_load_id},
    ).scalar_one()
    if missing or extra:
        failures.append(f"source-line identity differences: missing={missing}, extra={extra}")
    return failures


def _reconcile_existing(
    connection: Connection,
    source: dict[str, Any],
    source_metrics: dict[str, Any],
) -> dict[str, Any]:
    existing = connection.execute(
        text(
            """
            SELECT real_load_id, status, rows_read, rows_loaded, rows_rejected,
                   completed_at
            FROM real_retail.loads
            WHERE source_sha256 = :source_sha256
              AND transformation_policy_version = :policy
            ORDER BY real_load_id DESC
            LIMIT 1
            """
        ),
        {"source_sha256": source["source_sha256"], "policy": POLICY_VERSION},
    ).mappings().one_or_none()
    if existing is None:
        raise ValueError("Destination contains existing objects but no matching completed load")
    if existing["status"] != "completed":
        raise ValueError(f"Existing destination load {existing['real_load_id']} is not completed")
    count = connection.execute(
        text("SELECT COUNT(*) FROM real_retail.sales_lines WHERE real_load_id = :id"),
        {"id": existing["real_load_id"]},
    ).scalar_one()
    if count != source["rows_read"]:
        raise ValueError(f"Existing destination load has {count} rows, expected {source['rows_read']}")
    failures = _db_checks(connection, source, source_metrics, existing["real_load_id"])
    if failures:
        raise ValueError("Existing destination reconciliation failed: " + "; ".join(failures))
    return {"mode": "rerun", "real_load_id": existing["real_load_id"], "rows": count}


def run(load_id: int, workbook: Path) -> dict[str, Any]:
    source_engine = create_engine(settings.database_url, pool_pre_ping=True)
    source_load: dict[str, Any]
    source_metrics: dict[str, Any]
    with source_engine.connect() as source_connection:
        source_transaction = source_connection.begin()
        try:
            source_connection.execute(text("SET TRANSACTION READ ONLY"))
            source_url = make_url(settings.database_url)
            if source_url.database != "retail_sales_db" or (
                source_url.host or ""
            ).lower() not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("DATABASE_URL must target local retail_sales_db for read-only verification")
            source_load = _read_source_load(source_connection, load_id)
            if source_load["source_filename"] != workbook.name:
                raise ValueError("Staging source filename does not match the workbook")
            workbook_checksum = _sha256(workbook)
            if workbook_checksum != source_load["source_sha256"]:
                raise ValueError("Workbook checksum does not match the staged checksum")
            source_metrics = _quality_metrics(_source_rows(source_connection, load_id))
        finally:
            source_transaction.rollback()
    source_engine.dispose()

    raw_destination = os.environ.get(DESTINATION_ENV)
    if not raw_destination:
        raise ValueError(f"{DESTINATION_ENV} is required; refusing to write")
    destination_url = validate_destination_url(raw_destination)
    destination_engine = create_engine(destination_url, pool_pre_ping=True)
    try:
        with destination_engine.connect() as destination_connection:
            objects = _destination_objects(destination_connection)
            if objects:
                destination_connection.rollback()
                transaction = destination_connection.begin()
                try:
                    source_engine = create_engine(settings.database_url, pool_pre_ping=True)
                    with source_engine.connect() as source_connection:
                        source_connection.execute(text("SET TRANSACTION READ ONLY"))
                        report = reconcile_stream(
                            source_rows=(
                                _row_summary(record)
                                for record in _source_rows(source_connection, load_id)
                            ),
                            transformed_rows=(
                                _row_summary(
                                    record,
                                    transform_staged_row(record),
                                )
                                for record in _source_rows(source_connection, load_id)
                            ),
                            source_row_count=source_load["rows_read"],
                            staged_row_count=source_load["rows_staged"],
                            transformed_row_count=source_load["rows_read"],
                            rejected_row_count=0,
                            target_ready_row_count=source_load["rows_read"],
                        )
                    source_engine.dispose()
                    if not report.ready:
                        failed = [check.name for check in report.checks if not check.passed]
                        raise ValueError(f"Pure reconciliation failed: {failed}")
                    result = _reconcile_existing(
                        destination_connection,
                        source_load,
                        source_metrics,
                    )
                    transaction.commit()
                    return result
                except Exception:
                    transaction.rollback()
                    raise
            destination_connection.rollback()
            transaction = destination_connection.begin()
            try:
                _apply_ddl(destination_connection)
                destination_connection.execute(
                    text(
                        """
                        INSERT INTO stg_online_retail_loads
                            (load_id, source_filename, source_sha256, started_at,
                             completed_at, status, dry_run, rows_read, rows_staged,
                             error_count)
                        VALUES (:load_id, :filename, :checksum, :started_at,
                                :completed_at, 'completed', FALSE, :rows_read,
                                :rows_staged, :error_count)
                        """
                    ),
                    {
                        "load_id": source_load["load_id"],
                        "filename": source_load["source_filename"],
                        "checksum": source_load["source_sha256"],
                        "started_at": source_load["started_at"],
                        "completed_at": source_load["completed_at"],
                        "rows_read": source_load["rows_read"],
                        "rows_staged": source_load["rows_staged"],
                        "error_count": source_load["error_count"],
                    },
                )
                source_engine = create_engine(settings.database_url, pool_pre_ping=True)
                with source_engine.connect() as source_connection:
                    source_connection.execute(text("SET TRANSACTION READ ONLY"))
                    rows = _source_rows(source_connection, load_id)
                    source_batch: list[dict[str, Any]] = []
                    products: dict[str, tuple[str | None, bool]] = {}
                    customers: set[str] = set()
                    for record in rows:
                        transformed = transform_staged_row(record)
                        record = dict(record)
                        record["quality_classifications"] = _json_value(
                            record["quality_classifications"]
                        )
                        source_batch.append({column: record[column] for column in SOURCE_COLUMNS})
                        if transformed.source_product_key:
                            existing = products.get(transformed.source_product_key)
                            if existing is None or (
                                existing[0] is None and transformed.source_description
                            ):
                                products[transformed.source_product_key] = (
                                    transformed.source_description,
                                    transformed.is_missing_description,
                                )
                        if transformed.source_customer_key:
                            customers.add(transformed.source_customer_key)
                        if len(source_batch) >= BATCH_SIZE:
                            destination_connection.execute(
                                text(
                                    f"INSERT INTO stg_online_retail_source_rows "
                                    f"({', '.join(SOURCE_COLUMNS)}) VALUES "
                                    f"({', '.join(':' + c for c in SOURCE_COLUMNS)})"
                                ),
                                source_batch,
                            )
                            source_batch.clear()
                    if source_batch:
                        destination_connection.execute(
                            text(
                                f"INSERT INTO stg_online_retail_source_rows "
                                f"({', '.join(SOURCE_COLUMNS)}) VALUES "
                                f"({', '.join(':' + c for c in SOURCE_COLUMNS)})"
                            ),
                            source_batch,
                        )
                source_engine.dispose()
                real_load_id = destination_connection.execute(
                    text(
                        """
                        INSERT INTO real_retail.loads
                            (source_filename, source_sha256, staging_load_id,
                             transformation_policy_version, currency_code, status,
                             rows_read, rows_loaded, rows_rejected)
                        VALUES (:filename, :checksum, :staging_load_id, :policy,
                                'UNKNOWN', 'running', :rows_read, 0, 0)
                        RETURNING real_load_id
                        """
                    ),
                    {
                        "filename": source_load["source_filename"],
                        "checksum": source_load["source_sha256"],
                        "staging_load_id": source_load["load_id"],
                        "policy": POLICY_VERSION,
                        "rows_read": source_load["rows_read"],
                    },
                ).scalar_one()
                for key, (description, missing) in products.items():
                    destination_connection.execute(
                        text(
                            """
                            INSERT INTO real_retail.source_products
                                (source_product_key, source_description,
                                 is_missing_description, first_seen_real_load_id,
                                 last_seen_real_load_id)
                            VALUES (:key, :description, :missing, :load_id, :load_id)
                            """
                        ),
                        {"key": key, "description": description, "missing": missing, "load_id": real_load_id},
                    )
                for key in customers:
                    destination_connection.execute(
                        text(
                            """
                            INSERT INTO real_retail.source_customers
                                (source_customer_key, first_seen_real_load_id,
                                 last_seen_real_load_id)
                            VALUES (:key, :load_id, :load_id)
                            """
                        ),
                        {"key": key, "load_id": real_load_id},
                    )
                source_engine = create_engine(settings.database_url, pool_pre_ping=True)
                with source_engine.connect() as source_connection:
                    source_connection.execute(text("SET TRANSACTION READ ONLY"))
                    batch: list[dict[str, Any]] = []
                    for record in _source_rows(source_connection, load_id):
                        transformed = transform_staged_row(record)
                        batch.append(
                            {
                                "real_load_id": real_load_id,
                                "staging_row_id": transformed.staging_row_id,
                                "source_sha256": transformed.source_sha256,
                                "worksheet_name": transformed.worksheet_name,
                                "worksheet_row_number": transformed.worksheet_row_number,
                                "source_invoice": transformed.source_invoice,
                                "invoice_datetime": transformed.invoice_datetime,
                                "source_product_key": transformed.source_product_key,
                                "source_customer_key": transformed.source_customer_key,
                                "source_description": transformed.source_description,
                                "source_country": transformed.source_country,
                                "signed_quantity": transformed.signed_quantity,
                                "source_price": transformed.source_price,
                                "currency_code": transformed.currency_code,
                                "is_cancellation_candidate": transformed.is_cancellation_candidate,
                                "is_negative_quantity_candidate": transformed.is_negative_quantity_candidate,
                                "is_zero_price": transformed.is_zero_price,
                                "is_negative_price": transformed.is_negative_price,
                                "is_missing_customer": transformed.is_missing_customer,
                                "is_missing_description": transformed.is_missing_description,
                                "is_possible_duplicate": transformed.is_possible_duplicate,
                                "source_content_hash": transformed.source_content_hash,
                            }
                        )
                        if len(batch) >= BATCH_SIZE:
                            destination_connection.execute(
                                text(
                                    """
                                    INSERT INTO real_retail.sales_lines
                                    (real_load_id, staging_row_id, source_sha256,
                                     worksheet_name, worksheet_row_number, source_invoice,
                                     invoice_datetime, source_product_key,
                                     source_customer_key, source_description,
                                     source_country, signed_quantity, source_price,
                                     currency_code, is_cancellation_candidate,
                                     is_negative_quantity_candidate, is_zero_price,
                                     is_negative_price, is_missing_customer,
                                     is_missing_description, is_possible_duplicate,
                                     source_content_hash)
                                    VALUES
                                    (:real_load_id, :staging_row_id, :source_sha256,
                                     :worksheet_name, :worksheet_row_number, :source_invoice,
                                     :invoice_datetime, :source_product_key,
                                     :source_customer_key, :source_description,
                                     :source_country, :signed_quantity, :source_price,
                                     :currency_code, :is_cancellation_candidate,
                                     :is_negative_quantity_candidate, :is_zero_price,
                                     :is_negative_price, :is_missing_customer,
                                     :is_missing_description, :is_possible_duplicate,
                                     :source_content_hash)
                                    """
                                ),
                                batch,
                            )
                            batch.clear()
                    if batch:
                        destination_connection.execute(
                            text(
                                """
                                INSERT INTO real_retail.sales_lines
                                (real_load_id, staging_row_id, source_sha256,
                                 worksheet_name, worksheet_row_number, source_invoice,
                                 invoice_datetime, source_product_key,
                                 source_customer_key, source_description,
                                 source_country, signed_quantity, source_price,
                                 currency_code, is_cancellation_candidate,
                                 is_negative_quantity_candidate, is_zero_price,
                                 is_negative_price, is_missing_customer,
                                 is_missing_description, is_possible_duplicate,
                                 source_content_hash)
                                VALUES
                                (:real_load_id, :staging_row_id, :source_sha256,
                                 :worksheet_name, :worksheet_row_number, :source_invoice,
                                 :invoice_datetime, :source_product_key,
                                 :source_customer_key, :source_description,
                                 :source_country, :signed_quantity, :source_price,
                                 :currency_code, :is_cancellation_candidate,
                                 :is_negative_quantity_candidate, :is_zero_price,
                                 :is_negative_price, :is_missing_customer,
                                 :is_missing_description, :is_possible_duplicate,
                                 :source_content_hash)
                                """
                            ),
                            batch,
                        )
                source_engine.dispose()
                source_engine = create_engine(settings.database_url, pool_pre_ping=True)
                with source_engine.connect() as source_connection:
                    source_connection.execute(text("SET TRANSACTION READ ONLY"))
                    report = reconcile_stream(
                        source_rows=(
                            _row_summary(record)
                            for record in _source_rows(source_connection, load_id)
                        ),
                        transformed_rows=(
                            _row_summary(
                                record,
                                transform_staged_row(record),
                            )
                            for record in _source_rows(source_connection, load_id)
                        ),
                        source_row_count=source_load["rows_read"],
                        staged_row_count=source_load["rows_staged"],
                        transformed_row_count=source_load["rows_read"],
                        rejected_row_count=0,
                        target_ready_row_count=source_load["rows_read"],
                    )
                source_engine.dispose()
                if not report.ready:
                    failed = [check.name for check in report.checks if not check.passed]
                    raise ValueError(f"Pure reconciliation failed: {failed}")
                failures = _db_checks(destination_connection, source_load, source_metrics, real_load_id)
                if failures:
                    raise ValueError("Database reconciliation failed: " + "; ".join(failures))
                destination_connection.execute(
                    text(
                        """
                        UPDATE real_retail.loads
                        SET status = 'completed', completed_at = CURRENT_TIMESTAMP,
                            rows_loaded = :rows_loaded, rows_rejected = 0
                        WHERE real_load_id = :real_load_id
                        """
                    ),
                    {"rows_loaded": source_load["rows_read"], "real_load_id": real_load_id},
                )
                _set_sequences(destination_connection)
                transaction.commit()
                return {
                    "mode": "loaded",
                    "real_load_id": real_load_id,
                    "source_rows": source_load["rows_read"],
                    "destination_rows": source_load["rows_read"],
                    "quantity_total": str(source_metrics["quantity_total"]),
                    "monetary_total": str(source_metrics["monetary_total"]),
                    "quality": {
                        key: source_metrics[key]
                        for key in (
                            "cancellation",
                            "negative_quantity",
                            "negative_price",
                            "zero_price",
                            "missing_customer",
                            "missing_description",
                            "duplicate",
                        )
                    },
                    "currency": CURRENCY_UNKNOWN,
                    "reconciliation_ready": True,
                }
            except Exception:
                transaction.rollback()
                raise
    finally:
        destination_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-load-id", type=int, default=1)
    parser.add_argument(
        "--file",
        type=Path,
        default=PROJECT_ROOT / "data" / "raw" / SOURCE_FILENAME,
    )
    args = parser.parse_args()
    if not args.file.is_file():
        raise SystemExit(f"Workbook not found: {args.file}")
    try:
        result = run(args.staging_load_id, args.file)
    except Exception as error:
        raise SystemExit(f"Real-retail integration failed: {error}") from error
    print(json.dumps(result, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
