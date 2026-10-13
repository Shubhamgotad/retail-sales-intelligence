-- Phase 2B isolated real-data design.
-- Review and execute separately; this file is not run by the application.
CREATE SCHEMA IF NOT EXISTS real_retail;

CREATE TABLE IF NOT EXISTS real_retail.loads (
    real_load_id BIGSERIAL PRIMARY KEY,
    source_filename TEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    staging_load_id BIGINT NOT NULL,
    transformation_policy_version VARCHAR(40) NOT NULL,
    currency_code VARCHAR(20) NOT NULL DEFAULT 'UNKNOWN',
    status VARCHAR(20) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    rows_read BIGINT NOT NULL DEFAULT 0,
    rows_loaded BIGINT NOT NULL DEFAULT 0,
    rows_rejected BIGINT NOT NULL DEFAULT 0,
    failure_summary TEXT,
    CONSTRAINT ck_real_retail_load_currency_unknown
        CHECK (currency_code = 'UNKNOWN'),
    CONSTRAINT uq_real_retail_load_identity
        UNIQUE (source_sha256, transformation_policy_version)
);

CREATE TABLE IF NOT EXISTS real_retail.source_products (
    source_product_key TEXT PRIMARY KEY,
    source_description TEXT,
    is_missing_description BOOLEAN NOT NULL DEFAULT FALSE,
    first_seen_real_load_id BIGINT NOT NULL REFERENCES real_retail.loads(real_load_id),
    last_seen_real_load_id BIGINT NOT NULL REFERENCES real_retail.loads(real_load_id)
);

CREATE TABLE IF NOT EXISTS real_retail.source_customers (
    source_customer_key TEXT PRIMARY KEY,
    first_seen_real_load_id BIGINT NOT NULL REFERENCES real_retail.loads(real_load_id),
    last_seen_real_load_id BIGINT NOT NULL REFERENCES real_retail.loads(real_load_id)
);

CREATE TABLE IF NOT EXISTS real_retail.sales_lines (
    real_sales_line_id BIGSERIAL PRIMARY KEY,
    real_load_id BIGINT NOT NULL REFERENCES real_retail.loads(real_load_id),
    staging_row_id BIGINT REFERENCES public.stg_online_retail_source_rows(staging_row_id),
    source_sha256 CHAR(64) NOT NULL,
    worksheet_name TEXT NOT NULL,
    worksheet_row_number INTEGER NOT NULL,
    source_invoice TEXT,
    invoice_datetime TIMESTAMP,
    source_product_key TEXT REFERENCES real_retail.source_products(source_product_key),
    source_customer_key TEXT REFERENCES real_retail.source_customers(source_customer_key),
    source_description TEXT,
    source_country TEXT,
    signed_quantity NUMERIC(18, 6),
    source_price NUMERIC(18, 6),
    currency_code VARCHAR(20) NOT NULL DEFAULT 'UNKNOWN',
    is_cancellation_candidate BOOLEAN NOT NULL,
    is_negative_quantity_candidate BOOLEAN NOT NULL,
    is_zero_price BOOLEAN NOT NULL,
    is_negative_price BOOLEAN NOT NULL,
    is_missing_customer BOOLEAN NOT NULL,
    is_missing_description BOOLEAN NOT NULL,
    is_possible_duplicate BOOLEAN NOT NULL,
    source_content_hash CHAR(64) NOT NULL,
    CONSTRAINT ck_real_retail_sales_currency_unknown
        CHECK (currency_code = 'UNKNOWN'),
    CONSTRAINT uq_real_retail_source_line
        UNIQUE (source_sha256, worksheet_name, worksheet_row_number)
);

CREATE INDEX IF NOT EXISTS idx_real_retail_sales_load
    ON real_retail.sales_lines(real_load_id);
CREATE INDEX IF NOT EXISTS idx_real_retail_sales_invoice
    ON real_retail.sales_lines(source_invoice);
CREATE INDEX IF NOT EXISTS idx_real_retail_sales_product
    ON real_retail.sales_lines(source_product_key);
CREATE INDEX IF NOT EXISTS idx_real_retail_sales_customer
    ON real_retail.sales_lines(source_customer_key);
CREATE INDEX IF NOT EXISTS idx_real_retail_sales_date
    ON real_retail.sales_lines(invoice_datetime);
