"""Apriori recommendations backed by the trained, read-only model artifact."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
import pickle
from typing import Any

import joblib
from pandas import DataFrame
from sqlalchemy.engine import Connection

from app.services.real_retail_analytics import (
    _VALID_PRODUCT_DESCRIPTION,
    _rows,
    is_valid_product_description,
)

_MODEL_ERROR = (
    "Apriori recommendations are unavailable because the model artifact "
    "is missing or incompatible."
)
_FREQUENT_ITEMSET_COLUMNS = {"support", "itemsets"}
_RULE_COLUMNS = {"antecedents", "consequents", "support", "confidence", "lift"}


def _artifact_path() -> Path:
    return Path(__file__).resolve().parents[3] / "artifacts" / "apriori_model.pkl"


def _validate_model(model: Any) -> Mapping[str, Any]:
    required_keys = {
        "frequent_itemsets",
        "rules",
        "recommendations_lookup",
        "item_descriptions",
    }
    if not isinstance(model, Mapping) or not required_keys.issubset(model):
        raise ValueError("Invalid Apriori model structure")
    frequent_itemsets = model["frequent_itemsets"]
    rules = model["rules"]
    if (
        not isinstance(frequent_itemsets, DataFrame)
        or not _FREQUENT_ITEMSET_COLUMNS.issubset(frequent_itemsets.columns)
        or not isinstance(rules, DataFrame)
        or not _RULE_COLUMNS.issubset(rules.columns)
    ):
        raise ValueError("Invalid Apriori model tables")

    lookup = model["recommendations_lookup"]
    descriptions = model["item_descriptions"]
    if not isinstance(lookup, Mapping) or not isinstance(descriptions, Mapping):
        raise ValueError("Invalid Apriori model mappings")
    if any(
        not isinstance(code, str)
        or not code.strip()
        or not isinstance(recommendations, list)
        or any(not isinstance(item, str) or not item.strip() for item in recommendations)
        or len(recommendations) != len(set(recommendations))
        for code, recommendations in lookup.items()
    ):
        raise ValueError("Invalid Apriori recommendation entries")
    if any(
        not isinstance(code, str) or not isinstance(description, str)
        or not description.strip()
        for code, description in descriptions.items()
    ):
        raise ValueError("Invalid Apriori product descriptions")
    if any(code not in descriptions for code in lookup) or any(
        recommendation not in descriptions
        for recommendations in lookup.values()
        for recommendation in recommendations
    ):
        raise ValueError("Apriori product descriptions are missing recommendation codes")
    return model


@lru_cache(maxsize=1)
def _load_apriori_model() -> Mapping[str, Any]:
    try:
        return _validate_model(joblib.load(_artifact_path()))
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


def recommendations(connection: Connection, product_code: str) -> dict[str, Any]:
    code = product_code.strip()
    if not code or len(code) > 128 or any(ord(character) < 32 for character in code):
        raise ValueError("Enter a valid product code.")

    model = _load_apriori_model()
    recommendation_codes = model["recommendations_lookup"].get(code, [])
    candidate_codes = list(dict.fromkeys([code, *recommendation_codes]))
    placeholders = []
    params: dict[str, str] = {}
    for index, candidate_code in enumerate(candidate_codes):
        parameter = f"product_code_{index}"
        placeholders.append(f":{parameter}")
        params[parameter] = candidate_code

    rows = _rows(
        connection,
        f"""
        SELECT source_product_key AS product_code,
               MAX(source_description) FILTER (
                   WHERE {_VALID_PRODUCT_DESCRIPTION}
               ) AS product_name
        FROM real_retail.sales_lines
        WHERE source_product_key IN ({", ".join(placeholders)})
        GROUP BY source_product_key
        """,
        params,
    )
    product_names = {
        str(row["product_code"]): row["product_name"]
        for row in rows
        if is_valid_product_description(row["product_name"])
    }
    product_name = product_names.get(code)
    if product_name is None:
        raise LookupError("Product code was not found or has no valid description.")

    valid_recommendations = [
        {"product_code": candidate, "product_name": product_names[candidate]}
        for candidate in recommendation_codes
        if candidate != code and candidate in product_names
    ]
    message = None
    if not recommendation_codes:
        message = "No Apriori recommendations are available for this product."
    elif not valid_recommendations:
        message = "No current recommendations with valid product descriptions are available."

    return {
        "algorithm": "Apriori",
        "product_code": code,
        "product_name": product_name,
        "recommendations": valid_recommendations,
        "message": message,
    }
