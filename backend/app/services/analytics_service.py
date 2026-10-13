from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session


def _serialize(rows):
    result = []
    for row in rows:
        item = {}
        for key, value in dict(row).items():
            if isinstance(value, Decimal):
                item[key] = float(value)
            elif isinstance(value, (date,)):
                item[key] = value.isoformat()
            else:
                item[key] = value
        result.append(item)
    return result


def _query(db: Session, sql: str, params: dict | None = None):
    rows = db.execute(text(sql), params or {}).mappings().all()
    return _serialize(rows)


def get_summary(db: Session):
    rows = _query(
        db,
        """
        SELECT
            COUNT(DISTINCT transaction_id) AS total_transactions,
            COALESCE(SUM(quantity), 0) AS units_sold,
            COALESCE(SUM(total_amount), 0) AS total_revenue,
            COALESCE(SUM(profit), 0) AS total_profit,
            COALESCE(
                SUM(total_amount) / NULLIF(COUNT(DISTINCT transaction_id), 0),
                0
            ) AS average_order_value
        FROM fact_sales
        """,
    )
    return rows[0]


def get_monthly_sales(db: Session):
    return _query(
        db,
        """
        SELECT
            TO_CHAR(d.full_date, 'YYYY-MM') AS month,
            SUM(f.total_amount) AS revenue,
            SUM(f.profit) AS profit,
            SUM(f.quantity) AS units_sold,
            COUNT(DISTINCT f.transaction_id) AS transactions
        FROM fact_sales f
        JOIN dim_date d ON d.date_id = f.date_id
        GROUP BY DATE_TRUNC('month', d.full_date),
                 TO_CHAR(d.full_date, 'YYYY-MM')
        ORDER BY DATE_TRUNC('month', d.full_date)
        """,
    )


def get_sales_by_category(db: Session):
    return _query(
        db,
        """
        SELECT
            p.category,
            SUM(f.total_amount) AS revenue,
            SUM(f.profit) AS profit,
            SUM(f.quantity) AS units_sold
        FROM fact_sales f
        JOIN dim_product p ON p.product_id = f.product_id
        GROUP BY p.category
        ORDER BY revenue DESC
        """,
    )


def get_sales_by_region(db: Session):
    return _query(
        db,
        """
        SELECT
            s.region,
            SUM(f.total_amount) AS revenue,
            SUM(f.profit) AS profit,
            SUM(f.quantity) AS units_sold
        FROM fact_sales f
        JOIN dim_store s ON s.store_id = f.store_id
        GROUP BY s.region
        ORDER BY revenue DESC
        """,
    )


def get_top_products(db: Session, limit: int = 10):
    return _query(
        db,
        """
        SELECT
            p.product_id,
            p.product_name,
            p.category,
            SUM(f.quantity) AS units_sold,
            SUM(f.total_amount) AS revenue,
            SUM(f.profit) AS profit
        FROM fact_sales f
        JOIN dim_product p ON p.product_id = f.product_id
        GROUP BY p.product_id, p.product_name, p.category
        ORDER BY revenue DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )