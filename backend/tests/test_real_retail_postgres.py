"""Opt-in PostgreSQL checks for the isolated real_retail schema.

The test is deliberately skipped when REAL_RETAIL_TEST_DATABASE_URL is absent.
It never reads DATABASE_URL and never creates or drops a database.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DDL_PATH = PROJECT_ROOT / "database" / "real_retail_schema.sql"
DISPOSABLE_ENV = "REAL_RETAIL_TEST_DATABASE_URL"



def _test_database_url() -> str:
    raw_url = os.environ.get(DISPOSABLE_ENV)
    if not raw_url:
        pytest.skip(
            f"{DISPOSABLE_ENV} is not set; disposable PostgreSQL is unavailable"
        )

    parsed = make_url(raw_url)
    database = (parsed.database or "").lower()
    host = (parsed.host or "").lower()

    # Require an explicit local host; reject remote database connections.
    allowed_hosts = {"localhost", "127.0.0.1", "::1"}
    if host not in allowed_hosts:
        pytest.fail(
            f"{DISPOSABLE_ENV} must point to a local PostgreSQL host; "
            "refusing to execute SQL"
        )

    normal_names = {
        "retail_sales_db",
        "postgres",
        "template0",
        "template1",
    }
    disposable_markers = ("test", "tmp", "temp", "disposable", "ci")

    if (
        database in normal_names
        or not any(marker in database for marker in disposable_markers)
    ):
        pytest.fail(
            f"{DISPOSABLE_ENV} database name is not clearly disposable; "
            "refusing to execute SQL"
        )

    return raw_url



def _split_sql_statements(sql: str) -> list[str]:
    """Split SQL at statement terminators outside comments and quoted text."""
    statements: list[str] = []
    start = 0
    index = 0
    quote: str | None = None
    dollar_tag: str | None = None
    length = len(sql)

    while index < length:
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, index):
                index += len(dollar_tag)
                dollar_tag = None
            else:
                index += 1
            continue

        if quote is not None:
            if sql[index] == "\\" and quote == "'":
                index += 2
                continue
            if sql[index] == quote:
                if index + 1 < length and sql[index + 1] == quote:
                    index += 2
                else:
                    quote = None
                    index += 1
            else:
                index += 1
            continue

        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            comment_end = length if newline == -1 else newline + 1
            if not sql[start:index].strip():
                start = comment_end
            index = comment_end
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end == -1:
                raise ValueError("Unterminated SQL block comment")
            comment_end = end + 2
            if not sql[start:index].strip():
                start = comment_end
            index = comment_end
            continue
        if sql[index] in {"'", '"'}:
            quote = sql[index]
            index += 1
            continue
        if sql[index] == "$":
            end = sql.find("$", index + 1)
            if end != -1:
                candidate = sql[index : end + 1]
                if candidate == "$$" or (
                    candidate[1:-1].replace("_", "a").isalnum()
                    and candidate[1:-1][0].isalpha()
                ):
                    dollar_tag = candidate
                    index = end + 1
                    continue
        if sql[index] == ";":
            statement = sql[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1

    if quote is not None:
        raise ValueError("Unterminated SQL quoted string")
    if dollar_tag is not None:
        raise ValueError("Unterminated SQL dollar-quoted string")
    trailing = sql[start:].strip()
    if trailing:
        statements.append(trailing)
    return statements


def _statements() -> list[str]:
    statements = _split_sql_statements(DDL_PATH.read_text(encoding="utf-8"))
    assert statements
    return statements


def test_statement_parser_ignores_comments_and_quoted_semicolons():
    sql = """
        -- introductory comment; not SQL
        /* block comment; also not SQL */
        SELECT 'literal;value', "identifier;value";
        DO $$ BEGIN RAISE NOTICE 'body;value'; END $$;
    """
    assert _split_sql_statements(sql) == [
        "SELECT 'literal;value', \"identifier;value\"",
        "DO $$ BEGIN RAISE NOTICE 'body;value'; END $$",
    ]


@pytest.fixture
def disposable_connection():
    url = _test_database_url()
    engine = create_engine(url, pool_pre_ping=True)
    try:
        connection = engine.connect()
    except SQLAlchemyError as error:
        engine.dispose()
        pytest.skip(f"Disposable PostgreSQL is unavailable: {type(error).__name__}")

    transaction = connection.begin()
    try:
        # The real-retail DDL intentionally references the existing staging
        # lineage table. This minimal prerequisite is created inside the
        # transaction and is rolled back with all test objects and records.
        connection.execute(text("""
            CREATE TABLE public.stg_online_retail_source_rows (
                staging_row_id BIGINT PRIMARY KEY
            )
        """))
        yield connection
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def _execute_ddl(connection) -> None:
    for statement in _statements():
        connection.execute(text(statement))


def test_real_retail_ddl_twice_and_constraints(disposable_connection):
    connection = disposable_connection
    _execute_ddl(connection)
    connection.execute(text("""
        INSERT INTO real_retail.loads (
            source_filename, source_sha256, staging_load_id,
            transformation_policy_version, status, rows_read, rows_loaded
        ) VALUES (
            'fixture.xlsx', :checksum, 1, 'test-policy', 'completed', 1, 1
        )
    """), {"checksum": "a" * 64})
    connection.execute(text("""
        INSERT INTO real_retail.source_products (
            source_product_key, first_seen_real_load_id, last_seen_real_load_id
        ) VALUES ('SKU-1', 1, 1)
    """))
    connection.execute(text("""
        INSERT INTO real_retail.source_customers (
            source_customer_key, first_seen_real_load_id, last_seen_real_load_id
        ) VALUES ('CUSTOMER-1', 1, 1)
    """))
    connection.execute(text("""
        INSERT INTO public.stg_online_retail_source_rows (staging_row_id)
        VALUES (10)
    """))
    connection.execute(text("""
        INSERT INTO real_retail.sales_lines (
            real_load_id, staging_row_id, source_sha256, worksheet_name,
            worksheet_row_number, source_product_key, source_customer_key,
            signed_quantity, source_price, is_cancellation_candidate,
            is_negative_quantity_candidate, is_zero_price, is_negative_price,
            is_missing_customer, is_missing_description, is_possible_duplicate,
            source_content_hash
        ) VALUES (
            1, 10, :checksum, 'Year 2010-2011', 2, 'SKU-1', 'CUSTOMER-1',
            -2, 0, TRUE, TRUE, TRUE, FALSE, FALSE, TRUE, TRUE, :content_hash
        )
    """), {"checksum": "a" * 64, "content_hash": "b" * 64})

    # A second execution must be harmless and must preserve the fixture rows.
    _execute_ddl(connection)
    assert connection.execute(
        text("SELECT COUNT(*) FROM real_retail.sales_lines")
    ).scalar_one() == 1

    tables = connection.execute(text("""
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema = 'real_retail'
        ORDER BY table_name
    """)).all()
    assert [row[1] for row in tables] == [
        "loads",
        "sales_lines",
        "source_customers",
        "source_products",
    ]
    assert all(row[0] == "real_retail" for row in tables)

    unique_constraints = connection.execute(text("""
        SELECT constraint_name
        FROM information_schema.table_constraints
        WHERE table_schema = 'real_retail'
          AND constraint_type = 'UNIQUE'
    """)).scalars().all()
    assert "uq_real_retail_load_identity" in unique_constraints
    assert "uq_real_retail_source_line" in unique_constraints

    foreign_keys = connection.execute(text("""
        SELECT constraint_name
        FROM information_schema.table_constraints
        WHERE table_schema = 'real_retail'
          AND constraint_type = 'FOREIGN KEY'
    """)).scalars().all()
    assert len(foreign_keys) == 8


def test_real_retail_constraints_reject_bad_values(disposable_connection):
    connection = disposable_connection
    _execute_ddl(connection)
    connection.execute(text("""
        INSERT INTO real_retail.loads (
            source_filename, source_sha256, staging_load_id,
            transformation_policy_version, status
        ) VALUES ('fixture.xlsx', :checksum, 1, 'test-policy', 'running')
    """), {"checksum": "c" * 64})

    with pytest.raises(SQLAlchemyError):
        with connection.begin_nested():
            connection.execute(text("""
                INSERT INTO real_retail.sales_lines (
                    real_load_id, source_sha256, worksheet_name, worksheet_row_number,
                    signed_quantity, currency_code, is_cancellation_candidate,
                    is_negative_quantity_candidate, is_zero_price, is_negative_price,
                    is_missing_customer, is_missing_description, is_possible_duplicate,
                    source_content_hash
                ) VALUES (
                    1, :checksum, 'Sheet', 2, 1, 'GBP', FALSE, FALSE, FALSE, FALSE,
                    TRUE, FALSE, FALSE, :content_hash
                )
            """), {"checksum": "c" * 64, "content_hash": "d" * 64})

    connection.execute(text("""
        INSERT INTO real_retail.sales_lines (
            real_load_id, source_sha256, worksheet_name, worksheet_row_number,
            signed_quantity, currency_code, is_cancellation_candidate,
            is_negative_quantity_candidate, is_zero_price, is_negative_price,
            is_missing_customer, is_missing_description, is_possible_duplicate,
            source_content_hash
        ) VALUES (
            1, :checksum, 'Sheet', 2, -3, 'UNKNOWN', FALSE, TRUE, FALSE, FALSE,
            TRUE, FALSE, FALSE, :content_hash
        )
    """), {"checksum": "c" * 64, "content_hash": "d" * 64})

    with pytest.raises(SQLAlchemyError):
        with connection.begin_nested():
            connection.execute(text("""
                INSERT INTO real_retail.sales_lines (
                    real_load_id, source_sha256, worksheet_name, worksheet_row_number,
                    signed_quantity, currency_code, is_cancellation_candidate,
                    is_negative_quantity_candidate, is_zero_price, is_negative_price,
                    is_missing_customer, is_missing_description, is_possible_duplicate,
                    source_content_hash
                ) VALUES (
                    1, :checksum, 'Sheet', 2, -3, 'UNKNOWN', FALSE, TRUE, FALSE, FALSE,
                    TRUE, FALSE, FALSE, :content_hash
                )
            """), {"checksum": "c" * 64, "content_hash": "e" * 64})


def test_real_retail_foreign_keys_reject_invalid_references(disposable_connection):
    connection = disposable_connection
    _execute_ddl(connection)
    with pytest.raises(SQLAlchemyError):
        with connection.begin_nested():
            connection.execute(text("""
                INSERT INTO real_retail.sales_lines (
                    real_load_id, source_sha256, worksheet_name, worksheet_row_number,
                    signed_quantity, currency_code, is_cancellation_candidate,
                    is_negative_quantity_candidate, is_zero_price, is_negative_price,
                    is_missing_customer, is_missing_description, is_possible_duplicate,
                    source_content_hash
                ) VALUES (
                    999, :checksum, 'Sheet', 2, 1, 'UNKNOWN', FALSE, FALSE, FALSE,
                    FALSE, TRUE, FALSE, FALSE, :content_hash
                )
            """), {"checksum": "f" * 64, "content_hash": "e" * 64})


def test_staging_lineage_columns_match_transformer_requirements():
    staging_ddl = (
        PROJECT_ROOT / "backend" / "app" / "staging" / "online_retail.py"
    ).read_text(encoding="utf-8")
    required = (
        "staging_row_id",
        "source_sha256",
        "worksheet_name",
        "worksheet_row_number",
        "source_invoice_value",
        "source_date_value",
        "original_quantity",
        "original_price",
        "original_customer_value",
        "original_description",
        "is_possible_duplicate_content",
    )
    for column in required:
        assert column in staging_ddl
