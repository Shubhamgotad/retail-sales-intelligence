from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import analytics_service

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


@router.get("/summary")
def sales_summary(db: Session = Depends(get_db)):
    return analytics_service.get_summary(db)


@router.get("/monthly-sales")
def monthly_sales(db: Session = Depends(get_db)):
    return analytics_service.get_monthly_sales(db)


@router.get("/sales-by-category")
def sales_by_category(db: Session = Depends(get_db)):
    return analytics_service.get_sales_by_category(db)


@router.get("/sales-by-region")
def sales_by_region(db: Session = Depends(get_db)):
    return analytics_service.get_sales_by_region(db)


@router.get("/top-products")
def top_products(
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return analytics_service.get_top_products(db, limit)