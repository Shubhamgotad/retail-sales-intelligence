import csv
from datetime import date
from io import StringIO
from pathlib import Path
import sys

import pytest

from app.scripts import import_product_costs
from app.scripts.import_product_costs import (
    CONFIRMATION,
    DEFAULT_TEMPLATE,
    DESTINATION_ENV,
    DESTINATION_DATABASE,
    _report_payload,
    prepare_import,
    run_import,
    validate_import_target,
)
from app.transformations.product_costs import (
    ProductCostValidationError,
    ProductUnitCost,
    validate_product_cost_csv,
)

PRODUCT_KEYS = {"00123", "ABC-1"}
CSV_HEADER = (
    "stock_code,unit_cost,currency_code,effective_from,effective_to,source_reference\n"
)


def csv_row(**overrides):
    row = {
        "stock_code": "00123",
        "unit_cost": "12.3400",
        "currency_code": "GBP",
        "effective_from": "2010-12-01",
        "effective_to": "2011-06-30",
        "source_reference": "verified-document-ref",
    }
    row.update(overrides)
    return row


def make_csv(*records):
    stream = StringIO()
    writer = csv.DictWriter(
        stream,
        fieldnames=(
            "stock_code",
            "unit_cost",
            "currency_code",
            "effective_from",
            "effective_to",
            "source_reference",
        ),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue()


def validate_csv(text, *, policy="reject", existing=()):
    return validate_product_cost_csv(
        text,
        PRODUCT_KEYS,
        existing_costs=existing,
        currency_mismatch_policy=policy,
    )


def test_csv_parser_uses_header_names_and_preserves_exact_decimal():
    reordered = "unit_cost,stock_code,effective_from,currency_code,source_reference,effective_to\n"
    report = validate_csv(
        reordered + "12.340000,00123,2010-12-01,GBP,verified-document-ref,2011-06-30\n"
    )
    assert len(report.accepted) == 1
    row_number, record = report.accepted[0]
    assert row_number == 2
    assert record.stock_code == "00123"
    assert str(record.unit_cost) == "12.340000"


def test_csv_header_requires_all_template_columns():
    with pytest.raises(ProductCostValidationError, match="Invalid CSV header"):
        validate_csv("stock_code,unit_cost,currency_code\n")


def test_unknown_product_ids_are_reported_by_csv_row_without_silent_skips():
    report = validate_csv(
        make_csv(
            csv_row(stock_code="NOT-A-SOURCE-PRODUCT"),
            csv_row(stock_code="ABC-1"),
        )
    )
    assert [number for number, _ in report.accepted] == [3]
    assert [(row.row_number, row.stock_code) for row in report.rejected] == [
        (2, "NOT-A-SOURCE-PRODUCT")
    ]
    assert "does not match a real-retail product identifier" in report.rejected[0].reason


def test_duplicate_import_rows_reject_later_row_with_clear_number():
    duplicate = csv_row()
    report = validate_csv(make_csv(duplicate, duplicate))
    assert [number for number, _ in report.accepted] == [2]
    assert report.rejected[0].row_number == 3
    assert "duplicate product/currency/effective-period" in report.rejected[0].reason


def test_overlapping_import_periods_are_rejected_even_when_cost_is_same():
    report = validate_csv(
        make_csv(
            csv_row(effective_from="2010-12-01", effective_to="2011-06-30"),
            csv_row(effective_from="2011-06-30", effective_to="2012-01-01"),
        )
    )
    assert [number for number, _ in report.accepted] == [2]
    assert report.rejected[0].row_number == 3
    assert "overlapping effective period" in report.rejected[0].reason


def test_existing_database_periods_are_checked_against_incoming_rows():
    existing = ProductUnitCost(
        stock_code="00123",
        unit_cost=12,
        currency_code="GBP",
        effective_from=date(2010, 1, 1),
        effective_to=date(2010, 12, 31),
        source_reference="existing-verified-document",
    )
    report = validate_csv(
        make_csv(csv_row(effective_from="2010-12-31")),
        existing=(existing,),
    )
    assert report.accepted == ()
    assert report.rejected[0].row_number == 2
    assert "existing warehouse record" in report.rejected[0].reason


def test_currency_mismatch_requires_policy_and_never_converts():
    text = make_csv(csv_row(currency_code="USD"))
    rejected = validate_csv(text, policy="reject")
    assert rejected.rejected[0].row_number == 2
    assert "no conversion is performed" in rejected.rejected[0].reason

    kept_separate = validate_csv(text, policy="keep-separate")
    assert kept_separate.accepted[0][1].currency_code == "USD"
    assert "no conversion is performed" in kept_separate.warnings[0]

    with pytest.raises(ValueError, match="currency_mismatch_policy"):
        validate_product_cost_csv(text, PRODUCT_KEYS, currency_mismatch_policy="")


@pytest.mark.parametrize(
    ("url", "confirmation", "actual_database"),
    [
        ("postgresql://localhost/retail_sales_db", CONFIRMATION, None),
        ("postgresql://example.com/retail_sales_test_disposable", CONFIRMATION, None),
        ("mysql://localhost/retail_sales_test_disposable", CONFIRMATION, None),
        ("postgresql://localhost/retail_sales_test_disposable", "wrong", None),
        ("postgresql://localhost/retail_sales_test_disposable", CONFIRMATION, "retail_sales_db"),
    ],
)
def test_import_target_fails_closed_for_non_disposable_or_unconfirmed_targets(
    url, confirmation, actual_database
):
    with pytest.raises(ValueError):
        validate_import_target(url, confirmation, actual_database=actual_database)


def test_import_target_requires_exact_disposable_database_and_confirmation():
    url = validate_import_target(
        "postgresql://localhost/retail_sales_test_disposable",
        CONFIRMATION,
        actual_database=DESTINATION_DATABASE,
    )
    assert url.database == DESTINATION_DATABASE


def test_row_report_includes_csv_line_numbers_and_accepted_source_values():
    report = validate_csv(make_csv(csv_row(), csv_row(stock_code="UNKNOWN")))
    assert report.accepted[0][0] == 2
    assert report.accepted[0][1].source_reference == "verified-document-ref"
    assert report.rejected[0].row_number == 3
    assert report.rejected[0].reason


def test_importer_fails_without_disposable_url_and_never_uses_application_settings(
    monkeypatch,
):
    monkeypatch.delenv(DESTINATION_ENV, raising=False)
    monkeypatch.setattr(
        import_product_costs,
        "create_engine",
        lambda *_args, **_kwargs: pytest.fail("must not create an engine without disposable URL"),
    )
    with pytest.raises(ValueError, match=DESTINATION_ENV):
        run_import(
            Path("not-read-until-url-is-validated.csv"),
            apply=True,
            confirmation=CONFIRMATION,
            currency_mismatch_policy="reject",
        )


def test_header_only_template_reports_no_data_rows_and_no_persistence():
    report = validate_csv(DEFAULT_TEMPLATE.read_text(encoding="utf-8"))
    payload = _report_payload(report, apply=False)
    assert report.accepted == ()
    assert report.rejected == ()
    assert payload["data_row_count"] == 0
    assert payload["message"] == "No data rows found; nothing to validate or persist."
    assert payload["persistence_requested"] is False
    assert payload["persisted_count"] == 0


def test_cli_requires_explicit_disposable_confirmation(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_product_costs",
            "--currency-mismatch-policy",
            "reject",
        ],
    )
    monkeypatch.setattr(
        import_product_costs,
        "create_engine",
        lambda *_args, **_kwargs: pytest.fail("CLI must reject missing confirmation"),
    )
    with pytest.raises(SystemExit) as error:
        import_product_costs.main()
    assert error.value.code == 2


