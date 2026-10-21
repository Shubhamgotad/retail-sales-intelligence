from datetime import date
from decimal import Decimal
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.api.real_retail_analytics import _sort
from app.db.real_retail import _configured_url, get_real_retail_connection
from app.services.real_retail_analytics import (
    _database_error_diagnostic,
    _date_params,
    _json_value,
    _rows,
)
from app.services.real_retail_analytics import (
    CURRENCY_CODE,
    _currency_rows,
    _profitability_fields,
    _profitability_query,
    result_warnings,
)
from app.services import real_retail_analytics


def test_decimal_values_are_serialized_as_exact_strings():
    assert _json_value(Decimal("19287250.568000")) == "19287250.568000"
    assert _json_value(date(2011, 12, 9)) == "2011-12-09"


def test_database_error_diagnostic_extracts_only_sanitized_fields():
    driver_error = SimpleNamespace(
        sqlstate="23505",
        diag=SimpleNamespace(
            message_primary=(
                'duplicate key value violates unique constraint "safe_constraint" '
                "Key (customer_id)=(private-customer-42) already exists; "
                "postgresql://private-user:private-password@localhost/private-db "
                "password=private-password"
            )
        ),
    )
    error = DBAPIError(
        "SELECT private_sql",
        {"customer_id": "private-customer-42"},
        driver_error,
        False,
    )

    exception_class, sqlstate, primary_message = _database_error_diagnostic(error)

    assert exception_class == "SimpleNamespace"
    assert sqlstate == "23505"
    assert "duplicate key value violates unique constraint" in primary_message
    assert "Key (customer_id)=([redacted])" in primary_message
    assert "private-customer-42" not in primary_message
    assert "private-user" not in primary_message
    assert "private-password" not in primary_message
    assert "private-db" not in primary_message
    assert "private_sql" not in primary_message


def test_database_error_diagnostic_uses_none_for_missing_or_invalid_sqlstate():
    error = DBAPIError(
        "SELECT ignored",
        {},
        SimpleNamespace(
            sqlstate="not-a-state",
            diag=SimpleNamespace(
                message_primary="invalid input syntax for type text: private-row-value"
            ),
        ),
        False,
    )
    assert _database_error_diagnostic(error) == (
        "SimpleNamespace",
        "none",
        "invalid input syntax for type text: [redacted]",
    )


def test_rows_logs_sanitized_diagnostic_and_keeps_safe_error(caplog):
    error = DBAPIError(
        "SELECT private_sql",
        {"private_parameter": "private-row-value"},
        SimpleNamespace(
            sqlstate="42703",
            diag=SimpleNamespace(
                message_primary='column "missing_column" does not exist'
            ),
        ),
        False,
    )

    class FailingConnection:
        def execute(self, *_args, **_kwargs):
            raise error

    with caplog.at_level("ERROR", logger="uvicorn.error"):
        with pytest.raises(RuntimeError, match="Real-retail analytics schema is unavailable"):
            _rows(FailingConnection(), "SELECT private_sql", {"key": "private_parameter"})

    assert caplog.records[-1].name == "uvicorn.error"
    assert "exception_class=SimpleNamespace" in caplog.text
    assert "sqlstate=42703" in caplog.text
    assert 'primary_message=column "missing_column" does not exist' in caplog.text
    assert "private_sql" not in caplog.text
    assert "private_parameter" not in caplog.text
    assert "private-row-value" not in caplog.text


def test_date_range_parameters_are_preserved_and_validated():
    assert _date_params(date(2010, 1, 1), date(2010, 2, 1)) == {
        "start_date": date(2010, 1, 1),
        "end_date": date(2010, 2, 1),
    }
    with pytest.raises(ValueError, match="on or before"):
        _date_params(date(2010, 2, 1), date(2010, 1, 1))


def test_sort_values_are_allowlisted():
    assert _sort("value") == "value"
    with pytest.raises(Exception):
        _sort("source_sql")


@pytest.mark.parametrize(
    ("description", "valid"),
    [
        (None, False),
        ("", False),
        (" \t\r\n ", False),
        ("?", False),
        ("??", False),
        ("????", False),
        ("!?.,-", False),
        ("[missing description]", False),
        (" [ Missing \tdescription ] ", False),
        ("Tea & cup!", True),
        ("Product #12 (large)", True),
        (" café-12 ", True),
    ],
)
def test_product_description_validity_rule(description, valid):
    assert real_retail_analytics.is_valid_product_description(description) is valid


