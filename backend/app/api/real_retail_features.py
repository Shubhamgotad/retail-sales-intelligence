from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Connection

from app.db.real_retail import get_real_retail_connection
from app.services import apriori_recommendations
from app.services import random_forest_forecasting
from app.services import real_retail_analytics

router = APIRouter(prefix="/api/real-retail/features", tags=["Real Retail Features"])


@router.get("/demand")
def real_demand(connection: Connection = Depends(get_real_retail_connection)):
    try:
        return real_retail_analytics.demand(connection)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/demand-forecast/{product_code}")
def real_demand_forecast(
    product_code: str,
    connection: Connection = Depends(get_real_retail_connection),
):
    try:
        return random_forest_forecasting.forecast_product(connection, product_code)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/clustering")
def real_clustering(connection: Connection = Depends(get_real_retail_connection)):
    try:
        return real_retail_analytics.customer_clusters(connection)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/basket")
def real_basket(min_support: float = Query(default=0.01, gt=0, le=1), min_confidence: float = Query(default=0.2, ge=0, le=1), connection: Connection = Depends(get_real_retail_connection)):
    try:
        return real_retail_analytics.baskets(connection, min_support, min_confidence)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/recommendations/{product_code}")
def real_recommendations(
    product_code: str,
    connection: Connection = Depends(get_real_retail_connection),
):
    try:
        return apriori_recommendations.recommendations(connection, product_code)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
