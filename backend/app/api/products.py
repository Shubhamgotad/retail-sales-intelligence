from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(prefix="/api/products", tags=["Products"])


@router.get("")
def list_products(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    rows = db.execute(text("""
        SELECT p.product_id, p.product_name, p.category, p.subcategory,
               p.brand, p.unit_cost, p.unit_price,
               COALESCE(SUM(f.quantity), 0) AS units_sold,
               COALESCE(SUM(f.total_amount), 0) AS revenue,
               COALESCE(SUM(f.profit), 0) AS profit
        FROM dim_product p
        LEFT JOIN fact_sales f ON f.product_id = p.product_id
        GROUP BY p.product_id, p.product_name, p.category, p.subcategory,
                 p.brand, p.unit_cost, p.unit_price
        ORDER BY p.product_name
        LIMIT :limit
    """), {"limit": limit}).mappings().all()

    return [dict(row) for row in rows]