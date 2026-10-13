from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.ml.clustering import cluster_products

router = APIRouter(prefix="/api/clustering", tags=["Product Clustering"])


@router.get("/products")
def product_clusters(
    n_clusters: int = Query(default=3, ge=2, le=10),
    db: Session = Depends(get_db),
):
    return cluster_products(db, n_clusters)