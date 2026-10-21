import pytest

from app.scripts.load_real_retail import split_sql_statements, validate_destination_url


def test_runner_sql_parser_ignores_leading_comments_and_quoted_semicolons():
    assert split_sql_statements(
        "-- comment;\n/* block; */ SELECT 'value;'; SELECT 2;"
    ) == ["SELECT 'value;'", "SELECT 2"]


def test_destination_url_requires_exact_local_disposable_database():
    url = validate_destination_url(
        "postgresql+psycopg://user:password@localhost:5432/retail_sales_test_disposable"
    )
    assert url.database == "retail_sales_test_disposable"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://user:password@localhost:5432/retail_sales_db",
        "postgresql+psycopg://user:password@example.com:5432/retail_sales_test_disposable",
    ],
)
def test_destination_url_rejects_application_or_remote_targets(url):
    with pytest.raises(ValueError):
        validate_destination_url(url)
