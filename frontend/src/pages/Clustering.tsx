import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../services/api";
import type { RealRetailClusterResponse } from "../types";
import { formatCompactNumber, formatGBP } from "../utils/format";
import { paginate } from "../utils/pagination";
import { ErrorState } from "./Dashboard";

type ClusterView = {
  cluster: number;
  count: number;
  totalPrimary: number;
  totalSecondary: number;
  label: string;
  id: string;
  name: string;
  primary: number;
  secondary: number;
  category: string;
  invoices?: number;
  lines?: number;
  exactPrimary?: string;
};
type ClusterSort = "cluster" | "name" | "primary" | "secondary";

const clusterColors = ["#3e3028", "#59745e", "#9a6b42", "#55798b", "#8b688b", "#9a4f52", "#6e7651", "#4c7190", "#ad8251", "#6c6257"];
export const clusterColor = (cluster: number) => clusterColors[cluster % clusterColors.length];

export function normalizeCustomerClusters(data: RealRetailClusterResponse) {
  return {
    groups: data.clusters.map((item) => ({
      cluster: item.cluster,
      count: item.customer_count,
      totalPrimary: Number(item.total_monetary_value),
      totalSecondary: item.total_signed_quantity,
      exactPrimary: item.total_monetary_value,
    })),
    rows: data.customers.map((item) => ({
      cluster: item.cluster,
      count: 1,
      totalPrimary: Number(item.monetary_value),
      totalSecondary: item.signed_quantity,
      label: `Cluster ${item.cluster + 1}`,
      id: item.customer_id,
      name: item.customer_id,
      primary: Number(item.monetary_value),
      secondary: item.signed_quantity,
      category: "Customer",
      invoices: item.invoice_count,
      lines: item.line_count,
      exactPrimary: item.monetary_value,
    })),
    note: `${data.note} Currency is GBP (£). Missing customer lines excluded: ${formatCompactNumber(data.excluded_missing_customer_lines)}.`,
  };
}

export function filterAndSortClusterRows(
  rows: ClusterView[],
  search: string,
  selectedCluster: number | null,
  sort: ClusterSort,
): ClusterView[] {
  const query = search.trim().toLocaleLowerCase();
  return rows.filter((row) =>
    (selectedCluster === null || row.cluster === selectedCluster) &&
    (!query || `${row.name} ${row.id} ${row.category}`.toLocaleLowerCase().includes(query)),
  ).sort((left, right) => {
    if (sort === "name") return left.name.localeCompare(right.name) || left.id.localeCompare(right.id);
    if (sort === "primary") return right.primary - left.primary || left.id.localeCompare(right.id);
    if (sort === "secondary") return right.secondary - left.secondary || left.id.localeCompare(right.id);
    return left.cluster - right.cluster || left.name.localeCompare(right.name) || left.id.localeCompare(right.id);
  });
}

export function toggleClusterSelection(current: number | null, clicked: number): number | null {
  return current === clicked ? null : clicked;
}

