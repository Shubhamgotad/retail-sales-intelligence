from pathlib import Path
import pickle

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import LabelEncoder

from app.db.real_retail import get_real_retail_connection
from app.main import app
from app.services import random_forest_forecasting as forecasting


class _Model:
    def __init__(self, prediction=12.5):
        self.prediction = prediction
        self.received = None
        self.predict_calls = 0

    def predict(self, frame):
        self.predict_calls += 1
        self.received = frame.copy()
        return np.array([self.prediction])

    def fit(self, *_args, **_kwargs):
        raise AssertionError("Forecast inference must not fit the model.")


class _Encoder:
    def __init__(self, code="P001", encoded=7):
        self.code = code
        self.encoded = encoded
        self.received = []

    def transform(self, values):
        self.received.append(values)
        if values != [self.code]:
            raise ValueError("unknown product")
        return np.array([self.encoded])


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Connection:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.queries = []

    def execute(self, query, params):
        self.queries.append((str(query), params))
        return _Result(next(self.responses))


def _artifacts():
    model = _Model()
    encoder = _Encoder()
    artifacts = forecasting.ForecastArtifacts(
        model=model,
        encoder=encoder,
        features=forecasting.FORECAST_FEATURES,
        metrics={"mae": 1.0, "rmse": 2.0, "r2": 0.3},
    )
    return artifacts, model, encoder


def test_saved_bundle_loads_and_validates_actual_model_and_encoder():
    forecasting.load_forecast_artifacts.cache_clear()
    try:
        artifacts = forecasting.load_forecast_artifacts()
    finally:
        forecasting.load_forecast_artifacts.cache_clear()

    assert isinstance(artifacts.model, RandomForestRegressor)
    assert isinstance(artifacts.encoder, LabelEncoder)
    assert artifacts.features == forecasting.FORECAST_FEATURES
    assert artifacts.encoder.transform(["10002"]).shape == (1,)
    assert artifacts.metrics["mae"] == pytest.approx(191.054209436)


def test_artifact_loader_is_cached_and_uses_project_relative_path(monkeypatch, tmp_path):
    artifact = forecasting.joblib.load(forecasting._artifact_path())
    paths = []
    monkeypatch.setattr(
        forecasting.joblib, "load", lambda path: paths.append(Path(path)) or artifact
    )
    monkeypatch.chdir(tmp_path)
    forecasting.load_forecast_artifacts.cache_clear()
    try:
        first = forecasting.load_forecast_artifacts()
        second = forecasting.load_forecast_artifacts()
    finally:
        forecasting.load_forecast_artifacts.cache_clear()

    assert first is second
    assert paths == [
        Path(forecasting.__file__).resolve().parents[3]
        / "artifacts"
        / "random_forest_model.pkl"
    ]


@pytest.mark.parametrize(
    "artifact",
    [
        [],
        {"model": object()},
        {
            "model": object(),
            "label_encoder": object(),
            "features": list(forecasting.FORECAST_FEATURES[:-1]),
            "metrics": {},
        },
    ],
)
def test_artifact_validation_rejects_incompatible_bundles(artifact):
    with pytest.raises(ValueError):
        forecasting._validate_artifact(artifact)


@pytest.mark.parametrize(
    "artifact_error",
    [
        FileNotFoundError("private artifact path"),
        pickle.UnpicklingError("private corrupt artifact"),
    ],
)
def test_missing_or_corrupt_artifact_error_is_sanitized(monkeypatch, artifact_error):
    monkeypatch.setattr(
        forecasting.joblib,
        "load",
        lambda _path: (_ for _ in ()).throw(artifact_error),
    )
    forecasting.load_forecast_artifacts.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="missing or incompatible") as error:
            forecasting.load_forecast_artifacts()
    finally:
        forecasting.load_forecast_artifacts.cache_clear()
    assert "private" not in str(error.value)


def test_monthly_resampling_lags_rolling_mean_and_next_month_match_notebook():
    artifacts, _, encoder = _artifacts()
    monthly_rows = [
        {"period": "2024-01-01", "demand": 10},
        {"period": "2024-03-01", "demand": 30},
    ]

    forecast_month, features = forecasting.build_forecast_features(
        monthly_rows, "P001", artifacts
    )

    assert forecast_month.strftime("%Y-%m") == "2024-04"
    assert features.columns.tolist() == list(forecasting.FORECAST_FEATURES)
    assert features.iloc[0].tolist() == pytest.approx(
        [7, 4, 2024, 30, 0, 10, 40 / 3]
    )
    assert encoder.received == [["P001"]]


def test_insufficient_history_does_not_fabricate_forecast():
    artifacts, _, _ = _artifacts()

    with pytest.raises(forecasting.InsufficientForecastHistory):
        forecasting.build_forecast_features(
            [{"period": "2024-01-01", "demand": 10}], "P001", artifacts
        )


