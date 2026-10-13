r"""Profile the downloaded Online Retail II workbook without changing any data.

Usage:
    python scripts/profile_retail_dataset.py
    python scripts/profile_retail_dataset.py --input ..\data\raw\online_retail_II.xlsx \
        --output ..\data\processed\retail_dataset_profile.md
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

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


def _percentage(count: int, total: int) -> str:
    return f"{(count / total * 100):.2f}%" if total else "0.00%"


def _missing_counts(frame: pd.DataFrame) -> dict[str, int]:
    return {str(column): int(frame[column].isna().sum()) for column in frame.columns}


def validate_transaction_frame(frame: pd.DataFrame) -> list[str]:
    """Return data-quality issues relevant to the current warehouse schema."""
    issues: list[str] = []
    missing_columns = [column for column in SOURCE_COLUMNS if column not in frame.columns]
    if missing_columns:
        issues.append(f"Missing source columns: {', '.join(missing_columns)}")
        return issues

    if frame["Invoice"].isna().any():
        issues.append("Invoice contains missing values.")
    if frame["StockCode"].isna().any():
        issues.append("StockCode contains missing values.")
    if pd.to_datetime(frame["InvoiceDate"], errors="coerce").isna().any():
        issues.append("InvoiceDate contains values that cannot be parsed as dates.")
    if pd.to_numeric(frame["Quantity"], errors="coerce").isna().any():
        issues.append("Quantity contains non-numeric values.")
    if pd.to_numeric(frame["Price"], errors="coerce").isna().any():
        issues.append("Price contains non-numeric values.")
    if (pd.to_numeric(frame["Quantity"], errors="coerce") <= 0).any():
        issues.append("Quantity contains zero or negative values.")
    if (pd.to_numeric(frame["Price"], errors="coerce") < 0).any():
        issues.append("Price contains negative values.")
    return issues


def _profile_frame(frame: pd.DataFrame) -> dict[str, Any]:
    total = len(frame)
    dates = pd.to_datetime(frame["InvoiceDate"], errors="coerce")
    quantities = pd.to_numeric(frame["Quantity"], errors="coerce")
    prices = pd.to_numeric(frame["Price"], errors="coerce")
    invoice_text = frame["Invoice"].astype("string")
    cancellation_mask = invoice_text.str.startswith("C", na=False)
    return {
        "rows": total,
        "columns": [str(column) for column in frame.columns],
        "dtypes": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
        "missing": _missing_counts(frame),
        "duplicate_rows": int(frame.duplicated(keep=False).sum()),
        "date_invalid": int(dates.isna().sum()),
        "date_min": dates.min().isoformat() if dates.notna().any() else None,
        "date_max": dates.max().isoformat() if dates.notna().any() else None,
        "negative_quantity": int((quantities < 0).sum()),
        "zero_quantity": int((quantities == 0).sum()),
        "negative_price": int((prices < 0).sum()),
        "zero_price": int((prices == 0).sum()),
        "cancellation_rows": int(cancellation_mask.sum()),
        "cancellation_invoices": int(invoice_text[cancellation_mask].nunique()),
        "distinct_invoices": int(invoice_text.dropna().nunique()),
        "distinct_products": int(frame["StockCode"].dropna().nunique()),
        "distinct_customers": int(frame["Customer ID"].dropna().nunique()),
        "distinct_countries": int(frame["Country"].dropna().nunique()),
        "validation_issues": validate_transaction_frame(frame),
    }


def profile_workbook(path: str | Path) -> dict[str, Any]:
    """Read and summarize every workbook sheet; never writes to the workbook or DB."""
    workbook_path = Path(path)
    if not workbook_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")

    excel = pd.ExcelFile(workbook_path, engine="openpyxl")
    sheets: dict[str, dict[str, Any]] = {}
    transaction_frames: list[pd.DataFrame] = []
    for sheet_name in excel.sheet_names:
        frame = pd.read_excel(excel, sheet_name=sheet_name)
        profile = _profile_frame(frame) if set(SOURCE_COLUMNS).issubset(frame.columns) else {
            "rows": len(frame),
            "columns": [str(column) for column in frame.columns],
            "transaction_sheet": False,
        }
        profile["transaction_sheet"] = set(SOURCE_COLUMNS).issubset(frame.columns)
        sheets[sheet_name] = profile
        if profile["transaction_sheet"]:
            transaction_frames.append(frame)

    if not transaction_frames:
        raise ValueError("No sheet contains the expected transaction columns.")

    combined = pd.concat(transaction_frames, ignore_index=True)
    combined_profile = _profile_frame(combined)
    currency_columns = [
        column for column in combined.columns
        if str(column).strip().lower() in {"currency", "currency code", "currency_code"}
    ]
    combined_profile["currency_columns"] = [str(column) for column in currency_columns]
    combined_profile["currency_values"] = (
        sorted(combined[currency_columns[0]].dropna().astype(str).unique().tolist())
        if currency_columns
        else []
    )
    return {
        "path": str(workbook_path),
        "sheets": sheets,
        "transaction_sheets": [
            name for name, details in sheets.items() if details["transaction_sheet"]
        ],
        "combined": combined_profile,
    }


def _missing_table(missing: dict[str, int], rows: int) -> str:
    lines = ["| Column | Missing | Missing % |", "|---|---:|---:|"]
    lines.extend(
        f"| `{column}` | {count:,} | {_percentage(count, rows)} |"
        for column, count in missing.items()
    )
    return "\n".join(lines)


def render_report(profile: dict[str, Any]) -> str:
    combined = profile["combined"]
    lines = [
        "# Online Retail II dataset profile",
        "",
        "This report was generated by `backend/scripts/profile_retail_dataset.py`.",
        "It contains aggregate diagnostics only; customer names and customer IDs are not recorded.",
        "",
        "## Workbook structure",
        "",
        f"- Transaction sheets: {', '.join(profile['transaction_sheets'])}",
        f"- Total transaction rows across transaction sheets: {combined['rows']:,}",
        f"- Date range (parsed): {combined['date_min']} to {combined['date_max']}",
        f"- Exact duplicate rows (including all duplicate occurrences): {combined['duplicate_rows']:,}",
        f"- Distinct invoices: {combined['distinct_invoices']:,}",
        f"- Distinct products/stock codes: {combined['distinct_products']:,}",
        f"- Distinct customers with a value: {combined['distinct_customers']:,}",
        f"- Distinct countries: {combined['distinct_countries']:,}",
        "",
        "The workbook contains the transaction columns `Invoice`, `StockCode`, `Description`,",
        "`Quantity`, `InvoiceDate`, `Price`, `Customer ID`, and `Country`.",
        "",
        "## Sheet-level row counts and date ranges",
        "",
        "| Sheet | Transaction table | Rows | Date range | Duplicate rows |",
        "|---|---|---:|---|---:|",
    ]
    for name, details in profile["sheets"].items():
        lines.append(
            f"| {name} | {'yes' if details['transaction_sheet'] else 'no'} | "
            f"{details['rows']:,} | {details.get('date_min', 'n/a')} to "
            f"{details.get('date_max', 'n/a')} | {details.get('duplicate_rows', 0):,} |"
        )
    lines.extend([
        "",
        "## Missing values",
        "",
        _missing_table(combined["missing"], combined["rows"]),
        "",
        "## Returns, cancellations, and invalid monetary/quantity values",
        "",
        f"- Rows with negative quantity: {combined['negative_quantity']:,} "
        f"({_percentage(combined['negative_quantity'], combined['rows'])})",
        f"- Rows with zero quantity: {combined['zero_quantity']:,} "
        f"({_percentage(combined['zero_quantity'], combined['rows'])})",
        f"- Rows with negative price: {combined['negative_price']:,} "
        f"({_percentage(combined['negative_price'], combined['rows'])})",
        f"- Rows with zero price: {combined['zero_price']:,} "
        f"({_percentage(combined['zero_price'], combined['rows'])})",
        f"- Rows whose invoice begins with `C`: {combined['cancellation_rows']:,} "
        f"({_percentage(combined['cancellation_rows'], combined['rows'])})",
        f"- Distinct cancellation invoices: {combined['cancellation_invoices']:,}",
        "",
        "Cancellation and return rows must be retained in staging and classified explicitly.",
        "They must not be silently loaded into the current `fact_sales` table because that table",
        "requires `quantity > 0` and non-negative monetary amounts.",
        "",
        "## Data types and currency",
        "",
        "```text",
    ])
    lines.extend(f"{column}: {dtype}" for column, dtype in combined["dtypes"].items())
    lines.extend([
        "```",
        "",
        f"- Currency columns found: {', '.join(combined['currency_columns']) or 'none'}",
        f"- Currency values found: {', '.join(combined['currency_values']) or 'none'}",
        "- The workbook does not encode a currency column. GBP is not independently confirmed by",
        "  the workbook itself; preserve `Price` as source currency and require source provenance",
        "  confirmation before labeling it GBP or converting it.",
        "",
        "## Warehouse mapping and incompatibilities",
        "",
        "| Source column | Proposed warehouse field | Status |",
        "|---|---|---|",
        "| `StockCode` | `dim_product.product_id` (through a source-ID mapping) | available |",
        "| `Description` | `dim_product.product_name` | available, normalize carefully |",
        "| `Price` | `fact_sales.unit_price` | available; currency unconfirmed |",
        "| `Quantity` | `fact_sales.quantity` | available only after return/cancellation policy |",
        "| `Invoice` | `fact_sales.transaction_id` | available; preserve cancellation marker |",
        "| `InvoiceDate` | `dim_date.full_date` / `date_id` | available |",
        "| `Country` | no direct warehouse field | available only for a future geography mapping |",
        "| `Customer ID` | `dim_customer` source key | available, but current schema has no source-key column |",
        "| product cost | `dim_product.unit_cost` / `fact_sales.cost_amount` | missing |",
        "| store | `dim_store` / `fact_sales.store_id` | missing |",
        "| profit | `fact_sales.profit` | missing; do not invent |",
        "| currency | none | missing from workbook |",
        "",
        "The current backend uses raw SQL queries through SQLAlchemy sessions; the SQLAlchemy",
        "warehouse model module is currently empty. The current warehouse also requires non-null",
        "store and customer foreign keys, while the",
        "source has no store and has no approved mapping for customer dimensions. A staging model",
        "or an approved unknown-dimension policy is required before production-table changes.",
        "",
        "## Safe integration plan",
        "",
        "1. Preserve the synthetic warehouse and expose this workbook only through a separate staging",
        "   schema or isolated staging tables; do not mix source rows with demo facts.",
        "2. Load raw rows with source sheet, source row number, source file checksum, and an explicit",
        "   source transaction-line key. Keep cancellations, returns, zero prices, and missing IDs.",
        "3. Validate required fields, date parsing, duplicate source lines, product mappings, and",
        "   invoice-level basket consistency before any production insert.",
        "4. Obtain business decisions for currency provenance, cost/profit, store, customer handling,",
        "   returns, and whether country should become a region dimension.",
        "5. Build deterministic product and customer source-ID mappings, then reconcile row counts and",
        "   monetary totals. Only after review should a versioned import populate production tables.",
        "6. Recalculate dashboard aggregates, rerun clustering, retrain demand classification, and",
        "   recompute association rules after an approved import.",
        "",
        "## Current analytics limitations",
        "",
        "- Profit, cost, store/region analytics, and store-level joins cannot be populated from this",
        "  workbook without additional source data or an approved policy.",
        "- Demand classification and clustering require a deliberate treatment of cancellations and",
        "  returns; their results should not be compared directly with the synthetic demo.",
        "- Basket analysis can use `Invoice` plus `StockCode` after cancellation handling, but product",
        "  descriptions should not be the durable basket key.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data" / "raw" / "online_retail_II.xlsx",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data" / "processed" / "retail_dataset_profile.md",
    )
    args = parser.parse_args()
    report = render_report(profile_workbook(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"Wrote profile report to {args.output}")


if __name__ == "__main__":
    main()
