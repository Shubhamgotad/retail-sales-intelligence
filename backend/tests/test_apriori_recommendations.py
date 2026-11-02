import pickle
from pathlib import Path

from pandas import DataFrame
import pytest
from fastapi.testclient import TestClient

from app.db.real_retail import get_real_retail_connection
from app.main import app
from app.services import apriori_recommendations


def _model(lookup=None, descriptions=None, frequent_columns=None, rule_columns=None):
    lookup = lookup or {}
    if descriptions is None:
        descriptions = {
            code: f"Product {code}"
            for product_code, recommendations in lookup.items()
            for code in [product_code, *recommendations]
        }
    return {
        "frequent_itemsets": DataFrame(
            columns=frequent_columns or ["support", "itemsets"]
        ),
        "rules": DataFrame(
            columns=rule_columns
            or ["antecedents", "consequents", "support", "confidence", "lift"]
        ),
        "recommendations_lookup": lookup,
        "item_descriptions": descriptions,
    }


def test_valid_model_accepts_artifact_columns_and_preserves_product_codes():
    model = _model(
        {"001-A": ["00.2/B"]},
        {"001-A": " Main product ", "00.2/B": "Recommended product"},
        frequent_columns=["support", "itemsets"],
        rule_columns=[
            "antecedents",
            "consequents",
            "antecedent support",
            "consequent support",
            "support",
            "confidence",
            "lift",
            "representativity",
            "leverage",
            "conviction",
            "zhangs_metric",
            "jaccard",
            "certainty",
            "kulczynski",
        ],
    )

    assert apriori_recommendations._validate_model(model) is model


def test_trusted_project_artifact_passes_validation():
    apriori_recommendations._load_apriori_model.cache_clear()

    try:
        model = apriori_recommendations._load_apriori_model()
    finally:
        apriori_recommendations._load_apriori_model.cache_clear()

    assert len(model["recommendations_lookup"]) == 169
    assert len(model["item_descriptions"]) == 4017


@pytest.mark.parametrize(
    ("model", "message"),
    [
        (_model(frequent_columns=["support"]), "model tables"),
        (_model(rule_columns=["antecedents", "consequents"]), "model tables"),
    ],
)
def test_model_requires_notebook_produced_dataframe_columns(model, message):
    with pytest.raises(ValueError, match=message):
        apriori_recommendations._validate_model(model)


@pytest.mark.parametrize(
    "lookup",
    [
        {" ": []},
        {"A100": ["\t"]},
        {"A100": ["B200", "B200"]},
        {100: ["B200"]},
        {"A100": ( "B200",)},
        {"A100": [200]},
    ],
)
def test_model_rejects_invalid_recommendation_mapping_entries(lookup):
    with pytest.raises(ValueError, match="recommendation entries"):
        apriori_recommendations._validate_model(_model(lookup))


@pytest.mark.parametrize(
    ("lookup", "descriptions"),
    [
        ([], {}),
        ({}, []),
    ],
)
def test_model_requires_mapping_types_for_lookup_and_descriptions(
    lookup, descriptions
):
    model = _model()
    model["recommendations_lookup"] = lookup
    model["item_descriptions"] = descriptions

    with pytest.raises(ValueError, match="model mappings"):
        apriori_recommendations._validate_model(model)


@pytest.mark.parametrize(
    "descriptions",
    [
        {"A100": "Product A", "B200": " "},
        {"A100": "Product A", 200: "Product B"},
        {"A100": "Product A", "B200": None},
    ],
)
def test_model_rejects_invalid_descriptions(descriptions):
    with pytest.raises(ValueError, match="product descriptions"):
        apriori_recommendations._validate_model(
            _model({"A100": ["B200"]}, descriptions)
        )


@pytest.mark.parametrize(
    ("lookup", "descriptions"),
    [
        ({"A100": []}, {}),
        ({"A100": ["B200"]}, {"A100": "Product A"}),
    ],
)
def test_model_requires_descriptions_for_lookup_and_recommendation_codes(
    lookup, descriptions
):
    with pytest.raises(ValueError, match="missing recommendation codes"):
        apriori_recommendations._validate_model(_model(lookup, descriptions))


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, query, params):
        self.calls.append((str(query), params))
        return _Result(self.rows)


@pytest.fixture
def api_client():
    previous_override = app.dependency_overrides.get(get_real_retail_connection)
    app.dependency_overrides[get_real_retail_connection] = lambda: _Connection([])
    try:
        yield TestClient(app)
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_real_retail_connection, None)
        else:
            app.dependency_overrides[get_real_retail_connection] = previous_override


def test_recommendations_return_names_from_valid_database_descriptions(monkeypatch):
    model = _model({"A100": ["B200", "C300"]})
    monkeypatch.setattr(apriori_recommendations, "_load_apriori_model", lambda: model)
    connection = _Connection(
        [
            {"product_code": "A100", "product_name": "Main product"},
            {"product_code": "B200", "product_name": "Recommended product"},
            {"product_code": "C300", "product_name": "[Missing Description]"},
        ]
    )

    result = apriori_recommendations.recommendations(connection, "A100")

    assert result == {
        "algorithm": "Apriori",
        "product_code": "A100",
        "product_name": "Main product",
        "recommendations": [
            {"product_code": "B200", "product_name": "Recommended product"}
        ],
        "message": None,
    }
    assert "real_retail.sales_lines" in connection.calls[0][0]
    assert connection.calls[0][1] == {
        "product_code_0": "A100",
        "product_code_1": "B200",
        "product_code_2": "C300",
    }


