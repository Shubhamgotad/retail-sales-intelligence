CREATE TABLE IF NOT EXISTS dim_product (
    product_id SERIAL PRIMARY KEY,
    product_name VARCHAR(150) NOT NULL,
    category VARCHAR(100) NOT NULL,
    subcategory VARCHAR(100),
    brand VARCHAR(100),
    unit_cost NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
    unit_price NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (unit_price >= 0)
);

CREATE TABLE IF NOT EXISTS dim_store (
    store_id SERIAL PRIMARY KEY,
    store_name VARCHAR(150) NOT NULL,
    city VARCHAR(100) NOT NULL,
    state VARCHAR(100),
    region VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS dim_customer (
    customer_id SERIAL PRIMARY KEY,
    customer_name VARCHAR(150) NOT NULL,
    gender VARCHAR(30),
    age_group VARCHAR(30),
    city VARCHAR(100),
    customer_segment VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_id INTEGER PRIMARY KEY,
    full_date DATE NOT NULL UNIQUE,
    day_number INTEGER NOT NULL,
    month_number INTEGER NOT NULL,
    month_name VARCHAR(20) NOT NULL,
    quarter_number INTEGER NOT NULL,
    year_number INTEGER NOT NULL,
    day_of_week VARCHAR(20) NOT NULL,
    is_weekend BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS fact_sales (
    sales_id BIGSERIAL PRIMARY KEY,
    transaction_id VARCHAR(100) NOT NULL,
    date_id INTEGER NOT NULL REFERENCES dim_date(date_id),
    product_id INTEGER NOT NULL REFERENCES dim_product(product_id),
    store_id INTEGER NOT NULL REFERENCES dim_store(store_id),
    customer_id INTEGER NOT NULL REFERENCES dim_customer(customer_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    discount_amount NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    total_amount NUMERIC(14,2) NOT NULL CHECK (total_amount >= 0),
    cost_amount NUMERIC(14,2) NOT NULL CHECK (cost_amount >= 0),
    profit NUMERIC(14,2) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fact_sales_date ON fact_sales(date_id);
CREATE INDEX IF NOT EXISTS idx_fact_sales_product ON fact_sales(product_id);
CREATE INDEX IF NOT EXISTS idx_fact_sales_store ON fact_sales(store_id);
CREATE INDEX IF NOT EXISTS idx_fact_sales_customer ON fact_sales(customer_id);
CREATE INDEX IF NOT EXISTS idx_fact_sales_transaction ON fact_sales(transaction_id);
CREATE INDEX IF NOT EXISTS idx_dim_product_category ON dim_product(category);
CREATE INDEX IF NOT EXISTS idx_dim_date_year_month ON dim_date(year_number, month_number);