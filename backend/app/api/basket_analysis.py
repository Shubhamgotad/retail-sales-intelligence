from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.ml.association_rules import find_association_rules

router = APIRouter(prefix="/api/basket", tags=["Market Basket Analysis"])


@router.get("/rules")
def basket_rules(
    min_support: float = Query(default=0.01, gt=0, le=1),
    min_confidence: float = Query(default=0.2, ge=0, le=1),
    db: Session = Depends(get_db),
):
    return find_association_rules(db, min_support, min_confidence)