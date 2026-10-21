# Online Retail II staging validation report

This report contains aggregate diagnostics only. Raw customer values and customer IDs are not included.

## Load metadata

- Mode: staging write
- Load ID: 1
- Source filename: `online_retail_II.xlsx`
- Source SHA-256: `bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980`
- Currency: unconfirmed; source monetary values were not converted

## Counts

- Rows read: 1,067,371
- Rows staged: 1,067,371
- Rejected/error rows: 0
- Distinct invoices: 53,628
- Distinct stock codes: 5,304
- Possible duplicate-content rows: 67,242
- Cancellation-invoice rows: 19,494
- Potential-return rows (negative quantity): 22,950
- Negative-price rows: 5
- Zero-price rows: 6,202

## Rows by worksheet

- `Year 2009-2010`: 525,461
- `Year 2010-2011`: 541,910

## Missing values

- `Customer ID`: 243,007
- `Description`: 4,382

## Quantity and monetary totals

Monetary totals are `Quantity × Price` in GBP, as specified by the UCI Online Retail II documentation for `UnitPrice`; no conversion is applied.
The inclusion categories are mutually exclusive for reporting, using precedence: cancellation invoice,
potential return, negative-price anomaly, zero-price line, then positive nonzero price.

| Inclusion category | Quantity total | Monetary total |
|---|---:|---:|
| `all_rows` | 10608492 | 19287250.568 |
| `cancellation_invoice` | -490992 | -1526667.86 |
| `negative_price_anomaly` | 5 | -158676.14 |
| `positive_nonzero_price` | 11420305 | 20972594.568 |
| `potential_return` | -573085 | 0 |
| `zero_price_line` | 252259 | 0 |

## Limitations

- Negative quantities are classified as potential returns, not confirmed returns.
- Cancellation invoices and potential returns are retained in staging.
- Product cost, profit, store, customer identity, and currency conversion are not created.
- Staging rows are not production `dim_*` or `fact_sales` rows.
