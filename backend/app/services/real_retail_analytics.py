"""Read-only analytics over the isolated ``real_retail`` schema."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
import pandas as pd
from collections import Counter
from itertools import combinations
from sklearn.cluster import KMeans
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger("uvicorn.error")

SORT_FIELDS = {
    "lines": "line_count",
    "invoices": "invoice_count",
    "quantity": "quantity_total",
    "value": "monetary_total",
}
CURRENCY_CODE = "GBP"
UCI_DATASET_URL = "https://archive.ics.uci.edu/dataset/502/online+retail+ii"
_VALID_PRODUCT_DESCRIPTION = (
    "regexp_replace(COALESCE(source_description, ''), '[[:space:]]+', '', 'g') "
    "~ '[[:alnum:]]' AND "
    "LOWER(regexp_replace(COALESCE(source_description, ''), '[[:space:]]+', '', 'g')) "
    "<> '[missingdescription]'"
)
_PROFITABILITY_ELIGIBILITY = (
    "signed_quantity IS NOT NULL AND source_price IS NOT NULL "
    "AND NOT is_cancellation_candidate"
)
_DIAGNOSTIC_URL = re.compile(
    r"\b(?:postgres(?:ql)?(?:\+[A-Za-z0-9_]+)?|https?)://\S+",
    re.IGNORECASE,
)
_DIAGNOSTIC_CREDENTIAL = re.compile(
    r"\b(?:password|passwd|pwd|username|user|token|secret|api[_-]?key)"
    r"\s*[=:]\s*[^,\s;]+",
    re.IGNORECASE,
)
_DIAGNOSTIC_INPUT_VALUE = re.compile(
    r"(invalid input (?:syntax for type|value for enum)[^:]*:\s*).*$",
    re.IGNORECASE,
)
_DIAGNOSTIC_DUPLICATE_VALUE = re.compile(
    r"(\bKey\s*\([^)]*\)\s*=\s*)\([^)]*\)",
    re.IGNORECASE,
)
_DIAGNOSTIC_DETAIL = re.compile(r"\b(?:DETAIL|CONTEXT):.*$", re.IGNORECASE)


def is_valid_product_description(description: Any) -> bool:
    if not isinstance(description, str):
        return False
    normalized = "".join(description.split())
    return (
        normalized.casefold() != "[missingdescription]"
        and any(character.isalnum() for character in normalized)
    )


def _database_error_diagnostic(error: SQLAlchemyError) -> tuple[str, str, str]:
    """Extract and sanitize only the driver class, SQLSTATE, and primary message."""
    original = getattr(error, "orig", None)
    exception_class = type(original if original is not None else error).__name__
    raw_sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    sqlstate = (
        raw_sqlstate
        if isinstance(raw_sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", raw_sqlstate)
        else "none"
    )
    diagnostic = getattr(original, "diag", None)
    primary_message = getattr(diagnostic, "message_primary", None)
    if not isinstance(primary_message, str) or not primary_message.strip():
        primary_message = "none"
    else:
        primary_message = _DIAGNOSTIC_DETAIL.sub("[redacted]", primary_message.strip())
        primary_message = _DIAGNOSTIC_INPUT_VALUE.sub(r"\1[redacted]", primary_message)
        primary_message = _DIAGNOSTIC_DUPLICATE_VALUE.sub(
            r"\1([redacted])",
            primary_message,
        )
        primary_message = _DIAGNOSTIC_URL.sub("[redacted-url]", primary_message)
        primary_message = _DIAGNOSTIC_CREDENTIAL.sub("[redacted-credential]", primary_message)
        primary_message = " ".join(primary_message.split())[:500] or "none"
    return exception_class, sqlstate, primary_message


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _rows(connection: Connection, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        return [
            {key: _json_value(value) for key, value in dict(row).items()}
            for row in connection.execute(text(query), params or {}).mappings().all()
        ]
    except SQLAlchemyError as error:
        exception_class, sqlstate, primary_message = _database_error_diagnostic(error)
        logger.error(
            "Real-retail database diagnostic: exception_class=%s sqlstate=%s "
            "primary_message=%s",
            exception_class,
            sqlstate,
            primary_message,
        )
        raise RuntimeError("Real-retail analytics schema is unavailable") from error


def _currency_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**row, "currency_code": CURRENCY_CODE} for row in rows]


def _product_cost_table_available(connection: Connection) -> bool:
    try:
        return connection.execute(
            text("SELECT to_regclass('real_retail.product_unit_costs')")
        ).scalar_one() is not None
    except SQLAlchemyError as error:
        raise RuntimeError("Could not inspect verified product-cost availability") from error


def _profitability_query(*, by_product: bool = False) -> str:
    group_clause = "GROUP BY source_product_key" if by_product else ""
    product_column = "source_product_key," if by_product else ""
    return f"""
        WITH matched_lines AS (
            SELECT sl.real_sales_line_id,
                   sl.source_product_key,
                   sl.invoice_datetime,
                   sl.signed_quantity,
                   sl.source_price,
                   COUNT(cost.product_unit_cost_id) AS cost_match_count,
                   MIN(cost.unit_cost) AS matched_unit_cost
            FROM real_retail.sales_lines AS sl
            LEFT JOIN real_retail.product_unit_costs AS cost
              ON cost.source_product_key = sl.source_product_key
             AND cost.currency_code = :currency_code
             AND sl.invoice_datetime IS NOT NULL
             AND sl.invoice_datetime::date >= cost.effective_from
             AND (cost.effective_to IS NULL
                  OR sl.invoice_datetime::date <= cost.effective_to)
            WHERE sl.signed_quantity IS NOT NULL
              AND sl.source_price IS NOT NULL
              AND NOT sl.is_cancellation_candidate
            GROUP BY sl.real_sales_line_id, sl.source_product_key,
                     sl.invoice_datetime, sl.signed_quantity, sl.source_price
        )
        SELECT {product_column}
               COUNT(*) AS profitability_eligible_line_count,
               COUNT(*) FILTER (
                   WHERE source_product_key IS NULL OR invoice_datetime IS NULL
               ) AS cost_missing_product_or_date_line_count,
               COUNT(*) FILTER (
                   WHERE source_product_key IS NOT NULL
                     AND invoice_datetime IS NOT NULL
                     AND cost_match_count = 0
               ) AS cost_missing_line_count,
               COUNT(*) FILTER (WHERE cost_match_count > 1)
                   AS cost_ambiguous_line_count,
               COUNT(*) FILTER (WHERE cost_match_count = 1)
                   AS cost_matched_line_count,
               CASE
                   WHEN COUNT(*) > 0
                    AND COUNT(*) FILTER (WHERE cost_match_count = 1) = COUNT(*)
                   THEN SUM(signed_quantity * source_price)
               END AS profitability_sales_total,
               CASE
                   WHEN COUNT(*) > 0
                    AND COUNT(*) FILTER (WHERE cost_match_count = 1) = COUNT(*)
                   THEN SUM(signed_quantity * matched_unit_cost)
               END AS actual_cost_total
        FROM matched_lines
        {group_clause}
    """


def _profitability_fields(
    row: dict[str, Any] | None,
    *,
    eligible_line_count: int,
    cost_table_available: bool,
) -> dict[str, Any]:
    if not cost_table_available:
        return {
            "profitability_status": "unavailable_cost_table_missing",
            "profitability_eligible_line_count": eligible_line_count,
            "cost_matched_line_count": 0,
            "cost_missing_line_count": 0,
            "cost_ambiguous_line_count": 0,
            "cost_missing_product_or_date_line_count": 0,
            "profitability_sales_total": None,
            "actual_cost_total": None,
            "actual_gross_profit": None,
            "actual_gross_margin": None,
        }

    stats = row or {}
    counts = {
        field: int(stats.get(field, 0) or 0)
        for field in (
            "profitability_eligible_line_count",
            "cost_matched_line_count",
            "cost_missing_line_count",
            "cost_ambiguous_line_count",
            "cost_missing_product_or_date_line_count",
        )
    }
    if counts["profitability_eligible_line_count"] == 0:
        status = "unavailable_no_eligible_lines"
    elif counts["cost_missing_product_or_date_line_count"]:
        status = "unavailable_missing_product_or_date"
    elif counts["cost_ambiguous_line_count"]:
        status = "unavailable_ambiguous_costs"
    elif counts["cost_missing_line_count"]:
        status = "unavailable_missing_costs"
    elif counts["cost_matched_line_count"] != counts["profitability_eligible_line_count"]:
        status = "unavailable_incomplete_cost_coverage"
    else:
        status = "available"

    sales = stats.get("profitability_sales_total")
    cost = stats.get("actual_cost_total")
    if status != "available" or sales is None or cost is None:
        return {
            **counts,
            "profitability_status": status,
            "profitability_sales_total": None,
            "actual_cost_total": None,
            "actual_gross_profit": None,
            "actual_gross_margin": None,
        }

    sales_decimal = Decimal(str(sales))
    cost_decimal = Decimal(str(cost))
    profit = sales_decimal - cost_decimal
    margin = profit / sales_decimal * Decimal(100) if sales_decimal else None
    return {
        **counts,
        "profitability_status": status,
        "profitability_sales_total": str(sales_decimal),
        "actual_cost_total": str(cost_decimal),
        "actual_gross_profit": str(profit),
        "actual_gross_margin": str(margin) if margin is not None else None,
    }


def _profitability_rows(
    connection: Connection,
    *,
    by_product: bool = False,
) -> list[dict[str, Any]]:
    return _rows(
        connection,
        _profitability_query(by_product=by_product),
        {"currency_code": CURRENCY_CODE},
    )


def _date_params(start_date: date | None, end_date: date | None) -> dict[str, Any]:
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    return {"start_date": start_date, "end_date": end_date}


def overview(connection: Connection) -> dict[str, Any]:
    rows = _rows(
        connection,
        f"""
        SELECT COUNT(*) AS total_sales_lines,
               COUNT(DISTINCT source_invoice) FILTER (WHERE source_invoice IS NOT NULL)
                   AS distinct_invoices,
               COUNT(DISTINCT source_product_key)
                   FILTER (WHERE source_product_key IS NOT NULL) AS distinct_products,
               COUNT(DISTINCT source_customer_key)
                   FILTER (WHERE source_customer_key IS NOT NULL) AS distinct_customers,
               COUNT(*) FILTER (WHERE source_customer_key IS NULL) AS missing_customer_lines,
               COALESCE(SUM(signed_quantity) FILTER (WHERE source_customer_key IS NULL), 0)
                   AS missing_customer_quantity_total,
               COALESCE(SUM(signed_quantity * source_price)
                   FILTER (WHERE source_customer_key IS NULL), 0)
                   AS missing_customer_monetary_total,
               COALESCE(SUM(signed_quantity), 0) AS signed_quantity_total,
               COALESCE(SUM(signed_quantity * source_price), 0) AS source_monetary_total,
               COUNT(*) FILTER (
                   WHERE signed_quantity IS NULL OR source_price IS NULL
               ) AS missing_value_lines,
               COUNT(*) FILTER (WHERE source_invoice IS NULL) AS missing_invoice_lines,
               COUNT(*) FILTER (WHERE is_cancellation_candidate) AS cancellation_candidates,
               COUNT(*) FILTER (WHERE is_negative_quantity_candidate) AS potential_returns,
               COUNT(*) FILTER (WHERE is_negative_price) AS negative_price_anomalies,
               COUNT(*) FILTER (WHERE is_zero_price) AS zero_price_lines,
               COUNT(*) FILTER (WHERE is_missing_description) AS missing_descriptions,
               COUNT(*) FILTER (WHERE is_missing_customer) AS missing_customer_ids,
               COUNT(*) FILTER (WHERE is_possible_duplicate) AS duplicate_content_rows,
               COUNT(*) FILTER (WHERE {_PROFITABILITY_ELIGIBILITY})
                   AS profitability_eligible_line_count
        FROM real_retail.sales_lines
        """,
    )
    result = rows[0]
    cost_table_available = _product_cost_table_available(connection)
    if cost_table_available:
        cost_rows = _profitability_rows(connection)
        profitability = _profitability_fields(
            cost_rows[0],
            eligible_line_count=int(result["profitability_eligible_line_count"]),
            cost_table_available=True,
        )
    else:
        profitability = _profitability_fields(
            None,
            eligible_line_count=int(result["profitability_eligible_line_count"]),
            cost_table_available=False,
        )
    result.update(profitability)
    result["currency_code"] = CURRENCY_CODE
    result["warnings"] = [
        f"The UCI Online Retail II documentation defines UnitPrice in sterling (GBP); no currency conversion is applied. See {UCI_DATASET_URL}.",
        "Source monetary values are not verified revenue or profit.",
        "Product-profitability totals exclude C-prefixed cancellation candidates using the documented invoice convention; no separate cancellation confirmation is present.",
        "Other negative quantities retain their signed values as potential return adjustments; they are not confirmed returns.",
        "Actual cost and gross profit are reported only when exactly one verified cost matches every included line by product, transaction date, and GBP currency; currencies are never converted.",
    ]
    if result["profitability_status"] != "available":
        result["warnings"].append(
            f"Actual profitability is unavailable: {result['profitability_status']}."
        )
    return result


def monthly(
    connection: Connection,
    start_date: date | None,
    end_date: date | None,
) -> list[dict[str, Any]]:
    return _currency_rows(_rows(
        connection,
        """
        SELECT TO_CHAR(DATE_TRUNC('month', invoice_datetime), 'YYYY-MM') AS month,
               COUNT(*) AS line_count,
               COUNT(DISTINCT source_invoice) FILTER (WHERE source_invoice IS NOT NULL)
                   AS invoice_count,
               COALESCE(SUM(signed_quantity), 0) AS signed_quantity_total,
               COALESCE(SUM(signed_quantity * source_price), 0) AS source_monetary_total,
               COUNT(*) FILTER (WHERE is_cancellation_candidate) AS cancellation_lines,
               COALESCE(SUM(signed_quantity * source_price)
                   FILTER (WHERE is_cancellation_candidate), 0) AS cancellation_monetary_total,
               COUNT(*) FILTER (WHERE is_negative_quantity_candidate) AS potential_return_lines,
               COALESCE(SUM(signed_quantity * source_price)
                   FILTER (WHERE is_negative_quantity_candidate), 0) AS potential_return_monetary_total,
               COUNT(*) FILTER (
                   WHERE signed_quantity IS NULL OR source_price IS NULL
               ) AS missing_value_lines
        FROM real_retail.sales_lines
        WHERE (CAST(:start_date AS DATE) IS NULL
               OR invoice_datetime::date >= CAST(:start_date AS DATE))
          AND (CAST(:end_date AS DATE) IS NULL
               OR invoice_datetime::date <= CAST(:end_date AS DATE))
        GROUP BY DATE_TRUNC('month', invoice_datetime)
        ORDER BY DATE_TRUNC('month', invoice_datetime)
        """,
        _date_params(start_date, end_date),
    ))


def countries(
    connection: Connection,
    sort: str,
    limit: int,
) -> list[dict[str, Any]]:
    order = SORT_FIELDS[sort]
    return _currency_rows(_rows(
        connection,
        f"""
        SELECT COALESCE(NULLIF(BTRIM(source_country), ''), '[missing]') AS country,
               COUNT(*) AS line_count,
               COUNT(DISTINCT source_invoice) FILTER (WHERE source_invoice IS NOT NULL)
                   AS invoice_count,
               COALESCE(SUM(signed_quantity), 0) AS quantity_total,
               COALESCE(SUM(signed_quantity * source_price), 0) AS monetary_total
        FROM real_retail.sales_lines
        GROUP BY COALESCE(NULLIF(BTRIM(source_country), ''), '[missing]')
        ORDER BY {order} DESC, country ASC
        LIMIT :limit
        """,
        {"limit": limit},
    ))


def products(
    connection: Connection,
    sort: str,
    limit: int,
    offset: int,
    search: str | None = None,
) -> list[dict[str, Any]]:
    order = SORT_FIELDS[sort]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    search_filter = ""
    description_filter = ""
    if search and search.strip():
        search_filter = (
            f"AND COUNT(*) FILTER (WHERE {_VALID_PRODUCT_DESCRIPTION} "
            "AND source_description ILIKE :search) > 0"
        )
        description_filter = " AND source_description ILIKE :search"
        params["search"] = f"%{search.strip()}%"
    result_rows = _rows(
        connection,
        f"""
        SELECT source_product_key,
               MAX(source_description) FILTER (
                   WHERE {_VALID_PRODUCT_DESCRIPTION}{description_filter}
               )
                   AS source_description,
               COUNT(*) AS line_count,
               COUNT(*) FILTER (WHERE is_missing_description) AS missing_description_lines,
               COUNT(*) FILTER (WHERE is_negative_quantity_candidate) AS potential_return_lines,
               COUNT(*) FILTER (WHERE is_zero_price) AS zero_price_lines,
               COALESCE(SUM(signed_quantity), 0) AS quantity_total,
               COALESCE(SUM(signed_quantity * source_price), 0) AS monetary_total,
               COUNT(*) FILTER (WHERE {_PROFITABILITY_ELIGIBILITY})
                   AS profitability_eligible_line_count
        FROM real_retail.sales_lines
        GROUP BY source_product_key
        HAVING COUNT(*) FILTER (WHERE {_VALID_PRODUCT_DESCRIPTION}) > 0
        {search_filter}
        ORDER BY {order} DESC, source_product_key NULLS LAST
        LIMIT :limit OFFSET :offset
        """,
        params,
    )
    cost_table_available = _product_cost_table_available(connection)
    cost_rows = (
        _profitability_rows(connection, by_product=True)
        if cost_table_available
        else []
    )
    cost_by_product = {
        row["source_product_key"]: row
        for row in cost_rows
    }
    for row in result_rows:
        row.update(
            _profitability_fields(
                cost_by_product.get(row["source_product_key"]),
                eligible_line_count=int(row["profitability_eligible_line_count"]),
                cost_table_available=cost_table_available,
            )
        )
    return _currency_rows(result_rows)


def customers(connection: Connection, sort: str, limit: int, offset: int) -> list[dict[str, Any]]:
    order = SORT_FIELDS[sort]
    return _currency_rows(_rows(
        connection,
        f"""
        SELECT source_customer_key,
               COUNT(DISTINCT source_invoice) FILTER (WHERE source_invoice IS NOT NULL)
                   AS invoice_count,
               COUNT(*) AS line_count,
               COALESCE(SUM(signed_quantity), 0) AS quantity_total,
               COALESCE(SUM(signed_quantity * source_price), 0) AS monetary_total
        FROM real_retail.sales_lines
        WHERE source_customer_key IS NOT NULL
        GROUP BY source_customer_key
        ORDER BY {order} DESC, source_customer_key ASC
        LIMIT :limit OFFSET :offset
        """,
        {"limit": limit, "offset": offset},
    ))


def quality(connection: Connection) -> dict[str, Any]:
    result = overview(connection)
    total = int(result["total_sales_lines"])
    counts = {
        "missing_customer_ids": result["missing_customer_ids"],
        "missing_descriptions": result["missing_descriptions"],
        "negative_quantity_candidates": result["potential_returns"],
        "cancellation_candidates": result["cancellation_candidates"],
        "zero_price_lines": result["zero_price_lines"],
        "negative_price_anomalies": result["negative_price_anomalies"],
        "duplicate_content_rows": result["duplicate_content_rows"],
    }
    return {
        "total_sales_lines": total,
        "observed_counts": counts,
        "proportions": {
            key: (str(Decimal(value) / Decimal(total)) if total else "0")
            for key, value in counts.items()
        },
        "warnings": result["warnings"],
    }


def result_warnings() -> list[str]:
    return [
        f"UCI Online Retail II documents UnitPrice in sterling (GBP); no currency conversion is applied. See {UCI_DATASET_URL}.",
        "Source monetary values are not verified revenue or profit.",
        "Cancellation and return flags are candidates, not confirmed business events.",
    ]


def demand(connection: Connection) -> dict[str, Any]:
    rows = _rows(connection, f"""
        WITH product_labels AS (
            SELECT source_product_key AS product_id,
                   MAX(source_description) FILTER (WHERE {_VALID_PRODUCT_DESCRIPTION})
                       AS product_name
            FROM real_retail.sales_lines
            WHERE source_product_key IS NOT NULL
            GROUP BY source_product_key
            HAVING COUNT(*) FILTER (WHERE {_VALID_PRODUCT_DESCRIPTION}) > 0
        ),
        monthly AS (
            SELECT source_product_key AS product_id,
                   DATE_TRUNC('month', invoice_datetime) AS period,
                   COUNT(DISTINCT source_invoice) FILTER (WHERE source_invoice IS NOT NULL)
                       AS invoice_count,
                   SUM(signed_quantity) AS units_sold
            FROM real_retail.sales_lines
            WHERE source_product_key IS NOT NULL
            GROUP BY source_product_key, DATE_TRUNC('month', invoice_datetime)
        )
        SELECT monthly.product_id, product_labels.product_name, monthly.period,
               monthly.invoice_count, monthly.units_sold
        FROM monthly
        LEFT JOIN product_labels USING (product_id)
        ORDER BY monthly.product_id, monthly.period
    """)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"predictions": [], "message": "No real product history found.", "source_warnings": result_warnings()}
    frame["period"] = pd.to_datetime(frame["period"])
    frame["units_sold"] = frame["units_sold"].astype(float)
    grouped = frame.groupby("product_id", group_keys=False)
    frame["previous_units"] = grouped["units_sold"].shift(1)
    frame["previous_invoices"] = grouped["invoice_count"].shift(1)
    frame["previous_3m_avg"] = grouped["units_sold"].transform(lambda values: values.shift(1).rolling(3, min_periods=1).mean())
    frame["target_units"] = grouped["units_sold"].shift(-1)
    features = ["previous_units", "previous_invoices", "previous_3m_avg"]
    train = frame.dropna(subset=features + ["target_units"]).copy()
    if len(train) < 6:
        return {"predictions": [], "message": "Not enough consecutive real-retail monthly history to train.", "source_warnings": result_warnings()}
    q1, q2 = train["target_units"].quantile([1 / 3, 2 / 3]).tolist()
    def label(value: float) -> str:
        return "low" if value <= q1 else "medium" if value <= q2 else "high"
    train["demand_class"] = train["target_units"].map(label)
    model = RandomForestClassifier(n_estimators=150, random_state=42, class_weight="balanced")
    model.fit(train[features], train["demand_class"])
    latest = frame.dropna(subset=features).sort_values("period").groupby("product_id", as_index=False).tail(1).copy()
    latest["predicted_demand"] = model.predict(latest[features])
    predictions = [
        {"product_id": str(row["product_id"]), "product_name": row["product_name"],
         "last_observed_month": row["period"].strftime("%Y-%m"), "last_month_units": float(row["units_sold"]),
         "predicted_demand": row["predicted_demand"]}
        for row in latest.sort_values("product_id").to_dict(orient="records")
        if is_valid_product_description(row["product_name"])
    ]
    return {
        "algorithm": "Random Forest", "target": "next-month signed-demand class",
        "training_rows": int(len(train)),
        "training_accuracy": round(float(accuracy_score(train["demand_class"], model.predict(train[features]))), 4),
        "class_thresholds_units": {"low_max": round(float(q1), 2), "medium_max": round(float(q2), 2), "high": "above medium_max"},
        "predictions": predictions,
        "note": "Classes use signed source quantities; negative quantities and cancellation candidates are retained.",
        "source_warnings": result_warnings(),
    }


def customer_clusters(connection: Connection, n_clusters: int) -> dict[str, Any]:
    rows = _rows(connection, """
        SELECT source_customer_key AS customer_id,
               COUNT(DISTINCT source_invoice) FILTER (WHERE source_invoice IS NOT NULL) AS invoice_count,
               COUNT(*) AS line_count, SUM(signed_quantity) AS signed_quantity,
               SUM(signed_quantity * source_price) AS monetary_value
        FROM real_retail.sales_lines
        WHERE source_customer_key IS NOT NULL
        GROUP BY source_customer_key
        ORDER BY source_customer_key
    """)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"clusters": [], "customers": [], "excluded_missing_customer_lines": 0, "currency_code": CURRENCY_CODE, "message": "No identifiable real-retail customers found."}
    features = ["invoice_count", "line_count", "signed_quantity", "monetary_value"]
    frame["monetary_value_exact"] = frame["monetary_value"].astype(str)
    frame[features] = frame[features].astype(float)
    k = min(max(1, n_clusters), len(frame))
    labels = [0] * len(frame) if k == 1 else KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(StandardScaler().fit_transform(frame[features]))
    frame["cluster"] = labels
    clusters = [{"cluster": int(label_value), "customer_count": len(group), "total_monetary_value": str(sum((Decimal(value) for value in group["monetary_value_exact"]), Decimal(0))), "total_signed_quantity": float(group["signed_quantity"].sum()), "customers": group["customer_id"].tolist()} for label_value, group in frame.groupby("cluster")]
    customers = [{"customer_id": str(row["customer_id"]), "cluster": int(row["cluster"]), "invoice_count": int(row["invoice_count"]), "line_count": int(row["line_count"]), "signed_quantity": float(row["signed_quantity"]), "monetary_value": row["monetary_value_exact"]} for row in frame.to_dict(orient="records")]
    missing = int(_rows(connection, "SELECT COUNT(*) AS count FROM real_retail.sales_lines WHERE source_customer_key IS NULL")[0]["count"])
    return {"algorithm": "K-Means", "features": features, "cluster_count": k, "excluded_missing_customer_lines": missing, "currency_code": CURRENCY_CODE, "clusters": clusters, "customers": customers, "note": "Clusters use identifiable source customers only; missing customer IDs are excluded and reported."}


def baskets(connection: Connection, min_support: float, min_confidence: float) -> dict[str, Any]:
    transaction_rows = _rows(connection, f"""
        WITH valid_products AS (
            SELECT source_product_key
            FROM real_retail.sales_lines
            WHERE source_product_key IS NOT NULL
            GROUP BY source_product_key
            HAVING COUNT(*) FILTER (WHERE {_VALID_PRODUCT_DESCRIPTION}) > 0
        ),
        filtered AS (
            SELECT DISTINCT sales_lines.source_invoice, sales_lines.source_product_key
            FROM real_retail.sales_lines
            JOIN valid_products USING (source_product_key)
            WHERE sales_lines.source_invoice IS NOT NULL
              AND sales_lines.source_invoice NOT LIKE 'C%'
              AND sales_lines.signed_quantity > 0
        )
        SELECT COUNT(DISTINCT source_invoice) AS transaction_count,
               COUNT(*) FILTER (WHERE product_count >= 2) AS multi_item_transaction_count
        FROM (
            SELECT source_invoice, COUNT(*) AS product_count
            FROM filtered
            GROUP BY source_invoice
        ) baskets
    """)
    transaction_count = int(transaction_rows[0]["transaction_count"])
    multi_item_transaction_count = int(transaction_rows[0]["multi_item_transaction_count"])
    if transaction_count == 0:
        return {"algorithm": "Apriori", "transaction_count": 0, "multi_item_transaction_count": 0, "frequent_itemsets": [], "rules": [], "message": "No qualifying positive-quantity baskets.", "policy": "Excluded cancellation-invoice candidates and non-positive quantities."}
    minimum_count = min_support * transaction_count
    rows = _rows(connection, f"""
        WITH valid_products AS (
            SELECT source_product_key
            FROM real_retail.sales_lines
            WHERE source_product_key IS NOT NULL
            GROUP BY source_product_key
            HAVING COUNT(*) FILTER (WHERE {_VALID_PRODUCT_DESCRIPTION}) > 0
        ),
        filtered AS (
            SELECT DISTINCT sales_lines.source_invoice, sales_lines.source_product_key
            FROM real_retail.sales_lines
            JOIN valid_products USING (source_product_key)
            WHERE sales_lines.source_invoice IS NOT NULL
              AND sales_lines.source_invoice NOT LIKE 'C%'
              AND sales_lines.signed_quantity > 0
        ),
        frequent_products AS (
            SELECT source_product_key
            FROM filtered
            GROUP BY source_product_key
            HAVING COUNT(*) >= :minimum_count
        )
        SELECT filtered.source_invoice AS transaction_id,
               filtered.source_product_key AS product_id
        FROM filtered
        JOIN frequent_products
          ON frequent_products.source_product_key = filtered.source_product_key
        ORDER BY filtered.source_invoice, filtered.source_product_key
    """, {"minimum_count": minimum_count})
    basket_map: dict[str, set[str]] = {}
    for row in rows:
        basket_map.setdefault(str(row["transaction_id"]), set()).add(str(row["product_id"]))
    transactions = [sorted(items) for items in basket_map.values() if len(items) >= 2]
    if not transactions:
        return {"algorithm": "Apriori", "transaction_count": len(basket_map), "multi_item_transaction_count": 0, "frequent_itemsets": [], "rules": [], "product_names": {}, "message": "No qualifying positive-quantity multi-item baskets.", "policy": "Excluded cancellation-invoice candidates and non-positive quantities."}
    item_counts: Counter[str] = Counter()
    pair_counts: Counter[tuple[str, str]] = Counter()
    for items in transactions:
        item_counts.update(items)
        pair_counts.update(combinations(items, 2))

    minimum_count = min_support * transaction_count
    frequent = [
        {"items": [item], "support": round(count / transaction_count, 4)}
        for item, count in sorted(item_counts.items())
        if count >= minimum_count
    ]
    rules: list[dict[str, Any]] = []
    for (left, right), count in pair_counts.items():
        if count < minimum_count:
            continue
        support = count / transaction_count
        right_support = item_counts[right] / transaction_count
        left_support = item_counts[left] / transaction_count
        left_confidence = count / item_counts[left]
        right_confidence = count / item_counts[right]
        if left_confidence >= min_confidence:
            rules.append({"antecedents": [left], "consequents": [right], "support": round(support, 4), "confidence": round(left_confidence, 4), "lift": round(left_confidence / right_support, 4)})
        if right_confidence >= min_confidence:
            rules.append({"antecedents": [right], "consequents": [left], "support": round(support, 4), "confidence": round(right_confidence, 4), "lift": round(right_confidence / left_support, 4)})
        frequent.append({"items": [left, right], "support": round(support, 4)})
    rules.sort(key=lambda rule: (-rule["confidence"], -rule["lift"], rule["antecedents"], rule["consequents"]))
    frequent.sort(key=lambda item: (-item["support"], item["items"]))
    rule_product_ids = {
        product_id
        for rule in rules[:50]
        for product_id in rule["antecedents"] + rule["consequents"]
    }
    name_rows = _rows(connection, f"""
        SELECT source_product_key AS product_id,
               MAX(source_description) FILTER (WHERE {_VALID_PRODUCT_DESCRIPTION})
                   AS product_name
        FROM real_retail.sales_lines
        WHERE source_product_key IS NOT NULL
        GROUP BY source_product_key
        HAVING COUNT(*) FILTER (WHERE {_VALID_PRODUCT_DESCRIPTION}) > 0
    """)
    product_names = {
        str(row["product_id"]): row["product_name"]
        for row in name_rows
        if str(row["product_id"]) in rule_product_ids
    }
    return {
        "algorithm": "Apriori",
        "transaction_count": len(basket_map),
        "multi_item_transaction_count": multi_item_transaction_count,
        "frequent_itemsets": frequent,
        "rules": rules[:50],
        "product_names": product_names,
        "policy": "Excluded cancellation-invoice candidates and non-positive quantities; product IDs are source identifiers. Exact singleton and pair counting is used to avoid an unbounded dense item matrix.",
    }
