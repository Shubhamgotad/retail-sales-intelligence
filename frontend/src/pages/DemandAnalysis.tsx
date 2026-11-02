import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ApiError, api } from "../services/api";
import type { RandomForestDemandForecast, RealRetailDemandResponse, RealRetailProduct } from "../types";
import { ErrorState } from "./Dashboard";
import { formatCompactNumber } from "../utils/format";
import { paginate } from "../utils/pagination";
import { isValidProductDescription } from "../utils/productDescription";
import { PRODUCT_SEARCH_DEBOUNCE_MS, scheduleDebounced } from "../utils/debounce";
import { createRequestSequence } from "../utils/requestSequence";

type DemandView = { id: string; name: string; month: string; units: number; label: string };
type DemandSort = "name" | "units" | "month" | "class";

export const normalize = (row: RealRetailDemandResponse["predictions"][number]): DemandView | null =>
  isValidProductDescription(row.product_name) ? ({
    id: String(row.product_id),
    name: row.product_name,
    month: row.last_observed_month,
    units: row.last_month_units,
    label: row.predicted_demand.toLowerCase(),
  }) : null;

export function getDemandDistribution(rows: DemandView[]) {
  const total = rows.length;
  const counts = rows.reduce<Record<string, number>>((result, row) => {
    result[row.label] = (result[row.label] ?? 0) + 1;
    return result;
  }, {});
  return ["high", "medium", "low"].map((key) => ({
    key,
    name: key.charAt(0).toUpperCase() + key.slice(1),
    products: counts[key] ?? 0,
    percentage: total ? ((counts[key] ?? 0) / total) * 100 : 0,
  }));
}

export function filterAndSortDemandRows(
  rows: DemandView[],
  query: string,
  selectedClass: string | null,
  sort: DemandSort,
): DemandView[] {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  return rows.filter((row) =>
    (!selectedClass || row.label === selectedClass) &&
    (!normalizedQuery || `${row.name} ${row.id}`.toLocaleLowerCase().includes(normalizedQuery)),
  ).sort((left, right) => {
    if (sort === "units") return right.units - left.units || left.id.localeCompare(right.id);
    if (sort === "month") return right.month.localeCompare(left.month) || left.id.localeCompare(right.id);
    if (sort === "class") return left.label.localeCompare(right.label) || left.id.localeCompare(right.id);
    return left.name.localeCompare(right.name) || left.id.localeCompare(right.id);
  });
}

export function DemandTableRows({ rows }: { rows: DemandView[] }) {
  const visibleRows = rows.filter((row) => isValidProductDescription(row.name));
  return <tbody>{visibleRows.map((row) => <tr key={row.id}><td><b>{row.name}</b><small>{row.id}</small></td><td>{row.month}</td><td>{formatCompactNumber(row.units)}</td><td><span className={`tag ${row.label}`}>{row.label}</span></td></tr>)}</tbody>;
}

const demandColors: Record<string, string> = {
  high: "#59745e",
  medium: "#a27a45",
  low: "#8c7565",
};

type ForecastProduct = { productCode: string; productName: string };

export function normalizeForecastProduct(product: RealRetailProduct): ForecastProduct | null {
  if (!product.source_product_key || !isValidProductDescription(product.source_description)) return null;
  return { productCode: product.source_product_key, productName: product.source_description };
}

