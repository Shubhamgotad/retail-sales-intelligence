from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.ml.classification import classify_demand

router = APIRouter(prefix="/api/classification", tags=["Demand Classification"])


@router.get("/demand")
def demand_classification(db: Session = Depends(get_db)):
    return classify_demand(db)