def test_unknown_product_code_is_rejected_before_database_access():
    artifacts, _, encoder = _artifacts()

    with pytest.raises(LookupError, match="not supported"):
        forecasting.forecast_product(None, "UNKNOWN", artifacts)

    assert encoder.received == [["UNKNOWN"]]


def test_forecast_uses_predict_only_and_returns_monthly_quantity(monkeypatch):
    artifacts, model, encoder = _artifacts()
    connection = _Connection(
        [
            [{"product_name": "A valid product"}],
            [
                {"period": "2024-01-01", "demand": 10},
                {"period": "2024-02-01", "demand": 20},
                {"period": "2024-03-01", "demand": 30},
            ],
        ]
    )
    monkeypatch.setattr(
        forecasting,
        "_rows",
        lambda conn, query, params=None: _rows_from_connection(conn, query, params),
    )

    response = forecasting.forecast_product(connection, "P001", artifacts)

    assert response["forecast_month"] == "2024-04"
    assert response["predicted_monthly_quantity"] == 12.5
    assert response["target"] == "Monthly product quantity"
    assert response["algorithm"] == "Random Forest Regressor"
    assert response["input_features"] == list(forecasting.FORECAST_FEATURES)
    assert model.predict_calls == 1
    assert model.received.iloc[0].tolist() == [7, 4, 2024, 30, 20, 10, 20]
    assert encoder.received == [["P001"], ["P001"]]
    assert len(connection.queries) == 2
    assert all("real_retail.sales_lines" in query for query, _ in connection.queries)
    assert "source_customer_key IS NOT NULL" in connection.queries[1][0]
    assert "signed_quantity > 0" in connection.queries[1][0]
    assert "source_price > 0" in connection.queries[1][0]


def _rows_from_connection(connection, query, params):
    return connection.execute(query, params).mappings().all()


@pytest.mark.parametrize(
    ("description_rows", "monthly_rows", "exception"),
    [
        ([], [], LookupError),
        ([{"product_name": "[Missing Description]"}], [], LookupError),
        (
            [{"product_name": "A valid product"}],
            [{"period": "2024-01-01", "demand": 10}],
            forecasting.InsufficientForecastHistory,
        ),
    ],
)
def test_unusable_product_and_insufficient_history_errors(
    description_rows, monthly_rows, exception
):
    artifacts, _, _ = _artifacts()
    connection = _Connection([description_rows, monthly_rows])

    with pytest.raises(exception):
        forecasting.forecast_product(connection, "P001", artifacts)


def test_api_uses_guarded_connection_dependency_and_returns_forecast(monkeypatch):
    artifacts, _, _ = _artifacts()
    monkeypatch.setattr(forecasting, "load_forecast_artifacts", lambda: artifacts)

    class _FakeConnection:
        def __init__(self):
            self.calls = 0

        def execute(self, query, params):
            self.calls += 1
            result = (
                [{"product_name": "A valid product"}]
                if self.calls == 1
                else [
                    {"period": "2024-01-01", "demand": 10},
                    {"period": "2024-02-01", "demand": 20},
                    {"period": "2024-03-01", "demand": 30},
                ]
            )
            return _Result(result)

    connection = _FakeConnection()
    previous_override = app.dependency_overrides.get(get_real_retail_connection)
    app.dependency_overrides[get_real_retail_connection] = lambda: connection
    try:
        response = TestClient(app).get(
            "/api/real-retail/features/demand-forecast/P001"
        )
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_real_retail_connection, None)
        else:
            app.dependency_overrides[get_real_retail_connection] = previous_override

    assert response.status_code == 200
    payload = response.json()
    assert payload["product_code"] == "P001"
    assert payload["product_name"] == "A valid product"
    assert payload["forecast_month"] == "2024-04"
    assert payload["predicted_monthly_quantity"] == 12.5


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ValueError("Enter a product code."), 422),
        (LookupError("Product code is not supported by the saved model."), 404),
        (RuntimeError("The pre-trained Random Forest demand model is missing or incompatible."), 503),
    ],
)
def test_api_maps_forecast_errors_to_controlled_statuses(monkeypatch, error, status):
    def raise_error(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(forecasting, "forecast_product", raise_error)
    previous_override = app.dependency_overrides.get(get_real_retail_connection)
    app.dependency_overrides[get_real_retail_connection] = lambda: object()
    try:
        response = TestClient(app).get(
            "/api/real-retail/features/demand-forecast/P001"
        )
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_real_retail_connection, None)
        else:
            app.dependency_overrides[get_real_retail_connection] = previous_override

    assert response.status_code == status
    assert "private" not in response.text