export default function Clustering() {
  const [clusters, setClusters] = useState<ClusterView[]>([]);
  const [rows, setRows] = useState<ClusterView[]>([]);
  const [count, setCount] = useState(3);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedCluster, setSelectedCluster] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<ClusterSort>("cluster");
  const [page, setPage] = useState(1);
  const requestId = useRef(0);

  const load = (selectedCount: number) => {
    const request = ++requestId.current;
    setError("");
    setNotice("");
    setLoading(true);
    setClusters([]);
    setRows([]);
    setSelectedCluster(null);
    setPage(1);
    const result = api.realRetail.clusters(selectedCount).then(normalizeCustomerClusters);
    result.then((data) => {
      if (request === requestId.current) {
        setClusters(data.groups.map((group) => ({
          ...group, label: `Cluster ${group.cluster + 1}`, id: "", name: "",
          primary: 0, secondary: 0, category: "",
        })));
        setRows(data.rows);
        setNotice(data.note);
      }
    }).catch((e: Error) => {
      if (request === requestId.current) setError(e.message);
    }).finally(() => {
      if (request === requestId.current) setLoading(false);
    });
  };
  useEffect(() => { load(count); }, [count]);

  const filtered = useMemo(
    () => filterAndSortClusterRows(rows, search, selectedCluster, sort),
    [rows, search, selectedCluster, sort],
  );
  const current = paginate(filtered, page, 50);
  const chartRows = useMemo(
    () => rows.filter((row) => Number.isFinite(row.primary) && Number.isFinite(row.secondary)),
    [rows],
  );
  const toggleCluster = (cluster: number) => {
    setSelectedCluster((currentCluster) => toggleClusterSelection(currentCluster, cluster));
    setPage(1);
  };
  const clearSelection = () => {
    setSelectedCluster(null);
    setPage(1);
  };

  if (error && !clusters.length && !rows.length) return <ErrorState message={error} retry={() => load(count)} />;
  if (loading && !clusters.length && !rows.length) return <div className="loading-state">Loading customer clusters…</div>;
  return <section className="page-section">
    <div className="section-intro"><div><h2>Customer clustering</h2><p>Group identifiable customers by invoice count, line count, signed quantity, and source monetary value. Each point represents one customer; select a cluster to filter the customer records.</p></div><label className="filter-control">Clusters<select aria-label="Number of clusters" value={count} disabled={loading} onChange={(event) => { const value = Number(event.target.value); setSelectedCluster(null); setPage(1); setCount(value); }}>{[2,3,4,5,6,7,8,9,10].map((value) => <option key={value} value={value}>{value} clusters</option>)}</select></label></div>
    {error && <div className="inline-error" role="alert">{error}</div>}
    {notice && <div className="subtle-note">{notice}</div>}
    <div className="cluster-grid">{clusters.map((cluster) => <div className="metric-card" key={cluster.cluster}><div className="metric-label"><i className="cluster-color-dot" style={{ background: clusterColor(cluster.cluster) }} />Cluster {cluster.cluster + 1}</div><strong>{formatCompactNumber(cluster.count)}</strong><small>customers · {formatGBP(cluster.exactPrimary ?? "0")}</small></div>)}</div>
    <div className="analysis-grid">
      <div className="panel chart-panel"><div className="panel-head"><div><h3>Cluster size</h3><small>Click a bar or legend item to filter the records below.</small></div></div><ResponsiveContainer width="100%" height={280}><BarChart data={clusters} margin={{ top: 8, right: 12, bottom: 12, left: 4 }}><CartesianGrid vertical={false} stroke="#e2dbd2" /><XAxis dataKey="cluster" tickFormatter={(value) => `Cluster ${Number(value) + 1}`} tick={{ fontSize: 10 }} tickLine={false} axisLine={false} interval={0} /><YAxis allowDecimals={false} tickFormatter={(value) => formatCompactNumber(Number(value))} tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={48} /><Tooltip labelFormatter={(value) => `Cluster ${Number(value) + 1}`} formatter={(value) => [`${formatCompactNumber(Number(value))} customers`, "Records"]} /><Bar dataKey="count" name="Records" cursor="pointer" isAnimationActive={false} radius={[4,4,0,0]}>{clusters.map((item) => <Cell key={item.cluster} fill={clusterColor(item.cluster)} stroke={selectedCluster === item.cluster ? "#27211e" : "none"} strokeWidth={selectedCluster === item.cluster ? 2 : 0} role="button" tabIndex={0} aria-label={`Filter to cluster ${item.cluster + 1}`} aria-pressed={selectedCluster === item.cluster} onClick={(event: ReactMouseEvent<SVGElement>) => { event.stopPropagation(); toggleCluster(item.cluster); }} onKeyDown={(event: ReactKeyboardEvent<SVGElement>) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleCluster(item.cluster); } }} />)}</Bar></BarChart></ResponsiveContainer></div>
      <div className="panel chart-panel"><div className="panel-head"><div><h3>Source value vs signed quantity</h3><small>Each point represents one identifiable customer.</small></div></div><p className="chart-note">Select a point or cluster legend item to filter the customer table. All records, including outliers and signed quantities, are plotted.</p><ResponsiveContainer width="100%" height={340}><ScatterChart margin={{ top: 12, right: 20, bottom: 48, left: 42 }}><CartesianGrid stroke="#e2dbd2" strokeDasharray="3 5" /><XAxis type="number" dataKey="primary" name="Source value" tickFormatter={(value) => formatCompactNumber(Number(value))} tick={{ fontSize: 10 }} tickMargin={8} tickLine={false} axisLine={{ stroke: "#b7aa9d" }} label={{ value: "Source value (GBP £)", position: "insideBottom", offset: -34, style: { fontSize: 11, fill: "#5f554d" } }} /><YAxis type="number" dataKey="secondary" name="Signed quantity" tickFormatter={formatCompactNumber} tick={{ fontSize: 10 }} tickMargin={7} tickLine={false} axisLine={{ stroke: "#b7aa9d" }} width={64} label={{ value: "Signed quantity", angle: -90, position: "insideLeft", offset: 2, style: { fontSize: 11, fill: "#5f554d" } }} /><Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ active, payload }) => { const row = payload?.[0]?.payload as ClusterView | undefined; return active && row ? <div className="chart-tooltip"><b>Cluster {row.cluster + 1} · Customer ID {row.name}</b><span>Source value: {formatGBP(row.exactPrimary ?? "0")}</span><span>Signed quantity: {formatCompactNumber(row.secondary)}</span><span>{formatCompactNumber(row.invoices ?? 0)} invoices · {formatCompactNumber(row.lines ?? 0)} lines</span><span>Click point to show this cluster</span></div> : null; }} /><Scatter data={chartRows} name="Customers" fill="#796656" shape="circle" isAnimationActive={false}>{chartRows.map((row) => <Cell key={row.id} fill={clusterColor(row.cluster)} fillOpacity={selectedCluster === null || selectedCluster === row.cluster ? 0.82 : 0.3} stroke="#fbfaf7" strokeWidth={0.6} role="button" tabIndex={0} aria-label={`Cluster ${row.cluster + 1}: customer ${row.name}`} aria-pressed={selectedCluster === row.cluster} onClick={() => toggleCluster(row.cluster)} onKeyDown={(event: ReactKeyboardEvent<SVGElement>) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleCluster(row.cluster); } }} />)}</Scatter></ScatterChart></ResponsiveContainer><div className="cluster-legend" aria-label="Cluster legend">{clusters.map((cluster) => <button type="button" key={cluster.cluster} className={selectedCluster === cluster.cluster ? "cluster-legend-item selected" : "cluster-legend-item"} aria-pressed={selectedCluster === cluster.cluster} onClick={() => toggleCluster(cluster.cluster)}><i style={{ background: clusterColor(cluster.cluster) }} />Cluster {cluster.cluster + 1}<span>{formatCompactNumber(cluster.count)}</span></button>)}</div></div>
    </div>
    <div className="filters cluster-filters"><label className="filter-search"><span className="sr-only">Search cluster records</span><input aria-label="Search customers" placeholder="Search customers..." value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} /></label><label className="filter-control">Sort by<select aria-label="Sort cluster records" value={sort} onChange={(event) => { setSort(event.target.value as ClusterSort); setPage(1); }}><option value="cluster">Cluster</option><option value="name">Customer ID</option><option value="primary">Source value</option><option value="secondary">Signed quantity</option></select></label>{selectedCluster !== null && <button className="button secondary" onClick={clearSelection}>Clear selection</button>}</div>
    {selectedCluster !== null && <div className="disclaimer cluster-selection-note">Showing customers in Cluster {selectedCluster + 1}.</div>}
    <div className="table-panel cluster-records-table"><table><thead><tr><th>Customer ID</th><th>Cluster</th><th>Invoices</th><th>Lines</th><th>Signed quantity</th><th>Source value</th></tr></thead><tbody>{current.rows.map((row) => <tr key={row.id}><td><b>{row.name}</b><small>{row.id}</small></td><td><span className="cluster-table-label"><i style={{ background: clusterColor(row.cluster) }} />Cluster {row.cluster + 1}</span></td><td>{formatCompactNumber(row.invoices ?? 0)}</td><td>{formatCompactNumber(row.lines ?? 0)}</td><td>{formatCompactNumber(row.secondary)}</td><td>{formatGBP(row.exactPrimary ?? "0")}</td></tr>)}</tbody></table>{!filtered.length && <div className="empty-state">{rows.length ? "No records match your search." : "No customer cluster records are available."}</div>}</div>
    <div className="pagination"><button className="button secondary" disabled={current.page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</button><span>{current.start}-{current.end} of {formatCompactNumber(filtered.length)} · Page {current.page} of {current.pageCount}</span><button className="button secondary" disabled={current.page >= current.pageCount} onClick={() => setPage((value) => Math.min(current.pageCount, value + 1))}>Next</button></div>
  </section>;
}
