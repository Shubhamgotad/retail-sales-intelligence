INSERT INTO dim_product (product_name, category, subcategory, brand, unit_cost, unit_price) VALUES
('Laptop Pro 14', 'Electronics', 'Computers', 'TechNova', 52000, 65000),
('Wireless Mouse', 'Electronics', 'Accessories', 'ClickPro', 350, 599),
('Mechanical Keyboard', 'Electronics', 'Accessories', 'KeyCraft', 1800, 2999),
('Smartphone X', 'Electronics', 'Mobiles', 'TechNova', 18000, 24999),
('USB-C Hub', 'Electronics', 'Accessories', 'ConnectIt', 900, 1499),
('Office Chair', 'Furniture', 'Chairs', 'ComfortCo', 4200, 6999),
('Study Desk', 'Furniture', 'Desks', 'ComfortCo', 5500, 8999),
('Notebook Pack', 'Stationery', 'Notebooks', 'PaperPlus', 80, 149),
('Ballpoint Pens', 'Stationery', 'Writing', 'PaperPlus', 30, 59),
('Water Bottle', 'Lifestyle', 'Drinkware', 'HydroMax', 250, 449),
('Running Shoes', 'Fashion', 'Footwear', 'ActiveStep', 1600, 2799),
('Backpack', 'Fashion', 'Bags', 'CarryWell', 700, 1299)
ON CONFLICT DO NOTHING;

INSERT INTO dim_store (store_name, city, state, region) VALUES
('Mumbai Central', 'Mumbai', 'Maharashtra', 'West'),
('Pune Market', 'Pune', 'Maharashtra', 'West'),
('Bengaluru Hub', 'Bengaluru', 'Karnataka', 'South'),
('Delhi NCR', 'New Delhi', 'Delhi', 'North'),
('Hyderabad Square', 'Hyderabad', 'Telangana', 'South')
ON CONFLICT DO NOTHING;

INSERT INTO dim_customer (customer_name, gender, age_group, city, customer_segment) VALUES
('Customer 001', 'Female', '18-25', 'Mumbai', 'Regular'),
('Customer 002', 'Male', '26-35', 'Pune', 'Premium'),
('Customer 003', 'Female', '26-35', 'Bengaluru', 'Regular'),
('Customer 004', 'Male', '36-45', 'New Delhi', 'Occasional'),
('Customer 005', 'Female', '18-25', 'Hyderabad', 'Regular'),
('Customer 006', 'Male', '26-35', 'Mumbai', 'Premium'),
('Customer 007', 'Female', '36-45', 'Pune', 'Occasional'),
('Customer 008', 'Male', '18-25', 'Bengaluru', 'Regular'),
('Customer 009', 'Female', '26-35', 'New Delhi', 'Premium'),
('Customer 010', 'Male', '36-45', 'Hyderabad', 'Regular')
ON CONFLICT DO NOTHING;

INSERT INTO dim_date (
    date_id, full_date, day_number, month_number, month_name,
    quarter_number, year_number, day_of_week, is_weekend
)
SELECT
    TO_CHAR(d::date, 'YYYYMMDD')::INTEGER,
    d::date,
    EXTRACT(DAY FROM d)::INTEGER,
    EXTRACT(MONTH FROM d)::INTEGER,
    TO_CHAR(d, 'FMMonth'),
    EXTRACT(QUARTER FROM d)::INTEGER,
    EXTRACT(YEAR FROM d)::INTEGER,
    TO_CHAR(d, 'FMDay'),
    EXTRACT(ISODOW FROM d) IN (6, 7)
FROM generate_series(
    DATE '2025-01-01',
    DATE '2026-12-31',
    INTERVAL '1 day'
) AS d
ON CONFLICT (date_id) DO NOTHING;

INSERT INTO fact_sales (
    transaction_id, date_id, product_id, store_id, customer_id,
    quantity, unit_price, discount_amount, total_amount, cost_amount, profit
)
SELECT
    'TXN-' || LPAD(g::TEXT, 6, '0'),
    TO_CHAR(
        DATE '2025-01-01' + ((g * 7 + g / 4) % 730)::INTEGER,
        'YYYYMMDD'
    )::INTEGER,
    ((g * 7 + g / 3) % 12 + 1)::INTEGER,
    ((g * 3) % 5 + 1)::INTEGER,
    ((g * 7) % 10 + 1)::INTEGER,
    ((g * 5) % 4 + 1)::INTEGER,
    p.unit_price,
    ROUND((p.unit_price * (((g % 4) * 0.025)::NUMERIC)), 2),
    ROUND(
        p.unit_price * ((g * 5) % 4 + 1)
        - p.unit_price * (((g % 4) * 0.025)::NUMERIC),
        2
    ),
    ROUND(p.unit_cost * ((g * 5) % 4 + 1), 2),
    ROUND(
        (p.unit_price - p.unit_cost) * ((g * 5) % 4 + 1)
        - p.unit_price * (((g % 4) * 0.025)::NUMERIC),
        2
    )
FROM generate_series(1, 1000) AS g
JOIN dim_product p
    ON p.product_id = ((g * 7 + g / 3) % 12 + 1)
WHERE NOT EXISTS (
    SELECT 1
    FROM fact_sales f
    WHERE f.transaction_id = 'TXN-' || LPAD(g::TEXT, 6, '0')
);