class FakeResult:
    def __init__(self, value=None, rows=()):
        self.value = value
        self.rows = rows

    def scalar_one(self):
        return self.value

    def scalars(self):
        return iter(self.rows)

    def mappings(self):
        return iter(self.rows)


class FakeConnection:
    def __init__(self):
        self.queries = []

    def execute(self, statement, _params=None):
        query = str(statement)
        self.queries.append(query)
        if "current_database()" in query:
            return FakeResult(value=DESTINATION_DATABASE)
        if "to_regclass" in query:
            return FakeResult(value="real_retail.product_unit_costs")
        if "FROM real_retail.source_products" in query:
            return FakeResult(rows=PRODUCT_KEYS)
        if "FROM real_retail.product_unit_costs" in query:
            return FakeResult(rows=[])
        raise AssertionError(f"Unexpected query: {query}")


class FakeTransaction:
    def __init__(self):
        self.rolled_back = False
        self.committed = False

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        self.committed = True


class FakeImportConnection(FakeConnection):
    def __init__(self):
        super().__init__()
        self.transaction = FakeTransaction()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def begin(self):
        return self.transaction


class FakeImportEngine:
    def __init__(self):
        self.connection = FakeImportConnection()
        self.disposed = False

    def connect(self):
        return self.connection

    def dispose(self):
        self.disposed = True


def test_import_without_apply_validates_then_rolls_back_without_insert(
    monkeypatch, tmp_path, capsys
):
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text(CSV_HEADER, encoding="utf-8")
    engine = FakeImportEngine()
    monkeypatch.setenv(
        DESTINATION_ENV,
        "postgresql://localhost/retail_sales_test_disposable",
    )
    monkeypatch.setattr(import_product_costs, "create_engine", lambda *_args, **_kwargs: engine)

    result = run_import(
        csv_path,
        apply=False,
        confirmation=CONFIRMATION,
        currency_mismatch_policy="reject",
    )

    assert result["status"] == "validated"
    assert result["message"] == "No data rows found; nothing to validate or persist."
    assert result["persisted_count"] == 0
    assert engine.connection.transaction.rolled_back is True
    assert engine.connection.transaction.committed is False
    assert not any("INSERT INTO" in query for query in engine.connection.queries)
    assert engine.disposed is True
    assert "No data rows found" in capsys.readouterr().out


def test_preparation_uses_live_product_set_and_existing_cost_table_only():
    connection = FakeConnection()
    report = prepare_import(
        connection,
        make_csv(csv_row(stock_code="UNKNOWN")),
        currency_mismatch_policy="reject",
    )
    assert report.rejected[0].row_number == 2
    assert any("source_products" in query for query in connection.queries)
    assert any("product_unit_costs" in query for query in connection.queries)
    assert all("retail_sales_db" not in query for query in connection.queries)
