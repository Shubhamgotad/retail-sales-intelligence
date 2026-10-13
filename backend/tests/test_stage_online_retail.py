from pathlib import Path

import pandas as pd

from app.staging.online_retail import render_validation_report, stage_workbook


class FakeRepository:
    def __init__(self):
        self.load_metadata = None
        self.rows = []
        self.finished = None
        self.fail_inserts = False

    def start_load(self, metadata):
        self.load_metadata = metadata
        return 17

    def insert_rows(self, rows):
        if self.fail_inserts:
            raise RuntimeError("simulated staging failure")
        keys = {
            (row["source_sha256"], row["worksheet_name"], row["worksheet_row_number"])
            for row in self.rows
        }
        new_rows = [
            row for row in rows
            if (row["source_sha256"], row["worksheet_name"], row["worksheet_row_number"]) not in keys
        ]
        self.rows.extend(new_rows)
        return len(new_rows)

    def finish_load(self, load_id, values):
        self.finished = (load_id, values)


def _make_workbook(path: Path) -> None:
    columns = [
        "Invoice", "StockCode", "Description", "Quantity",
        "InvoiceDate", "Price", "Customer ID", "Country",
    ]
    first = pd.DataFrame([
        ["10001", "A", "Item A", 2, "2010-01-01", 3.5, 123, "United Kingdom"],
        ["C10002", "B", None, -1, "2010-01-02", 4.0, None, "United Kingdom"],
        ["C10002", "B", None, -1, "2010-01-02", 4.0, None, "United Kingdom"],
    ], columns=columns)
    second = pd.DataFrame([
        ["10003", "C", "Item C", 1, "2010-02-01", 0, 456, "France"],
    ], columns=columns)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        first.to_excel(writer, sheet_name="Year A", index=False)
        second.to_excel(writer, sheet_name="Year B", index=False)


def test_dry_run_reads_both_sheets_and_does_not_persist(tmp_path: Path):
    workbook = tmp_path / "retail.xlsx"
    _make_workbook(workbook)
    repository = FakeRepository()

    summary, load_id = stage_workbook(workbook, repository=repository, write=False)

    assert load_id is None
    assert repository.rows == []
    assert summary.rows_read == 4
    assert dict(summary.by_sheet) == {"Year A": 3, "Year B": 1}
    assert summary.cancellation_rows == 2
    assert summary.potential_return_rows == 2
    assert summary.missing["Customer ID"] == 2
    assert summary.duplicate_content_rows == 2
    assert summary.zero_price_rows == 1
    assert "validation-only dry run" in render_validation_report(summary, load_id=None, write=False)


def test_write_is_idempotent_by_checksum_sheet_and_row(tmp_path: Path):
    workbook = tmp_path / "retail.xlsx"
    _make_workbook(workbook)
    repository = FakeRepository()

    first_summary, first_load = stage_workbook(workbook, repository=repository, write=True)
    second_summary, second_load = stage_workbook(workbook, repository=repository, write=True)

    assert first_load == 17
    assert second_load == 17
    assert first_summary.rows_staged == 4
    assert second_summary.rows_staged == 0
    assert len(repository.rows) == 4
    assert repository.finished[1]["status"] == "completed"


def test_missing_source_columns_fail_before_persistence(tmp_path: Path):
    workbook = tmp_path / "bad.xlsx"
    pd.DataFrame({"Invoice": ["1"]}).to_excel(workbook, index=False)
    repository = FakeRepository()

    try:
        stage_workbook(workbook, repository=repository, write=True)
    except ValueError as error:
        assert "exact Online Retail II columns" in str(error)
    else:
        raise AssertionError("Expected missing-column validation failure")
    assert repository.load_metadata is None


def test_write_failure_marks_load_failed(tmp_path: Path):
    workbook = tmp_path / "retail.xlsx"
    _make_workbook(workbook)
    repository = FakeRepository()
    repository.fail_inserts = True

    try:
        stage_workbook(workbook, repository=repository, write=True)
    except RuntimeError as error:
        assert str(error) == "simulated staging failure"
    else:
        raise AssertionError("Expected staging failure")
    assert repository.finished[1]["status"] == "failed"
