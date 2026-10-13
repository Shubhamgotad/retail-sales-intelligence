from pathlib import Path

import pandas as pd

from scripts.profile_retail_dataset import (
    profile_workbook,
    render_report,
    validate_transaction_frame,
)


def test_profile_workbook_detects_transaction_sheets_without_exposing_customer_ids(tmp_path: Path):
    workbook = tmp_path / "sample.xlsx"
    frame = pd.DataFrame(
        [
            ["10001", "A", "Item A", 2, "2010-01-01", 3.5, "secret-customer", "United Kingdom"],
            ["C10002", "A", "Item A", -1, "2010-01-02", 3.5, "secret-customer", "United Kingdom"],
        ],
        columns=["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"],
    )
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Transactions", index=False)
        pd.DataFrame({"note": ["not a transaction table"]}).to_excel(
            writer, sheet_name="Notes", index=False
        )

    profile = profile_workbook(workbook)
    assert profile["transaction_sheets"] == ["Transactions"]
    assert profile["combined"]["rows"] == 2
    assert profile["combined"]["negative_quantity"] == 1
    assert profile["combined"]["cancellation_rows"] == 1
    report = render_report(profile)
    assert "secret-customer" not in report
    assert "Customer ID" in report


def test_validation_reports_values_incompatible_with_fact_sales():
    frame = pd.DataFrame(
        {
            "Invoice": ["1", "C2"],
            "StockCode": ["A", "B"],
            "Description": ["A", "B"],
            "Quantity": [1, -1],
            "InvoiceDate": ["2010-01-01", "2010-01-02"],
            "Price": [2.0, -3.0],
            "Customer ID": [1, None],
            "Country": ["UK", "UK"],
        }
    )
    issues = validate_transaction_frame(frame)
    assert "Quantity contains zero or negative values." in issues
    assert "Price contains negative values." in issues
