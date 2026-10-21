from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.analytics import router as analytics_router
from app.api.basket_analysis import router as basket_router
from app.api.classification import router as classification_router
from app.api.clustering import router as clustering_router
from app.api.products import router as products_router
from app.api.real_retail_analytics import router as real_retail_analytics_router
from app.api.real_retail_features import router as real_retail_features_router
from app.db.session import engine

app = FastAPI(
    title="Retail Sales Intelligence API",
    description="Retail data warehouse analytics and product intelligence.",
    version="1.0.0",
)

app.include_router(analytics_router)
app.include_router(clustering_router)
app.include_router(classification_router)
app.include_router(basket_router)
app.include_router(products_router)
app.include_router(real_retail_analytics_router)
app.include_router(real_retail_features_router)


@app.get("/", tags=["System"])
def root():
    return {
        "message": "Retail Sales Intelligence API",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health/live", tags=["System"])
def liveness():
    return {"status": "alive"}


@app.get("/health", tags=["System"])
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed. Check backend/.env and PostgreSQL.",
        )