def test_real_database_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("REAL_RETAIL_ANALYTICS_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="required"):
        _configured_url()


def test_real_feature_routes_are_registered():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert {
        "/api/real-retail/features/demand",
        "/api/real-retail/features/clustering",
        "/api/real-retail/features/basket",
    } <= paths


def test_real_feature_warnings_preserve_source_semantics():
    warnings = result_warnings()
    assert CURRENCY_CODE == "GBP"
    assert any("sterling (GBP)" in warning for warning in warnings)
    assert not any("UNKNOWN" in warning for warning in warnings)
    assert any("not verified revenue" in warning for warning in warnings)
    assert any("candidate" in warning for warning in warnings)


def test_monetary_api_rows_include_gbp_without_changing_decimal_strings():
    rows = _currency_rows([{"monetary_total": "19287250.568000000000"}])
    assert rows == [{
        "monetary_total": "19287250.568000000000",
        "currency_code": "GBP",
    }]


def test_verified_cost_matching_uses_product_date_currency_and_signed_quantities():
    query = _profitability_query()
    assert "cost.source_product_key = sl.source_product_key" in query
    assert "cost.currency_code = :currency_code" in query
    assert "sl.invoice_datetime::date >= cost.effective_from" in query
    assert "sl.invoice_datetime::date <= cost.effective_to" in query
    assert "SUM(signed_quantity * matched_unit_cost)" in query
    assert "AND NOT sl.is_cancellation_candidate" in query
    assert "signed_quantity > 0" not in query


def test_matched_costs_produce_exact_actual_profit_and_margin():
    result = _profitability_fields(
        {
            "profitability_eligible_line_count": 2,
            "cost_matched_line_count": 2,
            "cost_missing_line_count": 0,
            "cost_ambiguous_line_count": 0,
            "cost_missing_product_or_date_line_count": 0,
            "profitability_sales_total": Decimal("80.0000"),
            "actual_cost_total": Decimal("50.0000"),
        },
        eligible_line_count=2,
        cost_table_available=True,
    )
    assert result["profitability_status"] == "available"
    assert result["actual_cost_total"] == "50.0000"
    assert result["actual_gross_profit"] == "30.0000"
    assert Decimal(result["actual_gross_margin"]) == Decimal("37.5")


def test_signed_return_cost_and_sales_reduce_profit_without_treating_it_as_cancellation():
    # A sale (100, cost 60) plus a signed return (-20, cost -12) nets to 32 profit.
    result = _profitability_fields(
        {
            "profitability_eligible_line_count": 2,
            "cost_matched_line_count": 2,
            "cost_missing_line_count": 0,
            "cost_ambiguous_line_count": 0,
            "cost_missing_product_or_date_line_count": 0,
            "profitability_sales_total": Decimal("80"),
            "actual_cost_total": Decimal("48"),
        },
        eligible_line_count=2,
        cost_table_available=True,
    )
    assert result["actual_gross_profit"] == "32"
    query = _profitability_query()
    assert "AND NOT sl.is_cancellation_candidate" in query
    assert "signed_quantity > 0" not in query


@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        ({"cost_missing_line_count": 1}, "unavailable_missing_costs"),
        ({"cost_ambiguous_line_count": 1}, "unavailable_ambiguous_costs"),
        ({"cost_missing_product_or_date_line_count": 1}, "unavailable_missing_product_or_date"),
    ],
)
def test_missing_ambiguous_or_unmatchable_costs_are_unavailable(overrides, status):
    stats = {
        "profitability_eligible_line_count": 1,
        "cost_matched_line_count": 0,
        "cost_missing_line_count": 0,
        "cost_ambiguous_line_count": 0,
        "cost_missing_product_or_date_line_count": 0,
        "profitability_sales_total": None,
        "actual_cost_total": None,
        **overrides,
    }
    result = _profitability_fields(
        stats,
        eligible_line_count=1,
        cost_table_available=True,
    )
    assert result["profitability_status"] == status
    assert result["actual_cost_total"] is None
    assert result["actual_gross_profit"] is None
    assert result["actual_gross_margin"] is None