export function DemandForecastSection() {
  const [search, setSearch] = useState("");
  const [selectedProduct, setSelectedProduct] = useState<ForecastProduct | null>(null);
  const [matches, setMatches] = useState<ForecastProduct[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [forecast, setForecast] = useState<RandomForestDemandForecast | null>(null);
  const [forecastError, setForecastError] = useState("");
  const [forecastLoading, setForecastLoading] = useState(false);
  const searchRequests = useRef(createRequestSequence());
  const forecastRequests = useRef(createRequestSequence());

  useEffect(() => {
    const current = searchRequests.current.next();
    const controller = new AbortController();
    const term = search.trim();
    setSearchError("");
    setMatches([]);
    if (selectedProduct || term.length < 2) {
      setSearchLoading(false);
      return () => {
        controller.abort();
        searchRequests.current.invalidate(current);
      };
    }

    const cancelDebounce = scheduleDebounced(() => {
      setSearchLoading(true);
      api.realRetail.products("value", 10, 0, term, controller.signal).then((products) => {
        const valid = products.map(normalizeForecastProduct)
          .filter((product): product is ForecastProduct => product !== null);
        if (searchRequests.current.isCurrent(current)) setMatches(valid);
      }).catch((error: unknown) => {
        if (searchRequests.current.isCurrent(current) && !controller.signal.aborted) {
          setSearchError(error instanceof Error ? error.message : "Product search is unavailable.");
        }
      }).finally(() => {
        if (searchRequests.current.isCurrent(current)) setSearchLoading(false);
      });
    }, PRODUCT_SEARCH_DEBOUNCE_MS);
    return () => {
      cancelDebounce();
      controller.abort();
      searchRequests.current.invalidate(current);
    };
  }, [search, selectedProduct]);

  const onSearchChange = (value: string) => {
    setSearch(value);
    setSelectedProduct(null);
    setForecast(null);
    setForecastError("");
    setSearchLoading(false);
    forecastRequests.current.next();
    setForecastLoading(false);
  };

  const requestForecast = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const productCode = selectedProduct?.productCode ?? search.trim();
    if (!productCode) {
      setForecast(null);
      setForecastError("Search for a product or enter its product code.");
      return;
    }
    const current = forecastRequests.current.next();
    setForecast(null);
    setForecastError("");
    setForecastLoading(true);
    api.realRetail.demandForecast(productCode).then((result) => {
      if (forecastRequests.current.isCurrent(current)) setForecast(result);
    }).catch((error: unknown) => {
      if (!forecastRequests.current.isCurrent(current)) return;
      if (error instanceof ApiError && error.status === 404) {
        setForecastError("This product is not supported by the saved model, or has no valid description.");
      } else if (error instanceof ApiError && error.status === 422) {
        setForecastError(error.message);
      } else if (error instanceof ApiError && error.status === 503) {
        setForecastError(`The Random Forest model is unavailable. ${error.message}`);
      } else {
        setForecastError("The forecast service is unavailable. Check that FastAPI is running and try again.");
      }
    }).finally(() => {
      if (forecastRequests.current.isCurrent(current)) setForecastLoading(false);
    });
  };

  const selectProduct = (product: ForecastProduct) => {
    setSelectedProduct(product);
    setSearch(product.productCode);
    setMatches([]);
    setSearchError("");
    setForecast(null);
    setForecastError("");
    forecastRequests.current.next();
    setForecastLoading(false);
  };

  return <section className="page-section random-forest-forecast">
    <div className="section-intro"><div><h2>Random Forest Demand Forecast</h2><p>Request a monthly product-quantity estimate from the pre-trained Random Forest regressor.</p></div></div>
    <form className="filters demand-forecast-form" onSubmit={requestForecast}>
      <label className="filter-search">Search product names or enter a product code
        <input aria-label="Search product names or enter a product code" value={search} onChange={(event) => onSearchChange(event.target.value)} placeholder="Product name or code" maxLength={128} />
      </label>
      <button className="button primary" type="submit" disabled={forecastLoading || !search.trim()}>{forecastLoading ? "Forecasting..." : "Get forecast"}</button>
    </form>
    {searchLoading && <div className="loading-state" role="status">Searching products...</div>}
    {searchError && <div className="inline-error" role="alert">{searchError}</div>}
    {matches.length > 0 && <ul className="apriori-recommendation-list" aria-label="Matching products">
      {matches.map((product) => <li key={product.productCode}>
        <span>{product.productName}</span>
        <small>Product code: {product.productCode}</small>
        <button className="button secondary" type="button" onClick={() => selectProduct(product)}>Select</button>
      </li>)}
    </ul>}
    {selectedProduct && <div className="apriori-selected-product">
      <span>Selected product</span>
      <b>{selectedProduct.productName}</b>
      <small>Product code: {selectedProduct.productCode}</small>
    </div>}
    {forecastError && <div className="inline-error" role="alert">{forecastError}</div>}
    {forecast && <div className="panel demand-forecast-result" role="status">
      <div className="panel-head"><div><h3>{forecast.product_name}</h3><small>Product code: {forecast.product_code}</small></div></div>
      <p>Forecast month: <b>{forecast.forecast_month}</b></p>
      <p>Predicted monthly quantity: <b>{formatCompactNumber(forecast.predicted_monthly_quantity)} units</b></p>
    </div>}
    <p className="disclaimer">This is a monthly quantity estimate, not a demand class. Forecasts require at least three eligible monthly observations and a product code represented in the saved model. Historical model evaluation metrics do not guarantee accuracy for live products.</p>
  </section>;
}

