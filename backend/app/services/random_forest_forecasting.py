"""Inference with the saved monthly product-demand Random Forest."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import LabelEncoder
from sqlalchemy.engine import Connection

from app.services.real_retail_analytics import (
    _VALID_PRODUCT_DESCRIPTION,
    _rows,
    is_valid_product_description,
)

FORECAST_FEATURES = (
    "StockCode_Encoded",
    "MonthNumber",
    "Year",
    "Lag_1",
    "Lag_2",
    "Lag_3",
    "RollingMean_3",
)
_MODEL_ERROR = (
    "The pre-trained Random Forest demand model is missing or incompatible."
)
_INSUFFICIENT_HISTORY = (
    "At least three monthly observations are required to forecast this product."
)


@dataclass(frozen=True)
class ForecastArtifacts:
    model: RandomForestRegressor
    encoder: LabelEncoder
    features: tuple[str, ...]
    metrics: dict[str, float]


class InsufficientForecastHistory(ValueError):
    """Raised when a product has too few monthly observations for its features."""


def _artifact_path() -> Path:
    return Path(__file__).resolve().parents[3] / "artifacts" / "random_forest_model.pkl"


def _validate_artifact(artifact: Any) -> ForecastArtifacts:
    if not isinstance(artifact, Mapping):
        raise ValueError("Invalid Random Forest artifact structure")
    model = artifact.get("model")
    encoder = artifact.get("label_encoder")
    features = artifact.get("features")
    metrics = artifact.get("metrics")
    if (
        not isinstance(model, RandomForestRegressor)
        or not isinstance(encoder, LabelEncoder)
        or not isinstance(features, list)
        or tuple(features) != FORECAST_FEATURES
        or getattr(model, "n_features_in_", None) != len(FORECAST_FEATURES)
        or tuple(getattr(model, "feature_names_in_", ())) != FORECAST_FEATURES
        or not hasattr(model, "estimators_")
        or not isinstance(getattr(encoder, "classes_", None), np.ndarray)
        or encoder.classes_.ndim != 1
        or not len(encoder.classes_)
        or any(not isinstance(code, str) or not code for code in encoder.classes_)
        or len(set(encoder.classes_)) != len(encoder.classes_)
        or not isinstance(metrics, Mapping)
        or not {"mae", "rmse", "r2"}.issubset(metrics)
    ):
        raise ValueError("Incompatible Random Forest artifact metadata")
    try:
        numeric_metrics = {
            key: float(metrics[key]) for key in ("mae", "rmse", "r2")
        }
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Invalid Random Forest evaluation metrics") from error
    if not np.isfinite(list(numeric_metrics.values())).all():
        raise ValueError("Invalid Random Forest evaluation metrics")
    return ForecastArtifacts(
        model=model,
        encoder=encoder,
        features=FORECAST_FEATURES,
        metrics=numeric_metrics,
    )


@lru_cache(maxsize=1)
def load_forecast_artifacts() -> ForecastArtifacts:
    try:
        return _validate_artifact(joblib.load(_artifact_path()))
    except Exception as error:
        raise RuntimeError(_MODEL_ERROR) from error


def build_forecast_features(
    monthly_rows: list[dict[str, Any]],
    product_code: str,
    artifacts: ForecastArtifacts,
) -> tuple[pd.Timestamp, pd.DataFrame]:
    if len(monthly_rows) < 1:
        raise InsufficientForecastHistory(_INSUFFICIENT_HISTORY)
    try:
        history = pd.DataFrame(monthly_rows)
        history["period"] = pd.to_datetime(history["period"], errors="raise")
        history["demand"] = pd.to_numeric(history["demand"], errors="raise")
        if (
            history["period"].isna().any()
            or not np.isfinite(history["demand"].to_numpy(dtype=float)).all()
        ):
            raise ValueError("Historical monthly demand is not finite")
        monthly = (
            history.set_index("period")["demand"]
            .sort_index()
            .resample("MS")
            .sum()
        )
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise RuntimeError(
            "Historical product demand is incompatible with the forecasting model."
        ) from error

    if len(monthly) < 3:
        raise InsufficientForecastHistory(_INSUFFICIENT_HISTORY)

    try:
        encoded_code = int(artifacts.encoder.transform([product_code])[0])
    except (TypeError, ValueError) as error:
        raise LookupError("Product code is not supported by the saved model.") from error

    forecast_month = monthly.index[-1] + pd.offsets.MonthBegin(1)
    values = {
        "StockCode_Encoded": encoded_code,
        "MonthNumber": int(forecast_month.month),
        "Year": int(forecast_month.year),
        "Lag_1": float(monthly.iloc[-1]),
        "Lag_2": float(monthly.iloc[-2]),
        "Lag_3": float(monthly.iloc[-3]),
        "RollingMean_3": float(monthly.tail(3).mean()),
    }
    features = pd.DataFrame(
        [[values[feature] for feature in artifacts.features]],
        columns=artifacts.features,
    )
    if not np.isfinite(features.to_numpy(dtype=float)).all():
        raise RuntimeError(
            "Historical product demand is incompatible with the forecasting model."
        )
    return forecast_month, features


def forecast_product(
    connection: Connection,
    product_code: str,
    artifacts: ForecastArtifacts | None = None,
) -> dict[str, Any]:
    if not isinstance(product_code, str) or not product_code.strip():
        raise ValueError("Enter a product code.")
    if product_code != product_code.strip():
        raise ValueError("Product code must not contain surrounding whitespace.")
    artifacts = artifacts or load_forecast_artifacts()
    try:
        artifacts.encoder.transform([product_code])
    except (TypeError, ValueError) as error:
        raise LookupError("Product code is not supported by the saved model.") from error

    description_rows = _rows(
        connection,
        f"""
        SELECT MAX(source_description) FILTER (
                   WHERE {_VALID_PRODUCT_DESCRIPTION}
               ) AS product_name
        FROM real_retail.sales_lines
        WHERE source_product_key = :product_code
        GROUP BY source_product_key
        """,
        {"product_code": product_code},
    )
    if (
        not description_rows
        or not is_valid_product_description(description_rows[0]["product_name"])
    ):
        raise LookupError("Product code was not found with a valid description.")

    monthly_rows = _rows(
        connection,
        """
        SELECT DATE_TRUNC('month', invoice_datetime)::date AS period,
               SUM(signed_quantity) AS demand
        FROM real_retail.sales_lines
        WHERE source_product_key = :product_code
          AND source_customer_key IS NOT NULL
          AND invoice_datetime IS NOT NULL
          AND signed_quantity > 0
          AND source_price > 0
        GROUP BY DATE_TRUNC('month', invoice_datetime)
        ORDER BY period
        """,
        {"product_code": product_code},
    )
    forecast_month, features = build_forecast_features(
        monthly_rows, product_code, artifacts
    )
    try:
        prediction = np.asarray(artifacts.model.predict(features), dtype=float)
        if prediction.shape != (1,) or not np.isfinite(prediction).all():
            raise ValueError("Invalid prediction")
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            "The pre-trained Random Forest could not forecast this product."
        ) from error

    return {
        "algorithm": "Random Forest Regressor",
        "product_code": product_code,
        "product_name": description_rows[0]["product_name"],
        "forecast_month": forecast_month.strftime("%Y-%m"),
        "predicted_monthly_quantity": float(prediction[0]),
        "target": "Monthly product quantity",
        "input_features": list(artifacts.features),
        "history_months_used": 3,
        "evaluation_metrics": artifacts.metrics,
        "note": (
            "Forecasts use notebook-compatible monthly quantity totals and "
            "the saved product-code encoder. Missing months between a product's "
            "first and last eligible month contribute zero. Product codes not "
            "seen during training and histories shorter than three months "
            "cannot be forecast. Evaluation metrics come from the saved model "
            "bundle and are not guarantees for live data."
        ),
    }
