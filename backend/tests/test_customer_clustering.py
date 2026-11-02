from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.db.real_retail import get_real_retail_connection
from app.main import app
from app.services import customer_clustering, real_retail_analytics


def _row(
    customer_id="100",
    recency=10,
    frequency=4,
    monetary=100,
    avgordervalue=25,
    totalquantity=8,
):
    return {
        "customer_id": customer_id,
        "recency": recency,
        "frequency": frequency,
        "monetary": monetary,
        "avgordervalue": avgordervalue,
        "totalquantity": totalquantity,
        "invoice_count": 4,
        "line_count": 8,
    }


class _Scaler:
    def __init__(self):
        self.received = None

    def transform(self, frame):
        self.received = frame.copy()
        return frame.to_numpy()


class _Estimator:
    def __init__(self, labels=(1,)):
        self.labels = np.asarray(labels)
        self.received = None
        self.predict_calls = 0

    def predict(self, values):
        self.predict_calls += 1
        self.received = values.copy()
        return self.labels[: len(values)]

    def fit(self, *_args, **_kwargs):
        raise AssertionError("The saved estimator must not be fitted.")

    def fit_predict(self, *_args, **_kwargs):
        raise AssertionError("The saved estimator must not be fitted.")


def _artifacts(labels=(1,)):
    scaler = _Scaler()
    estimator = _Estimator(labels)
    return (
        customer_clustering.CustomerClusteringArtifacts(
            estimator=estimator,
            scaler=scaler,
            features=customer_clustering.MODEL_FEATURES,
            cluster_count=2,
        ),
        scaler,
        estimator,
    )


def test_saved_model_and_scaler_load_with_expected_metadata():
    customer_clustering.load_customer_clustering_artifacts.cache_clear()
    try:
        artifacts = customer_clustering.load_customer_clustering_artifacts()
    finally:
        customer_clustering.load_customer_clustering_artifacts.cache_clear()

    assert artifacts.features == (
        "Recency",
        "Frequency",
        "Monetary",
        "AvgOrderValue",
        "TotalQuantity",
    )
    assert artifacts.cluster_count == 2
    assert artifacts.estimator.n_clusters == 2
    assert artifacts.scaler.feature_names_in_.tolist() == list(artifacts.features)


def test_artifact_loader_is_cached_and_resolves_paths_from_project(monkeypatch, tmp_path):
    model_artifact = customer_clustering.joblib.load(
        customer_clustering._artifact_path("kmeans_model.pkl")
    )
    scaler_artifact = customer_clustering.joblib.load(
        customer_clustering._artifact_path("kmeans_scaler.pkl")
    )
    loaded_paths = []

    def load(path):
        loaded_paths.append(Path(path))
        return model_artifact if len(loaded_paths) == 1 else scaler_artifact

    monkeypatch.setattr(customer_clustering.joblib, "load", load)
    monkeypatch.chdir(tmp_path)
    customer_clustering.load_customer_clustering_artifacts.cache_clear()
    try:
        first = customer_clustering.load_customer_clustering_artifacts()
        second = customer_clustering.load_customer_clustering_artifacts()
    finally:
        customer_clustering.load_customer_clustering_artifacts.cache_clear()

    assert first is second
    assert loaded_paths == [
        Path(customer_clustering.__file__).resolve().parents[3]
        / "artifacts"
        / "kmeans_model.pkl",
        Path(customer_clustering.__file__).resolve().parents[3]
        / "artifacts"
        / "kmeans_scaler.pkl",
    ]


def test_missing_artifact_error_is_sanitized(monkeypatch):
    monkeypatch.setattr(
        customer_clustering.joblib,
        "load",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("private path")),
    )
    customer_clustering.load_customer_clustering_artifacts.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="missing or incompatible") as error:
            customer_clustering.load_customer_clustering_artifacts()
    finally:
        customer_clustering.load_customer_clustering_artifacts.cache_clear()

    assert "private" not in str(error.value)