def test_product_with_no_recommendations_returns_message(monkeypatch):
    monkeypatch.setattr(
        apriori_recommendations, "_load_apriori_model", lambda: _model()
    )
    connection = _Connection(
        [{"product_code": "A100", "product_name": "Main product"}]
    )

    result = apriori_recommendations.recommendations(connection, "A100")

    assert result["recommendations"] == []
    assert result["message"] == "No Apriori recommendations are available for this product."


def test_missing_product_code_is_rejected_by_api(api_client):
    response = api_client.get("/api/real-retail/features/recommendations/%20")

    assert response.status_code == 422
    assert response.json()["detail"] == "Enter a valid product code."


def test_recommendations_endpoint_returns_expected_response(monkeypatch, api_client):
    monkeypatch.setattr(
        apriori_recommendations,
        "_load_apriori_model",
        lambda: _model({"A100": ["B200"]}),
    )
    connection = _Connection(
        [
            {"product_code": "A100", "product_name": "Main product"},
            {"product_code": "B200", "product_name": "Recommended product"},
        ]
    )
    api_client.app.dependency_overrides[get_real_retail_connection] = (
        lambda: connection
    )

    response = api_client.get(
        "/api/real-retail/features/recommendations/A100"
    )

    assert response.status_code == 200
    assert response.json() == {
        "algorithm": "Apriori",
        "product_code": "A100",
        "product_name": "Main product",
        "recommendations": [
            {"product_code": "B200", "product_name": "Recommended product"}
        ],
        "message": None,
    }


def test_missing_or_invalid_selected_product_is_not_returned(monkeypatch):
    monkeypatch.setattr(
        apriori_recommendations,
        "_load_apriori_model",
        lambda: _model({"A100": ["B200"]}),
    )
    connection = _Connection(
        [{"product_code": "A100", "product_name": "[Missing Description]"}]
    )

    with pytest.raises(LookupError, match="not found or has no valid description"):
        apriori_recommendations.recommendations(connection, "A100")


def test_stale_recommendation_code_is_excluded(monkeypatch):
    monkeypatch.setattr(
        apriori_recommendations,
        "_load_apriori_model",
        lambda: _model({"A100": ["B200", "STALE"]}),
    )
    connection = _Connection(
        [
            {"product_code": "A100", "product_name": "Main product"},
            {"product_code": "B200", "product_name": "Current product"},
        ]
    )

    result = apriori_recommendations.recommendations(connection, "A100")

    assert result["recommendations"] == [
        {"product_code": "B200", "product_name": "Current product"}
    ]


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("private artifact path"),
        pickle.UnpicklingError("private corrupt artifact detail"),
    ],
)
def test_missing_or_corrupt_model_returns_controlled_error(monkeypatch, failure):
    monkeypatch.setattr(
        apriori_recommendations.joblib, "load", lambda _path: (_ for _ in ()).throw(failure)
    )
    apriori_recommendations._load_apriori_model.cache_clear()

    try:
        with pytest.raises(RuntimeError, match="artifact is missing or incompatible") as error:
            apriori_recommendations._load_apriori_model()
    finally:
        apriori_recommendations._load_apriori_model.cache_clear()

    assert "private" not in str(error.value)


def test_model_loader_is_cached_and_path_is_independent_of_working_directory(
    monkeypatch, tmp_path
):
    calls = []
    expected_path = Path(apriori_recommendations.__file__).resolve().parents[3] / "artifacts" / "apriori_model.pkl"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        apriori_recommendations.joblib,
        "load",
        lambda path: calls.append(Path(path)) or _model(),
    )
    apriori_recommendations._load_apriori_model.cache_clear()

    try:
        first = apriori_recommendations._load_apriori_model()
        second = apriori_recommendations._load_apriori_model()
    finally:
        apriori_recommendations._load_apriori_model.cache_clear()

    assert first is second
    assert calls == [expected_path]


def test_incompatible_model_structure_returns_controlled_error(monkeypatch):
    monkeypatch.setattr(apriori_recommendations.joblib, "load", lambda _path: {})
    apriori_recommendations._load_apriori_model.cache_clear()

    try:
        with pytest.raises(RuntimeError, match="artifact is missing or incompatible"):
            apriori_recommendations._load_apriori_model()
    finally:
        apriori_recommendations._load_apriori_model.cache_clear()


def test_artifact_error_is_sanitized_by_api(monkeypatch, api_client):
    monkeypatch.setattr(
        apriori_recommendations,
        "_load_apriori_model",
        lambda: (_ for _ in ()).throw(
            RuntimeError("Apriori recommendations are unavailable because the model artifact is missing or incompatible.")
        ),
    )

    response = api_client.get(
        "/api/real-retail/features/recommendations/A100"
    )

    assert response.status_code == 503
    assert "private" not in response.text