export default function DemandAnalysis() {
  const [rows, setRows] = useState<DemandView[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedClass, setSelectedClass] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<DemandSort>("name");
  const [page, setPage] = useState(1);
  const requestId = useRef(0);

  const load = () => {
    const currentRequest = ++requestId.current;
    setError("");
    setRows([]);
    setMessage("");
    setSelectedClass(null);
    setQuery("");
    setSort("name");
    setPage(1);
    setLoading(true);
    const request = api.realRetail.demand().then((data: RealRetailDemandResponse) => ({
      rows: data.predictions.map(normalize).filter((row): row is DemandView => row !== null),
      message: data.message ?? "",
    }));
    request.then((result) => {
      if (currentRequest === requestId.current) {
        setRows(result.rows);
        setMessage(result.message);
      }
    }).catch((e: Error) => {
      if (currentRequest === requestId.current) setError(e.message);
    }).finally(() => {
      if (currentRequest === requestId.current) setLoading(false);
    });
  };
  useEffect(() => { load(); }, []);

  const distribution = useMemo(() => getDemandDistribution(rows), [rows]);
  const filtered = useMemo(
    () => filterAndSortDemandRows(rows, query, selectedClass, sort),
    [rows, query, selectedClass, sort],
  );
  const pageSize = 50;
  const current = paginate(filtered, page, pageSize);
  const toggleClass = (key: string) => {
    setSelectedClass((value) => value === key ? null : key);
    setPage(1);
  };
  const clearFilters = () => {
    setSelectedClass(null);
    setQuery("");
    setSort("name");
    setPage(1);
  };

  const forecastSection = <DemandForecastSection />;
  if (error) return <>{forecastSection}<ErrorState message={error} retry={load} /></>;
  if (loading) return <>{forecastSection}<div className="loading-state">Loading demand signals…</div></>;

  return <>{forecastSection}<section className="page-section">
    <div className="section-intro"><div><h2>Demand signals</h2><p>Demand classes derived from the returned product classifications.</p></div><span className="count-badge">{formatCompactNumber(rows.length)} products analyzed</span></div>
    <section className="demand-summary" aria-label="Demand summary">
      <div className="demand-summary-card"><span>Total products analyzed</span><strong>{formatCompactNumber(rows.length)}</strong></div>
      {distribution.map((entry) => <div className="demand-summary-card" key={entry.key}><span>{entry.name} demand products</span><strong style={{ color: demandColors[entry.key] }}>{formatCompactNumber(entry.products)}</strong></div>)}
    </section>
    <div className="panel demand-chart-panel"><div className="panel-head"><div><h3>Demand distribution</h3><small>Count and share of product classifications assigned to each class. Select a bar to filter the table.</small></div></div><ResponsiveContainer width="100%" height={250}><BarChart data={distribution} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}><CartesianGrid vertical={false} stroke="#e2dbd2" /><XAxis dataKey="name" tickLine={false} axisLine={false} /><YAxis allowDecimals={false} tickFormatter={(value) => formatCompactNumber(Number(value))} tickLine={false} axisLine={false} width={54} label={{ value: "Products classified", angle: -90, position: "insideLeft", offset: 6, style: { fontSize: 10, fill: "#847970" } }} /><Tooltip content={({ active, payload }) => { const entry = payload?.[0]?.payload as (typeof distribution)[number] | undefined; return active && entry ? <div className="chart-tooltip"><b>{entry.name} demand</b><span>{formatCompactNumber(entry.products)} products classified</span><span>{entry.percentage.toFixed(1)}% of classifications</span></div> : null; }} /><Bar dataKey="products" name="Products classified" cursor="pointer" isAnimationActive={false} radius={[4,4,0,0]}>{distribution.map((entry) => <Cell key={entry.key} fill={selectedClass === entry.key ? "#3e3028" : demandColors[entry.key]} role="button" tabIndex={0} aria-label={`${entry.name} demand: ${entry.products} products. Activate to filter products.`} aria-pressed={selectedClass === entry.key} onClick={(event: ReactMouseEvent<SVGElement>) => { event.stopPropagation(); toggleClass(entry.key); }} onKeyDown={(event: ReactKeyboardEvent<SVGElement>) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleClass(entry.key); } }} />)}</Bar></BarChart></ResponsiveContainer></div>
    <div className="filters demand-filters">
      <label className="filter-search"><span className="sr-only">Search demand products</span><input aria-label="Search demand products" placeholder="Search products..." value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} /></label>
      <label className="filter-control">Sort by<select aria-label="Sort demand products" value={sort} onChange={(event) => { setSort(event.target.value as DemandSort); setPage(1); }}><option value="name">Product name</option><option value="units">Last month units</option><option value="month">Observed month</option><option value="class">Demand class</option></select></label>
      {(selectedClass || query || sort !== "name") && <button className="button secondary filter-reset" onClick={clearFilters}>Clear filters</button>}
    </div>
    {selectedClass && <div className="disclaimer demand-filter-note">Showing {selectedClass} demand products from the chart selection.</div>}
    <div className="table-panel"><table><thead><tr><th>Product</th><th>Observed month</th><th>Last month units</th><th>Demand class</th></tr></thead><DemandTableRows rows={current.rows} /></table>{!filtered.length && <div className="empty-state">{rows.length ? "No products match the selected filters." : message || "No predictions are available for the current sales history."}</div>}</div>
    <div className="pagination"><button className="button secondary" disabled={current.page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</button><span>{current.start}-{current.end} of {formatCompactNumber(filtered.length)} · Page {current.page} of {current.pageCount}</span><button className="button secondary" disabled={current.page >= current.pageCount} onClick={() => setPage((value) => Math.min(current.pageCount, value + 1))}>Next</button></div>
  </section></>;
}