def test_missing_cost_table_and_no_eligible_lines_have_explicit_status():
    missing_table = _profitability_fields(
        None,
        eligible_line_count=5,
        cost_table_available=False,
    )
    no_lines = _profitability_fields(
        {"profitability_eligible_line_count": 0},
        eligible_line_count=0,
        cost_table_available=True,
    )
    assert missing_table["profitability_status"] == "unavailable_cost_table_missing"
    assert no_lines["profitability_status"] == "unavailable_no_eligible_lines"
    assert missing_table["actual_gross_profit"] is None


def test_signed_return_costs_reduce_net_totals_and_zero_denominator_margin_is_null():
    # A sale of 100 with cost 60 and a signed return of -100 with cost -60 net to zero.
    result = _profitability_fields(
        {
            "profitability_eligible_line_count": 2,
            "cost_matched_line_count": 2,
            "cost_missing_line_count": 0,
            "cost_ambiguous_line_count": 0,
            "cost_missing_product_or_date_line_count": 0,
            "profitability_sales_total": Decimal("0"),
            "actual_cost_total": Decimal("0"),
        },
        eligible_line_count=2,
        cost_table_available=True,
    )
    assert result["actual_gross_profit"] == "0"
    assert result["actual_gross_margin"] is None


def test_overview_api_exposes_verified_profitability_shape_without_simulation(monkeypatch):
    from app.main import app

    payload = {
        "profitability_status": "unavailable_missing_costs",
        "actual_cost_total": None,
        "actual_gross_profit": None,
        "actual_gross_margin": None,
    }
    monkeypatch.setattr(real_retail_analytics, "overview", lambda _connection: payload)
    monkeypatch.setitem(app.dependency_overrides, get_real_retail_connection, lambda: object())
    response = TestClient(app).get("/api/real-retail/analytics/overview")
    assert response.status_code == 200
    assert response.json() == payload


def test_products_api_keeps_product_identifier_and_verified_cost_status(monkeypatch):
    from app.main import app

    product = {
        "source_product_key": "00123",
        "source_description": "Sample item",
        "profitability_status": "unavailable_missing_costs",
        "actual_cost_total": None,
        "actual_gross_profit": None,
        "actual_gross_margin": None,
    }
    monkeypatch.setattr(real_retail_analytics, "products", lambda *_args: [product])
    monkeypatch.setitem(app.dependency_overrides, get_real_retail_connection, lambda: object())
    response = TestClient(app).get("/api/real-retail/analytics/products?limit=1")
    assert response.status_code == 200
    assert response.json()[0]["source_product_key"] == "00123"
    assert response.json()[0]["profitability_status"] == "unavailable_missing_costs"
    assert "estimated_gross_profit" not in response.json()[0]


def test_product_actual_profit_is_only_returned_for_full_cost_match_coverage(monkeypatch):
    products = [
        {
            "source_product_key": "P1",
            "line_count": 2,
            "profitability_eligible_line_count": 2,
            "monetary_total": "80",
        },
        {
            "source_product_key": "P2",
            "line_count": 1,
            "profitability_eligible_line_count": 1,
            "monetary_total": "10",
        },
    ]
    cost_summaries = [
        {
            "source_product_key": "P1",
            "profitability_eligible_line_count": 2,
            "cost_matched_line_count": 2,
            "cost_missing_line_count": 0,
            "cost_ambiguous_line_count": 0,
            "cost_missing_product_or_date_line_count": 0,
            "profitability_sales_total": "80.00",
            "actual_cost_total": "48.00",
        },
        {
            "source_product_key": "P2",
            "profitability_eligible_line_count": 1,
            "cost_matched_line_count": 0,
            "cost_missing_line_count": 1,
            "cost_ambiguous_line_count": 0,
            "cost_missing_product_or_date_line_count": 0,
            "profitability_sales_total": None,
            "actual_cost_total": None,
        },
    ]
    monkeypatch.setattr(real_retail_analytics, "_product_cost_table_available", lambda _connection: True)
    monkeypatch.setattr(real_retail_analytics, "_profitability_rows", lambda *_args, **_kwargs: cost_summaries)
    monkeypatch.setattr(real_retail_analytics, "_rows", lambda *_args, **_kwargs: products)

    result = real_retail_analytics.products(None, "value", 10, 0)

    assert result[0]["profitability_status"] == "available"
    assert result[0]["actual_gross_profit"] == "32.00"
    assert result[1]["profitability_status"] == "unavailable_missing_costs"
    assert result[1]["actual_gross_profit"] is None


