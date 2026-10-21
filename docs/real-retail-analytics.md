# Real-retail analytics

The isolated read-only analytics API is exposed under:

```text
/api/real-retail/analytics
```

Configure the API process with `REAL_RETAIL_ANALYTICS_DATABASE_URL`. It must
point to the local disposable PostgreSQL database
`retail_sales_test_disposable`. The API never falls back to `DATABASE_URL` or
the synthetic warehouse. Each request uses a transaction marked
`READ ONLY`; the database role should also have only read privileges in a
deployed environment.

## Endpoints

- `GET /overview` — line, invoice, product, customer, signed-quantity,
  source-monetary, verified-cost profitability availability, and data-quality
  totals.
- `GET /monthly?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` — chronological
  monthly line/invoice, signed quantity, source monetary, cancellation
  candidate, and potential-return totals.
- `GET /countries?sort=value&limit=25` — country totals. Supported `sort`
  values are `lines`, `invoices`, `quantity`, and `value`. Missing countries
  are returned as `[missing]`.
- `GET /products?sort=value&limit=25&offset=0` — paginated source-product
  totals, anomaly counts, and verified actual cost/gross-profit/gross-margin
  when every eligible line has exactly one matching effective-dated unit cost.
  Products are grouped by the stable source product identifier; descriptions
  are display labels only.
- `GET /customers?sort=value&limit=25&offset=0` — paginated identifiable
  customer totals. Missing customer IDs are excluded from rankings and are
  reported by the overview/quality endpoints.
- `GET /quality` — missing-value, signed/negative quantity, candidate
  classification, zero/negative price, and duplicate-content counts plus
  proportions.

The original application feature pages can select the same isolated source
through the `Real Online Retail II` source selector:

- `GET /api/real-retail/features/demand` — product-month signed-quantity
  demand classes. Negative quantities and cancellation candidates remain in
  the training history.
- `GET /api/real-retail/features/clustering?n_clusters=3` — reproducible
  K-Means clusters of identifiable customers using invoice count, line count,
  signed quantity, and source monetary value. Missing customer IDs are
  excluded from individual clusters and reported separately.
- `GET /api/real-retail/features/basket?min_support=0.01&min_confidence=0.2`
  — Apriori baskets keyed by source invoice and source product identifier.
  Cancellation-invoice candidates and non-positive quantities are excluded
  from co-occurrence calculations under the documented real-data policy.

The frontend keeps a `Synthetic demo` selector for the original warehouse
routes. Selecting the real source never falls back to synthetic data when the
isolated database is unavailable; it displays the API error instead.