def test_notebook_log1p_preprocessing_and_exact_feature_order():
    rows, transformed, excluded = customer_clustering.prepare_customer_features(
        [_row()]
    )

    assert excluded == 0
    assert rows == [_row()]
    assert transformed.columns.tolist() == [
        "Recency",
        "Frequency",
        "Monetary",
        "AvgOrderValue",
        "TotalQuantity",
    ]
    assert transformed.iloc[0].tolist() == pytest.approx(
        [
            10,
            np.log1p(4),
            np.log1p(100),
            np.log1p(25),
            np.log1p(8),
        ]
    )


def test_invalid_missing_zero_negative_and_nonfinite_features_are_excluded():
    rows = [
        _row(),
        _row(customer_id="missing", monetary=None),
        _row(customer_id="zero", frequency=0),
        _row(customer_id="negative", totalquantity=-1),
        _row(customer_id="nonfinite", avgordervalue=float("inf")),
        {"customer_id": "missing-column", "recency": 10},
    ]

    valid, features, excluded = customer_clustering.prepare_customer_features(rows)

    assert [row["customer_id"] for row in valid] == ["100"]
    assert len(features) == 1
    assert excluded == 5


def test_prediction_transforms_with_saved_scaler_and_calls_predict_only():
    artifacts, scaler, estimator = _artifacts()

    rows, labels, excluded = customer_clustering.predict_customer_clusters(
        [_row()], artifacts
    )

    assert rows == [_row()]
    assert labels == [1]
    assert excluded == 0
    assert scaler.received.columns.tolist() == list(customer_clustering.MODEL_FEATURES)
    assert estimator.predict_calls == 1
    assert estimator.received.shape == (1, 5)


def test_unusable_scaled_features_return_a_controlled_error():
    artifacts, _, _ = _artifacts()

    class InvalidScaler:
        def transform(self, _frame):
            return np.array([[np.nan] * 5])

    artifacts = customer_clustering.CustomerClusteringArtifacts(
        estimator=artifacts.estimator,
        scaler=InvalidScaler(),
        features=artifacts.features,
        cluster_count=artifacts.cluster_count,
    )

    with pytest.raises(RuntimeError, match="could not process live customer features"):
        customer_clustering.predict_customer_clusters([_row()], artifacts)


def test_live_api_response_uses_saved_cluster_count_and_live_rows(monkeypatch):
    artifacts, _, _ = _artifacts(labels=(1,))
    monkeypatch.setattr(
        customer_clustering,
        "load_customer_clustering_artifacts",
        lambda: artifacts,
    )
    queries = []

    def rows(_connection, query, _params=None):
        queries.append(query)
        if "WITH eligible_sales" in query:
            return [_row()]
        return [{
            "excluded_missing_customer_lines": 12,
            "excluded_training_ineligible_lines": 20,
        }]

    monkeypatch.setattr(real_retail_analytics, "_rows", rows)
    previous_override = app.dependency_overrides.get(get_real_retail_connection)
    app.dependency_overrides[get_real_retail_connection] = lambda: object()
    try:
        response = TestClient(app).get("/api/real-retail/features/clustering")
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_real_retail_connection, None)
        else:
            app.dependency_overrides[get_real_retail_connection] = previous_override

    assert response.status_code == 200
    payload = response.json()
    assert payload["algorithm"] == "K-Means"
    assert payload["model_type"] == "Pre-trained"
    assert payload["cluster_count"] == 2
    assert payload["features"] == list(customer_clustering.MODEL_FEATURES)
    assert payload["excluded_missing_customer_lines"] == 12
    assert payload["excluded_training_ineligible_lines"] == 20
    assert payload["clusters"] == [{
        "cluster": 1,
        "customer_count": 1,
        "total_monetary_value": "100",
        "total_signed_quantity": 8.0,
        "customers": ["100"],
    }]
    assert payload["customers"][0]["cluster"] == 1
    assert "training summary" in payload["note"]
    assert "COUNT(DISTINCT eligible_sales.invoice_datetime)" in queries[0]
    assert "signed_quantity > 0" in queries[0]
    assert "source_price > 0" in queries[0]
    assert "source_customer_key IS NOT NULL" in queries[0]
    assert "invoice_datetime IS NOT NULL" in queries[0]
