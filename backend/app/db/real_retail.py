"""Read-only connection dependency for the isolated real-retail database."""

from __future__ import annotations

import os
from collections.abc import Generator

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, URL, make_url
from sqlalchemy.exc import SQLAlchemyError

REAL_RETAIL_DATABASE_ENV = "REAL_RETAIL_ANALYTICS_DATABASE_URL"
REQUIRED_DATABASE = "retail_sales_test_disposable"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _configured_url() -> URL:
    raw_url = os.environ.get(REAL_RETAIL_DATABASE_ENV)
    if not raw_url:
        raise ValueError(
            f"{REAL_RETAIL_DATABASE_ENV} is required; refusing to use the application database"
        )
    url = make_url(raw_url)
    if url.database != REQUIRED_DATABASE:
        raise ValueError(
            f"{REAL_RETAIL_DATABASE_ENV} must target {REQUIRED_DATABASE!r}"
        )
    if (url.host or "").lower() not in LOCAL_HOSTS:
        raise ValueError("Real-retail analytics requires a local PostgreSQL host")
    return url


def get_real_retail_connection() -> Generator[Connection, None, None]:
    """Yield a transaction-scoped read-only connection."""
    try:
        url = _configured_url()
        engine = create_engine(url, pool_pre_ping=True)
        connection = engine.connect()
        transaction = connection.begin()
        try:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            yield connection
        finally:
            transaction.rollback()
            connection.close()
            engine.dispose()
    except (ValueError, SQLAlchemyError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"Real-retail analytics database unavailable: {type(error).__name__}",
        ) from error
