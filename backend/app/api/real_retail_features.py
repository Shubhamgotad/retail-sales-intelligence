from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Connection

from app.db.real_retail import get_real_retail_connection
from app.services import real_retail_analytics

router = APIRouter(prefix="/api/real-retail/features", tags=["Real Retail Features"])


@router.get("/demand")
def real_demand(connection: Connection = Depends(get_real_retail_connection)):
    try:
        return real_retail_analytics.demand(connection)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/clustering")
def real_clustering(n_clusters: int = Query(default=3, ge=2, le=10), connection: Connection = Depends(get_real_retail_connection)):
    try:
        return real_retail_analytics.customer_clusters(connection, n_clusters)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/basket")
def real_basket(min_support: float = Query(default=0.01, gt=0, le=1), min_confidence: float = Query(default=0.2, ge=0, le=1), connection: Connection = Depends(get_real_retail_connection)):
    try:
        return real_retail_analytics.baskets(connection, min_support, min_confidence)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
