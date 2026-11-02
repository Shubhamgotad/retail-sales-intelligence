"""Inference with the saved customer K-Means model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from numbers import Integral
from pathlib import Path
import pickle
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

MODEL_FEATURES = (
    "Recency",
    "Frequency",
    "Monetary",
    "AvgOrderValue",
    "TotalQuantity",
)
LOG_TRANSFORM_FEATURES = (
    "Frequency",
    "Monetary",
    "AvgOrderValue",
    "TotalQuantity",
)
_MODEL_ERROR = (
    "The pre-trained customer clustering model is missing or incompatible."
)


@dataclass(frozen=True)
class CustomerClusteringArtifacts:
    estimator: KMeans
    scaler: StandardScaler
    features: tuple[str, ...]
    cluster_count: int


def _artifact_path(filename: str) -> Path:
    return Path(__file__).resolve().parents[3] / "artifacts" / filename


def _validate_artifacts(
    model_artifact: Any, scaler_artifact: Any
) -> CustomerClusteringArtifacts:
    if not isinstance(model_artifact, Mapping):
        raise ValueError("Invalid K-Means model artifact")
    estimator = model_artifact.get("model")
    bundled_scaler = model_artifact.get("scaler")
    features = model_artifact.get("features")
    best_k = model_artifact.get("best_k")
    if (
        not isinstance(estimator, KMeans)
        or not isinstance(bundled_scaler, StandardScaler)
        or not isinstance(scaler_artifact, StandardScaler)
        or not isinstance(features, list)
        or tuple(features) != MODEL_FEATURES
        or not isinstance(best_k, Integral)
        or isinstance(best_k, bool)
        or best_k < 1
        or estimator.n_clusters != best_k
        or getattr(estimator, "n_features_in_", None) != len(MODEL_FEATURES)
        or not hasattr(estimator, "cluster_centers_")
    ):
        raise ValueError("Incompatible K-Means estimator metadata")

    for scaler in (bundled_scaler, scaler_artifact):
        if (
            not isinstance(getattr(scaler, "feature_names_in_", None), np.ndarray)
            or tuple(scaler.feature_names_in_.tolist()) != MODEL_FEATURES
            or getattr(scaler, "n_features_in_", None) != len(MODEL_FEATURES)
            or scaler.mean_.shape != (len(MODEL_FEATURES),)
            or scaler.scale_.shape != (len(MODEL_FEATURES),)
            or not np.isfinite(scaler.mean_).all()
            or not np.isfinite(scaler.scale_).all()
            or (scaler.scale_ <= 0).any()
        ):
            raise ValueError("Incompatible K-Means scaler metadata")

    if (
        not np.array_equal(bundled_scaler.mean_, scaler_artifact.mean_)
        or not np.array_equal(bundled_scaler.scale_, scaler_artifact.scale_)
        or estimator.cluster_centers_.shape != (int(best_k), len(MODEL_FEATURES))
        or not np.isfinite(estimator.cluster_centers_).all()
    ):
        raise ValueError("K-Means artifacts do not match")

    return CustomerClusteringArtifacts(
        estimator=estimator,
        scaler=scaler_artifact,
        features=MODEL_FEATURES,
        cluster_count=int(best_k),
    )


@lru_cache(maxsize=1)
def load_customer_clustering_artifacts() -> CustomerClusteringArtifacts:
    try:
        model_artifact = joblib.load(_artifact_path("kmeans_model.pkl"))
        scaler_artifact = joblib.load(_artifact_path("kmeans_scaler.pkl"))
        return _validate_artifacts(model_artifact, scaler_artifact)
    except (
        AttributeError,
        EOFError,
        ImportError,
        IndexError,
        KeyError,
        OSError,
        pickle.UnpicklingError,
        TypeError,
        ValueError,
    ) as error:
        raise RuntimeError(_MODEL_ERROR) from error


def prepare_customer_features(
    rows: list[dict[str, Any]], features: tuple[str, ...] = MODEL_FEATURES
) -> tuple[list[dict[str, Any]], pd.DataFrame, int]:
    """Return training-compatible customer rows and their transformed features."""
    valid_rows: list[dict[str, Any]] = []
    feature_rows: list[list[float]] = []
    excluded_count = 0

    for row in rows:
        try:
            values = [float(row[feature.lower()]) for feature in features]
        except (KeyError, TypeError, ValueError, OverflowError):
            excluded_count += 1
            continue
        if not np.isfinite(values).all() or any(value <= 0 for value in values):
            excluded_count += 1
            continue
        valid_rows.append(row)
        feature_rows.append(values)

    frame = pd.DataFrame(feature_rows, columns=features)
    if not frame.empty:
        frame.loc[:, list(LOG_TRANSFORM_FEATURES)] = np.log1p(
            frame.loc[:, list(LOG_TRANSFORM_FEATURES)]
        )
    return valid_rows, frame, excluded_count


def predict_customer_clusters(
    rows: list[dict[str, Any]], artifacts: CustomerClusteringArtifacts
) -> tuple[list[dict[str, Any]], list[int], int]:
    valid_rows, features, excluded_count = prepare_customer_features(
        rows, artifacts.features
    )
    if features.empty:
        return valid_rows, [], excluded_count

    try:
        scaled_features = artifacts.scaler.transform(features)
        if (
            scaled_features.shape != (len(valid_rows), len(artifacts.features))
            or not np.isfinite(scaled_features).all()
        ):
            raise ValueError("Scaled customer features are not finite")
        labels = np.asarray(artifacts.estimator.predict(scaled_features))
        if labels.shape != (len(valid_rows),):
            raise ValueError("Estimator returned an invalid number of labels")
        if any(
            not isinstance(label, Integral)
            or label < 0
            or label >= artifacts.cluster_count
            for label in labels
        ):
            raise ValueError("Estimator returned an invalid cluster")
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            "The pre-trained customer clustering model could not process live customer features."
        ) from error
    return valid_rows, [int(label) for label in labels], excluded_count