def test_each_monetary_analytics_endpoint_adds_gbp_metadata(monkeypatch):
    exact_value = "19287250.568000000000"
    monkeypatch.setattr(real_retail_analytics, "_product_cost_table_available", lambda _connection: False)
    monkeypatch.setattr(
        real_retail_analytics,
        "_rows",
        lambda *_args, **_kwargs: [{
            "monetary_total": exact_value,
            "source_product_key": "P1",
            "profitability_eligible_line_count": 0,
        }],
    )
    responses = [
        real_retail_analytics.monthly(None, None, None),
        real_retail_analytics.countries(None, "value", 1),
        real_retail_analytics.products(None, "value", 1, 0),
        real_retail_analytics.customers(None, "value", 1, 0),
    ]
    assert all(row[0]["currency_code"] == "GBP" for row in responses)
    assert all(row[0]["monetary_total"] == exact_value for row in responses)


def test_real_product_search_uses_bound_case_insensitive_description_filter(monkeypatch):
    captured = {}
    monkeypatch.setattr(real_retail_analytics, "_product_cost_table_available", lambda _connection: False)

    def fake_rows(_connection, query, params):
        captured["query"] = query
        captured["params"] = params
        return [{
            "source_description": "Tea cup",
            "monetary_total": "12.345678",
            "source_product_key": "P1",
            "profitability_eligible_line_count": 1,
        }]

    monkeypatch.setattr(real_retail_analytics, "_rows", fake_rows)
    result = real_retail_analytics.products(None, "value", 10, 0, " cup ")
    assert "source_description ILIKE :search" in captured["query"]
    assert "HAVING COUNT(*) FILTER" in captured["query"]
    assert "GROUP BY source_product_key" in captured["query"]
    assert "[[:alnum:]]" in captured["query"]
    assert captured["params"]["search"] == "%cup%"
    assert result[0]["source_description"] == "Tea cup"
    assert result[0]["monetary_total"] == "12.345678"


def test_customer_cluster_monetary_values_keep_exact_decimal_strings(monkeypatch):
    customer_rows = [
        {"customer_id": "100", "invoice_count": 2, "line_count": 3, "signed_quantity": -5, "monetary_value": "100.1000000000"},
        {"customer_id": "200", "invoice_count": 3, "line_count": 8, "signed_quantity": 10, "monetary_value": "0.0000000001"},
    ]

    def rows(_connection, query, _params=None):
        return [{"count": 0}] if "COUNT(*) AS count" in query else customer_rows

    monkeypatch.setattr(real_retail_analytics, "_rows", rows)
    result = real_retail_analytics.customer_clusters(None, 2)
    assert {customer["monetary_value"] for customer in result["customers"]} == {
        "100.1000000000",
        "0.0000000001",
    }
    assert sum((Decimal(group["total_monetary_value"]) for group in result["clusters"]), Decimal(0)) == Decimal("100.1000000001")


def test_real_basket_rules_resolve_product_descriptions_without_losing_ids(monkeypatch):
    queries = []

    def rows(_connection, query, _params=None):
        queries.append(query)
        if "AS transaction_count" in query:
            return [{"transaction_count": 2, "multi_item_transaction_count": 2}]
        if "AS transaction_id" in query:
            return [
                {"transaction_id": "I1", "product_id": "001"},
                {"transaction_id": "I1", "product_id": "002"},
                {"transaction_id": "I2", "product_id": "001"},
                {"transaction_id": "I2", "product_id": "002"},
            ]
        return [
            {"product_id": "001", "product_name": "A real product"},
            {"product_id": "002", "product_name": "Another valid name!"},
        ]

    monkeypatch.setattr(real_retail_analytics, "_rows", rows)
    result = real_retail_analytics.baskets(None, 0.1, 0.2)
    assert result["product_names"] == {
        "001": "A real product",
        "002": "Another valid name!",
    }
    assert result["rules"]
    assert {value for rule in result["rules"] for value in rule["antecedents"] + rule["consequents"]} == {"001", "002"}
    assert all("[[:alnum:]]" in query for query in queries)


