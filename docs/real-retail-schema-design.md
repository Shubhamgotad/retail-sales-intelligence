# Phase 2B real-retail schema and transformation design

## Scope

This phase adds a versioned design only. The SQL file is not executed, and no
production warehouse or synthetic seed data is changed.

## Isolated schema

[`database/real_retail_schema.sql`](../database/real_retail_schema.sql) creates
the `real_retail` schema idempotently:

- `real_retail.loads`: source checksum, filename, staging load ID, policy
  version, `UNKNOWN` currency, status, timestamps, and row counters.
- `real_retail.source_products`: normalized `StockCode`, nullable source
  description, and description-quality flag.
- `real_retail.source_customers`: normalized source `Customer ID` only; no
  fabricated names or attributes.
- `real_retail.sales_lines`: signed quantities, source prices, source
  geography, invoice/date values, all lineage and quality flags, and a
  uniqueness constraint on checksum + worksheet + worksheet row.

There are no cost, profit, discount, store, customer-name, or converted-currency
columns. `currency_code` is constrained to `UNKNOWN`.

## Transformation policy

[`backend/app/transformations/online_retail.py`](../backend/app/transformations/online_retail.py)
uses policy version:

```text
online-retail-v1-unknown-currency-signed-quantity
```

Source identifiers are trimmed, internal whitespace is collapsed, and
upper-cased. Excel-style integer customer values such as `123.0` normalize to
`123`. This is deterministic and does not invent identifiers.

Invoice values beginning with `C` are cancellation candidates. Negative
quantities are return candidates. Neither is treated as confirmed. Signed
quantities and original prices are preserved. Missing customers and
descriptions remain nullable. Zero and negative prices are flagged without
coercion.

Malformed dates, quantities, prices, missing source checksum, missing worksheet
identity, and invalid source row numbers raise `TransformationError`.

## Lineage and idempotency

The production-design uniqueness key is:

```text
source_sha256 + worksheet_name + worksheet_row_number
```

The staging schema already provides `staging_row_id` and the same source-line
identity. The design also retains `source_content_hash` for duplicate-content
analysis without deleting or deduplicating rows.

## Safe testing

From `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The unit tests do not connect to PostgreSQL. No live database, configured
production database, or staging write is required.

Before any future execution of the SQL file, use a disposable PostgreSQL
database or isolated review environment and verify object compatibility. The
SQL uses `CREATE ... IF NOT EXISTS`; incompatible pre-existing objects should
be detected and reviewed rather than altered or dropped.

## Phase 2D load readiness and reconciliation

[`backend/app/transformations/reconciliation.py`](../backend/app/transformations/reconciliation.py)
contains pure, side-effect-free checks for a future load. It does not connect
to PostgreSQL, read the workbook, or execute the schema file.

`validate_load_state` applies application-level completion rules:

- all row counters are nonnegative;
- `rows_loaded + rows_rejected == rows_read`;
- a completed load has `completed_at`;
- completed loads have no rejected rows by default (callers must explicitly
  opt into a rejected-row policy);
- failed and incomplete statuses cannot be treated as completed; and
- non-completed statuses cannot carry `completed_at`.

The current SQL DDL enforces column nullability, `UNKNOWN` currency, source-line
and load identity uniqueness, and foreign keys. It does **not** enforce the
cross-column count equation, status transitions, completion timestamps, or the
rejected-row policy. Those rules therefore remain application-level checks
until a later, explicitly approved schema change.

`reconcile` compares supplied source, staged, transformed, and target-ready
summaries. It verifies source-line identity and signed quantity preservation,
retains duplicate-content rows, counts cancellation and negative-quantity
candidates separately, preserves missing customer/description classifications,
requires `UNKNOWN` currency, and rejects supplied fabricated cost, profit,
store, discount, or converted-currency values. It returns named checks with
observed and expected values plus an overall `ready` decision.

Run the pure tests safely from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_reconciliation.py tests\test_online_retail_transformation.py
```

These tests do not require a database and do not perform a staging or
production load.
