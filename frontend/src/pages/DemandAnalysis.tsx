import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../services/api";
import type { RealRetailDemandResponse } from "../types";
import { ErrorState } from "./Dashboard";
import { formatCompactNumber } from "../utils/format";
import { paginate } from "../utils/pagination";
import { isValidProductDescription } from "../utils/productDescription";

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

  if (error) return <ErrorState message={error} retry={load} />;
  if (loading) return <div className="loading-state">Loading demand signals…</div>;

  return <section className="page-section">
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
  </section>;
}