def test_demand_keeps_source_aggregation_but_omits_invalid_product_labels(monkeypatch):
    rows = []
    for product_id, product_name in (
        ("valid", "Tea & cup!"),
        ("invalid", "??"),
        ("punctuation", "!!??"),
        ("missing", None),
        ("placeholder", "[missing description]"),
    ):
        for month, quantity in (("2010-01-01", 1), ("2010-02-01", 2), ("2010-03-01", 3), ("2010-04-01", 4)):
            rows.append({
                "product_id": product_id,
                "product_name": product_name,
                "period": month,
                "invoice_count": 1,
                "units_sold": quantity,
            })

    captured = {}

    def fake_rows(_connection, query, _params=None):
        captured["query"] = query
        return rows

    monkeypatch.setattr(real_retail_analytics, "_rows", fake_rows)
    result = real_retail_analytics.demand(None)

    assert "LEFT JOIN product_labels USING (product_id)" in captured["query"]
    assert "monthly AS" in captured["query"]
    assert "[[:alnum:]]" in captured["query"]
    assert len(result["predictions"]) == 1
    assert result["predictions"][0]["product_id"] == "valid"
    assert result["predictions"][0]["product_name"] == "Tea & cup!"


def test_demand_api_never_returns_missing_or_placeholder_product_descriptions(monkeypatch):
    rows = []
    for product_id, product_name in (
        ("valid", "Tea & cup!"),
        ("missing", None),
        ("placeholder", "[missing description]"),
        ("question", "??"),
    ):
        for month, quantity in (("2010-01-01", 1), ("2010-02-01", 2), ("2010-03-01", 3), ("2010-04-01", 4)):
            rows.append({
                "product_id": product_id,
                "product_name": product_name,
                "period": month,
                "invoice_count": 1,
                "units_sold": quantity,
            })

    monkeypatch.setattr(real_retail_analytics, "_rows", lambda *_args, **_kwargs: rows)
    from app.db.real_retail import get_real_retail_connection
    from app.main import app

    previous_override = app.dependency_overrides.get(get_real_retail_connection)
    app.dependency_overrides[get_real_retail_connection] = lambda: object()
    try:
        with TestClient(app) as client:
            response = client.get("/api/real-retail/features/demand")
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_real_retail_connection, None)
        else:
            app.dependency_overrides[get_real_retail_connection] = previous_override

    assert response.status_code == 200
    predictions = response.json()["predictions"]
    assert predictions
    assert {row["product_id"] for row in predictions} == {"valid"}
    assert {row["product_name"] for row in predictions} == {"Tea & cup!"}


def test_api_reads_disposable_real_retail_database_only():
    raw_url = os.environ.get("REAL_RETAIL_ANALYTICS_DATABASE_URL")
    if not raw_url:
        pytest.skip("REAL_RETAIL_ANALYTICS_DATABASE_URL is not configured")
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        overview = client.get("/api/real-retail/analytics/overview")
        monthly = client.get("/api/real-retail/analytics/monthly")
        product = client.get("/api/real-retail/analytics/products?limit=1")
        customer = client.get("/api/real-retail/analytics/customers?limit=1")
        clusters = client.get("/api/real-retail/features/clustering?n_clusters=2")
    assert all(response.status_code == 200 for response in (overview, monthly, product, customer, clusters))
    overview_payload = overview.json()
    assert overview_payload["total_sales_lines"] == 1_067_371
    assert overview_payload["source_monetary_total"] == "19287250.568000000000"
    assert overview_payload["currency_code"] == "GBP"
    assert monthly.json()[0]["currency_code"] == "GBP"
    assert product.json()[0]["currency_code"] == "GBP"
    assert isinstance(product.json()[0]["monetary_total"], str)
    assert customer.json()[0]["currency_code"] == "GBP"
    assert clusters.json()["currency_code"] == "GBP"
    assert isinstance(clusters.json()["customers"][0]["monetary_value"], str)
