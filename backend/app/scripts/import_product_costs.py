"""Validate and explicitly import verified costs to disposable real_retail."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from app.transformations.product_costs import (
    ProductCostCsvReport,
    ProductUnitCost,
    validate_product_cost_csv,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TEMPLATE = PROJECT_ROOT / "data" / "templates" / "product_unit_costs_template.csv"
DESTINATION_DATABASE = "retail_sales_test_disposable"
DESTINATION_HOSTS = {"localhost", "127.0.0.1", "::1"}
DESTINATION_ENV = "REAL_RETAIL_TEST_DATABASE_URL"
CONFIRMATION = "retail_sales_test_disposable"
SOURCE_CURRENCY = "GBP"


def validate_import_target(
    raw_url: str,
    confirmation: str,
    *,
    actual_database: str | None = None,
) -> URL:
    """Fail closed unless URL, explicit confirmation, and optional live identity agree."""
    if confirmation != CONFIRMATION:
        raise ValueError(
            f"Pass --confirm-disposable-target {CONFIRMATION!r}; refusing to import"
        )
    try:
        url = make_url(raw_url)
    except SQLAlchemyError as error:
        raise ValueError("Disposable database URL is invalid") from error
    if url.database != DESTINATION_DATABASE:
        raise ValueError(
            f"{DESTINATION_ENV} must target {DESTINATION_DATABASE!r}; refusing to import"
        )
    if url.get_backend_name() != "postgresql":
        raise ValueError(f"{DESTINATION_ENV} must use PostgreSQL")
    if (url.host or "").lower() not in DESTINATION_HOSTS:
        raise ValueError(f"{DESTINATION_ENV} must target a local PostgreSQL host")
    if actual_database is not None and actual_database != DESTINATION_DATABASE:
        raise ValueError(
            f"Connected database identity {actual_database!r} does not match "
            f"{DESTINATION_DATABASE!r}; refusing to import"
        )
    return url


def _known_products(connection: Connection) -> set[str]:
    rows = connection.execute(
        text("SELECT source_product_key FROM real_retail.source_products")
    ).scalars()
    return set(rows)


def _existing_costs(connection: Connection) -> list[ProductUnitCost]:
    rows = connection.execute(
        text(
            """
            SELECT source_product_key, unit_cost, currency_code,
                   effective_from, effective_to, source_reference
            FROM real_retail.product_unit_costs
            """
        )
    ).mappings()
    return [
        ProductUnitCost(
            stock_code=row["source_product_key"],
            unit_cost=row["unit_cost"],
            currency_code=row["currency_code"],
            effective_from=row["effective_from"],
            effective_to=row["effective_to"],
            source_reference=row["source_reference"],
        )
        for row in rows
    ]


def _table_exists(connection: Connection) -> bool:
    return connection.execute(
        text("SELECT to_regclass('real_retail.product_unit_costs')")
    ).scalar_one() is not None


def prepare_import(
    connection: Connection,
    csv_text: str,
    *,
    currency_mismatch_policy: str,
) -> ProductCostCsvReport:
    """Validate against actual disposable product keys and existing cost periods."""
    actual_database = connection.execute(text("SELECT current_database()")).scalar_one()
    validate_import_target(
        f"postgresql://localhost/{actual_database}",
        CONFIRMATION,
        actual_database=actual_database,
    )
    if not _table_exists(connection):
        raise ValueError(
            "real_retail.product_unit_costs is not installed; apply the reviewed "
            "real-retail schema only to the confirmed disposable database"
        )
    return validate_product_cost_csv(
        csv_text,
        _known_products(connection),
        existing_costs=_existing_costs(connection),
        currency_mismatch_policy=currency_mismatch_policy,
        source_currency=SOURCE_CURRENCY,
    )


def _report_payload(report: ProductCostCsvReport, *, apply: bool) -> dict[str, Any]:
    data_row_count = len(report.accepted) + len(report.rejected)
    return {
        "accepted_rows": [
            {
                "row_number": row_number,
                "stock_code": record.stock_code,
                "unit_cost": str(record.unit_cost),
                "currency_code": record.currency_code,
                "effective_from": record.effective_from.isoformat(),
                "effective_to": record.effective_to.isoformat() if record.effective_to else None,
                "source_reference": record.source_reference,
            }
            for row_number, record in report.accepted
        ],
        "rejected_rows": [asdict(row) for row in report.rejected],
        "warnings": list(report.warnings),
        "accepted_count": len(report.accepted),
        "rejected_count": len(report.rejected),
        "data_row_count": data_row_count,
        "message": (
            "No data rows found; nothing to validate or persist."
            if data_row_count == 0
            else None
        ),
        "persistence_requested": apply,
        "persisted_count": 0,
    }


def persist_import(connection: Connection, report: ProductCostCsvReport) -> int:
    """Insert validated costs without overwriting existing rows."""
    connection.execute(
        text("LOCK TABLE real_retail.product_unit_costs IN SHARE ROW EXCLUSIVE MODE")
    )
    if not _table_exists(connection):
        raise ValueError("Cost table disappeared before persistence")
    if not report.accepted:
        return 0
    parameters = [
        {
            "source_product_key": record.stock_code,
            "unit_cost": record.unit_cost,
            "currency_code": record.currency_code,
            "effective_from": record.effective_from,
            "effective_to": record.effective_to,
            "source_reference": record.source_reference,
        }
        for _, record in report.accepted
    ]
    connection.execute(
        text(
            """
            INSERT INTO real_retail.product_unit_costs (
                source_product_key, unit_cost, currency_code,
                effective_from, effective_to, source_reference
            ) VALUES (
                :source_product_key, :unit_cost, :currency_code,
                :effective_from, :effective_to, :source_reference
            )
            """
        ),
        parameters,
    )
    return len(parameters)


def run_import(
    csv_path: Path,
    *,
    apply: bool,
    confirmation: str,
    currency_mismatch_policy: str,
) -> dict[str, Any]:
    raw_url = os.environ.get(DESTINATION_ENV)
    if not raw_url:
        raise ValueError(
            f"{DESTINATION_ENV} is required; refusing to connect or import"
        )
    destination_url = validate_import_target(raw_url, confirmation)
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as source_file:
            csv_text = source_file.read()
    except OSError as error:
        raise ValueError(f"Cannot read cost CSV {csv_path}: {error}") from error

    engine = create_engine(destination_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                actual_database = connection.execute(
                    text("SELECT current_database()")
                ).scalar_one()
                validate_import_target(
                    raw_url,
                    confirmation,
                    actual_database=actual_database,
                )
                if apply:
                    connection.execute(
                        text(
                            "LOCK TABLE real_retail.product_unit_costs "
                            "IN SHARE ROW EXCLUSIVE MODE"
                        )
                    )
                report = prepare_import(
                    connection,
                    csv_text,
                    currency_mismatch_policy=currency_mismatch_policy,
                )
                payload = _report_payload(report, apply=apply)
                print(json.dumps(payload, sort_keys=True))
                if report.rejected:
                    transaction.rollback()
                    return {**payload, "status": "rejected", "persisted_count": 0}
                if not apply:
                    transaction.rollback()
                    return {**payload, "status": "validated"}
                persisted = persist_import(connection, report)
                transaction.commit()
                return {**payload, "status": "imported", "persisted_count": persisted}
            except Exception:
                transaction.rollback()
                raise
    except SQLAlchemyError as error:
        raise RuntimeError(
            f"Disposable product-cost import failed: {type(error).__name__}"
        ) from error
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument(
        "--confirm-disposable-target",
        required=True,
        help=f"Must exactly equal {CONFIRMATION!r}.",
    )
    parser.add_argument(
        "--currency-mismatch-policy",
        required=True,
        choices=("reject", "keep-separate"),
        help="Reject non-GBP costs, or retain their currency separately without conversion.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist accepted rows after full-file validation; default is validation-only.",
    )
    args = parser.parse_args()
    if not args.file.is_file():
        raise SystemExit(f"Cost CSV not found: {args.file}")
    try:
        result = run_import(
            args.file,
            apply=args.apply,
            confirmation=args.confirm_disposable_target,
            currency_mismatch_policy=args.currency_mismatch_policy,
        )
    except (ValueError, RuntimeError, csv.Error) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, sort_keys=True))
    return 2 if result["status"] == "rejected" else 0


if __name__ == "__main__":
    raise SystemExit(main())
