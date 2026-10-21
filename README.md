# Retail Sales Intelligence & Product Recommendation Analytics

A full-stack retail analytics and data-warehouse project that explores transaction data, summarizes sales patterns, classifies product demand, groups customers, and discovers products frequently purchased together.

**Repository:** [Shubhamgotad/retail-sales-intelligence](https://github.com/Shubhamgotad/retail-sales-intelligence)

## Highlights

- **Sales overview:** transaction-line and invoice totals, product/customer counts, monthly trends, country comparisons, and data-quality indicators.
- **Product explorer:** searchable and sortable product-level metrics with pagination.
- **Demand analysis:** product-month demand classification based on historical signed quantities.
- **Customer clustering:** reproducible K-Means segmentation using invoice count, line count, signed quantity, and source monetary value.
- **Market-basket analysis:** Apriori association rules for products purchased together.
- **Data quality and auditability:** retains source records and flags cancellation candidates, potential returns, missing values, price anomalies, and possible duplicate content.
- **API documentation:** interactive FastAPI/Swagger docs at `/docs`.

## Tech stack

| Layer | Technologies |
| --- | --- |
| Frontend | React, TypeScript, Vite, Recharts, Lucide |
| Backend | Python, FastAPI, SQLAlchemy, Pydantic Settings |
| Database | PostgreSQL, SQL |
| Analytics / ML | Pandas, NumPy, scikit-learn, MLxtend |
| Testing | Pytest, Node.js built-in test runner |

## Architecture

```text
retail-sales-intelligence/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI route modules
│   │   ├── core/             # Application settings
│   │   ├── db/               # Database connections
│   │   ├── scripts/          # Import and processing commands
│   │   ├── services/         # Analytics queries and business logic
│   │   ├── staging/          # Source-data staging
│   │   └── transformations/  # Data validation and transformations
│   ├── tests/
│   └── requirements.txt
├── database/                 # PostgreSQL schema SQL
├── data/
│   ├── raw/                  # Local source files (not committed)
│   ├── processed/            # Data profiling and validation reports
│   └── templates/            # Input templates
├── docs/                     # Data pipeline and analytics documentation
└── frontend/
    ├── src/
    │   ├── pages/
    │   ├── services/
    │   └── utils/
    └── tests/
```

## Requirements

Install these locally before running the project:

- Python with pip
- Node.js and npm
- PostgreSQL
- The Online Retail II source workbook, downloaded separately from the [UCI Machine Learning Repository](https://archive.ics.uci.edu/dataset/502/online+retail+ii)

This project does **not** require Docker.

## Getting started

Run commands from the repository root unless a command explicitly changes directory.

### 1. Configure PostgreSQL

Create/configure the application database and the isolated disposable database following your local setup. The default application connection setting is read from `backend/.env`.

Create `backend/.env` locally with your own PostgreSQL credentials:

```dotenv
DATABASE_URL=postgresql+psycopg://<user>:<url-encoded-password>@localhost:5432/retail_sales_db
```

Replace the placeholders with your local connection details. URL-encode reserved characters in the password. **Do not commit `backend/.env` or share database credentials.**

The real Online Retail II analytics connection is deliberately separate. It must use a local PostgreSQL database named exactly `retail_sales_test_disposable`, and the analytics API opens read-only transactions against it. Do not point the real-retail connection at `retail_sales_db`.

### 2. Set up the backend

In PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Prepare the real-retail dataset

Download the UCI Online Retail II workbook and place it at:

```text
data/raw/online_retail_II.xlsx
```

The raw workbook is intentionally excluded from Git. Follow the pipeline documentation for staging, validation, schema preparation, and loading into the disposable `real_retail` schema:

- [Online Retail II staging pipeline](docs/online-retail-staging.md)
- [Real-retail analytics, endpoints, and cost-data policy](docs/real-retail-analytics.md)
- [Real-retail schema design](docs/real-retail-schema-design.md)

The loading process is designed to preserve source information and reconcile row counts. Use the documented commands and target checks; do not rerun a load against an already-populated database without first reviewing the load's idempotency and reconciliation behavior.

### 4. Start the backend

From the `backend/` directory, set the isolated analytics database URL in the **same PowerShell session** that starts the API:

```powershell
$env:REAL_RETAIL_ANALYTICS_DATABASE_URL = 'postgresql+psycopg://<user>:<url-encoded-password>@localhost:5432/retail_sales_test_disposable'
python -m uvicorn app.main:app --reload
```

Replace the placeholders locally. Keep the value private. The backend defaults to `http://127.0.0.1:8000`.

Useful endpoints:

- `GET /` — API welcome response
- `GET /health/live` — liveness check
- `GET /health` — application database connectivity check
- `GET /docs` — interactive API documentation
- `GET /api/real-retail/analytics/overview` — real dataset summary
- `GET /api/real-retail/analytics/monthly` — monthly metrics
- `GET /api/real-retail/analytics/countries` — country metrics
- `GET /api/real-retail/analytics/products` — product metrics
- `GET /api/real-retail/analytics/customers` — customer metrics
- `GET /api/real-retail/analytics/quality` — data-quality metrics
- `GET /api/real-retail/features/demand` — demand classification
- `GET /api/real-retail/features/clustering?n_clusters=3` — customer clusters
- `GET /api/real-retail/features/basket?min_support=0.01&min_confidence=0.2` — basket rules

### 5. Start the frontend

Open a second PowerShell terminal:

```powershell
cd frontend
npm install
npm run dev
```

Vite prints the local development URL when the server starts. By default, the frontend calls the API on the same origin; if your local frontend setup uses a separate API origin, set `VITE_API_URL` to the backend base URL before starting Vite.

Create a production build with:

```powershell
npm run build
```

## Testing

Backend tests (from `backend/`):

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Frontend unit tests (from `frontend/`):

```powershell
npm run test:unit
```

Some PostgreSQL integration tests require the explicitly configured disposable test database. Review test configuration before running database-dependent tests; never point them at a database containing data you need to preserve.

## Data interpretation and limitations

- The project uses the UCI **Online Retail II** transaction dataset. Its source monetary values are in pounds sterling (GBP).
- Source monetary totals are calculated from signed quantity multiplied by unit price. They should not automatically be interpreted as verified net revenue, accounting revenue, or profit.
- The workbook does not supply product cost/COGS. Gross profit and gross margin must not be invented or inferred from a guessed margin. Verified profitability is available only when supported by appropriately sourced, effective-dated product unit-cost records that match product, date, and currency.
- Invoice IDs beginning with `C` are treated as cancellation candidates; negative quantities are potential return adjustments, not independently confirmed returns.
- The real-retail analytics API is isolated from the main application database and requires the exact local disposable database name. It does not silently fall back to the application database or synthetic data.

## Security and Git hygiene

Do not commit credentials, environment files, virtual environments, build output, or raw source datasets. The repository's `.gitignore` excludes `backend/.env`, virtual environments, frontend build output, and files under `data/raw/` (except the tracked placeholder).

## Documentation

- [Real-retail analytics and API behavior](docs/real-retail-analytics.md)
- [Online Retail II staging pipeline](docs/online-retail-staging.md)
- [Real-retail schema design](docs/real-retail-schema-design.md)

## License

No project-specific open-source license is currently declared. Contact the repository owner before redistributing or reusing the project beyond applicable dataset terms.