Monetary totals are serialized as decimal strings to preserve PostgreSQL
`NUMERIC` precision. The [UCI Online Retail II documentation](https://archive.ics.uci.edu/dataset/502/online+retail+ii)
defines `UnitPrice` in sterling (£), so API currency metadata is `GBP`; no
conversion is applied. Source monetary totals are signed `Quantity × UnitPrice`
values, not verified revenue or profit. The reported stored currency marker
may remain `UNKNOWN`; GBP attribution comes from the source documentation.

Verified product profitability is calculated only from a persisted
`real_retail.product_unit_costs` record that matches the stable product key,
the transaction date within its inclusive effective period, and `GBP`
currency. Per line, actual cost is signed quantity multiplied by the matched
unit cost; actual gross profit is signed sales value minus that signed cost.
Negative quantities retain their sign and reduce net sales, cost, and profit.
There is no currency conversion. Non-GBP unit costs are never combined with
GBP sales.

Invoice IDs prefixed with `C` are cancellation candidates and are excluded
from actual profitability under the dataset convention; the dataset provides
no independent confirmation flag. Other negative quantities are retained as
potential return adjustments, not asserted to be confirmed returns. Lines
with missing quantity or price are excluded from profitability; lines missing
a product key or transaction date cannot match a cost. Missing or overlapping
cost matches produce a `profitability_status` explaining why totals are
unavailable. Aggregate actual cost, gross profit, and margin are returned only
when exactly one cost record matches every eligible line; partial totals are
not presented as complete profit. A zero signed-sales denominator yields a
null gross margin. Lines without invoice IDs remain included as ambiguous
records and are counted separately. Duplicate-content lines remain
represented.

## Preparing verified product unit costs

[`data/templates/product_unit_costs_template.csv`](../data/templates/product_unit_costs_template.csv)
is a header-only template; no product or cost examples are included. Its
columns, in order, are `stock_code`, `unit_cost`, `currency_code`,
`effective_from`, `effective_to`, and `source_reference`.

The pure validation helpers in
[`backend/app/transformations/product_costs.py`](../backend/app/transformations/product_costs.py)
and explicit CLI in
[`backend/app/scripts/import_product_costs.py`](../backend/app/scripts/import_product_costs.py)
validate future imports. The importer reads real product keys from
`real_retail.source_products` in the disposable destination itself; it does
not trust a caller-supplied product list. This mapping follows the existing
transformation policy: `StockCode` is trimmed, internal whitespace collapsed,
upper-cased, and normalized for Excel-style integer `.0` values before it is
stored as `source_product_key`.

CSV records are parsed by header, preserve unit costs as exact decimal strings,
and require non-negative finite decimal amounts which fit the target
`NUMERIC(18, 6)` without rounding. They require a recognized explicit ISO
4217 currency code and a real `YYYY-MM-DD` `effective_from` date.
`effective_to` may be blank for an open-ended period; when present it is
inclusive and may not precede `effective_from`. `source_reference` must
identify a supplier document or other verifiable evidence. Every rejected CSV
row is reported with its CSV row number and reason. The import is all-or-nothing:
if any row is rejected, no accepted rows from that file are persisted.

The importer defaults to validation-only. Persistence requires `--apply`, the
explicit `--confirm-disposable-target retail_sales_test_disposable` argument,
and `REAL_RETAIL_TEST_DATABASE_URL` pointing to a local database with that
exact database name. It reads that variable from the process environment (it
does not load `.env` itself), and accepts the SQLAlchemy PostgreSQL/psycopg URL
form:

```text
postgresql+psycopg://<user>:<url-encoded-password>@localhost:<port>/retail_sales_test_disposable
```

Set the value locally in the same PowerShell session used to run the CLI, after
replacing every placeholder with the connection details supplied by the
database administrator:

```powershell
$env:REAL_RETAIL_TEST_DATABASE_URL = 'postgresql+psycopg://<user>:<url-encoded-password>@localhost:<port>/retail_sales_test_disposable'
```

Do not commit this value or put credentials in documentation. URL-encode
reserved characters in the password. The importer rejects non-local hosts,
non-PostgreSQL URLs, any database name other than
`retail_sales_test_disposable`, and a connected `current_database()` identity
that does not match. It never reads `DATABASE_URL` or connects to
`retail_sales_db`.

Before importing, verify that the disposable database has
`real_retail.source_products` and the expected source-product key column. The
cost table is an additive `CREATE TABLE IF NOT EXISTS` block in
`database/real_retail_schema.sql`; the importer does not execute DDL. Apply
only that block after positively verifying the disposable target and only if
the table is absent. A header-only CSV can then be dry-run; it reports zero
data rows and writes no records.

An explicit `--currency-mismatch-policy` is required: `reject` rejects rows
whose currency differs from the documented GBP sales currency, while
`keep-separate` stores another valid currency as-is with a warning and no
conversion. Before any insert, the importer takes a table lock and rejects
duplicate or overlapping product/currency effective periods found either in
the CSV or in existing costs. Existing records are never updated or replaced.
The schema adds an effective-dated
`real_retail.product_unit_costs` table with `NUMERIC(18, 6)` values, a foreign
key to source products, and basic nonnegative/date constraints; application
validation handles effective-period overlap.

Example validation (no write):

```powershell
.\.venv\Scripts\python.exe -m app.scripts.import_product_costs `
  --file ..\data\templates\product_unit_costs_template.csv `
  --confirm-disposable-target retail_sales_test_disposable `
  --currency-mismatch-policy reject
```

Only after reviewing verified, populated source records and accepted/rejected
row output, persistence can be explicitly requested by adding `--apply`.
Actual profitability reads these verified costs only when product key, date,
and currency all match.

Effective periods support the dataset's multi-year history. Overlapping
inclusive periods for the same product and currency with different unit costs
are rejected; overlap for another currency or product is independent. A future
cost importer or profit calculation must not silently convert currencies:
cost and sales values with different currency codes must remain separate or be
explicitly rejected until an approved conversion policy exists. Verified cost
evidence and successful effective-date/currency matching are prerequisites for
actual COGS, gross-profit, or gross-margin reporting.

The isolated database was loaded before that currency provenance was confirmed
and retains its original `UNKNOWN` marker in stored row metadata. No schema or
data migration is performed for this display/metadata correction; analytics
responses now report GBP based on the UCI source documentation.

From `backend/`, run the unit tests with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_real_retail_analytics.py
```

Use the already-created disposable database for integration verification only;
never point this API configuration at `retail_sales_db`.
