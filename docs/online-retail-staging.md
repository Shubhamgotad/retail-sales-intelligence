# Online Retail II staging pipeline

The Phase 2 importer is intentionally separate from the production warehouse.
It only writes to `stg_online_retail_loads` and
`stg_online_retail_source_rows`; it never inserts into or changes
`dim_product`, `dim_store`, `dim_customer`, `dim_date`, or `fact_sales`.

## Validation-only (safe default)

From `backend/`:

```powershell
.\.venv\Scripts\python.exe -m app.scripts.stage_online_retail `
  --file ..\data\raw\online_retail_II.xlsx
```

This reads both transaction worksheets, calculates classifications and totals,
and writes an aggregate report to
`data/processed/online_retail_staging_validation.md`. It does not open a
database connection or insert rows.

## Explicit staging write

Only run after reviewing the validation report:

```powershell
.\.venv\Scripts\python.exe -m app.scripts.stage_online_retail `
  --file ..\data\raw\online_retail_II.xlsx `
  --write
```

The command creates only the two `stg_online_retail_*` tables and records a
load attempt. Source-line uniqueness is enforced by
`source_sha256 + worksheet_name + worksheet_row_number`, so repeating the same
file is idempotent. Each repeat still records a load attempt; duplicate source
rows are skipped by the staging unique constraint.

## Classifications

Each source row retains its original values and receives explicit flags for:

- cancellation invoice: invoice starts with `C`, case-insensitive;
- potential return: negative quantity, not a confirmed return;
- negative price anomaly;
- zero-price line;
- missing customer ID;
- missing description;
- possible duplicate content: the complete source row content occurs more than once.

Cancellation and return rows are retained. No currency is assumed or converted,
and no cost, profit, store, customer identity, or product dimension record is
created by this phase.

## Limitations and review gate

The workbook does not provide product cost, profit, store, or an explicit
currency field. The current production fact table also requires positive
quantities and non-negative amounts, so production mapping requires approved
business policies for returns, currency provenance, missing customers, and
unknown stores. This phase does not make those decisions.
