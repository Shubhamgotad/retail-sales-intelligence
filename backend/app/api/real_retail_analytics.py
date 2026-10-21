from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Connection

from app.db.real_retail import get_real_retail_connection
from app.services import real_retail_analytics

router = APIRouter(prefix="/api/real-retail/analytics", tags=["Real Retail Analytics"])


def _sort(value: str) -> str:
    if value not in real_retail_analytics.SORT_FIELDS:
        raise HTTPException(status_code=422, detail="Unsupported sort field")
    return value


@router.get("/overview")
def dataset_overview(connection: Connection = Depends(get_real_retail_connection)):
    try:
        return real_retail_analytics.overview(connection)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/monthly")
def monthly_trends(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    connection: Connection = Depends(get_real_retail_connection),
):
    try:
        return real_retail_analytics.monthly(connection, start_date, end_date)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/countries")
def country_analytics(
    sort: str = Query(default="value"),
    limit: int = Query(default=25, ge=1, le=200),
    connection: Connection = Depends(get_real_retail_connection),
):
    try:
        return real_retail_analytics.countries(connection, _sort(sort), limit)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/products")
def product_analytics(
    sort: str = Query(default="value"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100000),
    search: str | None = Query(default=None, max_length=100),
    connection: Connection = Depends(get_real_retail_connection),
):
    try:
        return real_retail_analytics.products(connection, _sort(sort), limit, offset, search)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/customers")
def customer_analytics(
    sort: str = Query(default="value"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100000),
    connection: Connection = Depends(get_real_retail_connection),
):
    try:
        return real_retail_analytics.customers(connection, _sort(sort), limit, offset)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/quality")
def data_quality(connection: Connection = Depends(get_real_retail_connection)):
    try:
        return real_retail_analytics.quality(connection)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
