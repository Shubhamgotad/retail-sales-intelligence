import { useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../services/api";
import type { ClusterResponse } from "../types";
import { formatCompactINRTick, formatCurrency } from "../utils/format";
import { ErrorState } from "./Dashboard";

export default function Clustering() {
  const [data, setData] = useState<ClusterResponse | null>(null);
  const [count, setCount] = useState(3);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [scaleMode, setScaleMode] = useState<"linear" | "log">("linear");
  const requestId = useRef(0);
  const load = (selectedCount: number) => {
    const currentRequest = ++requestId.current;
    setError("");
    setLoading(true);
    api.clusters(selectedCount).then((result) => {
      if (currentRequest === requestId.current) setData(result);
    }).catch((e: Error) => {
      if (currentRequest === requestId.current) setError(e.message);
    }).finally(() => {
      if (currentRequest === requestId.current) setLoading(false);
    });
  };
  useEffect(() => { load(count); }, [count]);
  const scatterData = useMemo(() => data?.products.map((product) => ({ ...product, clusterLabel: `Cluster ${product.cluster + 1}` })) ?? [], [data]);
  const validScatterData = useMemo(() => scatterData.filter((product) => Number.isFinite(product.revenue) && Number.isFinite(product.profit)), [scatterData]);
  const canUseLogScale = validScatterData.length > 0 && validScatterData.every((product) => product.revenue > 0 && product.profit > 0);
  const scatterDomain = useMemo(() => {
    if (scaleMode === "log" && canUseLogScale) return ["auto", "auto"] as const;
    const values = validScatterData.flatMap((product) => [product.revenue, product.profit]);
    if (!values.length) return [0, 1] as const;
    const maximum = Math.max(...values);
    const minimum = Math.min(0, ...values);
    const padding = Math.max((maximum - minimum) * 0.08, 1);
    return [minimum - padding, maximum + padding] as const;
  }, [canUseLogScale, scaleMode, validScatterData]);
  if (error && !data) return <ErrorState message={error} retry={() => load(count)} />;
  if (!data) return <div className="loading-state">Loading product clusters...</div>;
  return <section className="page-section">
    <div className="section-intro"><div><h2>Product clustering</h2><p>Group products by units sold, revenue, and profit.</p></div><select value={count} disabled={loading} onChange={(e) => setCount(Number(e.target.value))}>{[2,3,4,5,6,7,8,9,10].map((value) => <option key={value} value={value}>{value} clusters</option>)}</select></div>
    {error && <div className="inline-error" role="alert">{error}</div>}
    {data.cluster_count !== count && <div className="inline-error" role="alert">The service returned {data.cluster_count} clusters for a request of {count}.</div>}
    <div className="cluster-grid">{data.clusters.map((cluster) => <div className="metric-card" key={cluster.cluster}><div className="metric-label">Cluster {cluster.cluster + 1}</div><strong>{cluster.product_count}</strong><small>products · {formatCurrency(cluster.total_revenue)} revenue</small></div>)}</div>
    <div className="analysis-grid"><div className="panel chart-panel"><div className="panel-head"><div><h3>Cluster size</h3><small>Number of products assigned to each returned cluster</small></div></div><ResponsiveContainer width="100%" height={270}><BarChart data={data.clusters}><CartesianGrid vertical={false} stroke="#e2dbd2" /><XAxis dataKey="cluster" tickFormatter={(value) => `C${Number(value) + 1}`} tickLine={false} axisLine={false} /><YAxis allowDecimals={false} tickLine={false} axisLine={false} /><Tooltip labelFormatter={(value) => `Cluster ${Number(value) + 1}`} formatter={(value) => [`${value} products`, "Cluster size"]} /><Bar dataKey="product_count" fill="#796656" radius={[4,4,0,0]} /></BarChart></ResponsiveContainer></div><div className="panel chart-panel"><div className="panel-head"><div><h3>Revenue vs profit</h3><small>Each point is a returned product, colored by cluster</small></div><label className="scale-toggle">Scale <select value={scaleMode} disabled={!canUseLogScale} onChange={(event) => setScaleMode(event.target.value as "linear" | "log")}><option value="linear">Linear</option><option value="log">Logarithmic</option></select></label></div><ResponsiveContainer width="100%" height={320}><ScatterChart margin={{ top: 18, right: 20, bottom: 28, left: 22 }}><CartesianGrid stroke="#e2dbd2" /><XAxis type="number" dataKey="revenue" name="Revenue" scale={scaleMode === "log" && canUseLogScale ? "log" : "linear"} domain={scatterDomain} tickFormatter={(value) => formatCompactINRTick(Number(value))} tickLine={false} axisLine={false} label={{ value: "Revenue (₹)", position: "insideBottom", offset: -18, style: { fontSize: 11, fill: "#756960" } }} /><YAxis type="number" dataKey="profit" name="Profit" scale={scaleMode === "log" && canUseLogScale ? "log" : "linear"} domain={scatterDomain} tickFormatter={(value) => formatCompactINRTick(Number(value))} tickLine={false} axisLine={false} width={72} label={{ value: "Profit (₹)", angle: -90, position: "insideLeft", offset: 8, style: { fontSize: 11, fill: "#756960" } }} /><Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ active, payload }) => { const product = payload?.[0]?.payload as (typeof scatterData)[number] | undefined; return active && product ? <div className="chart-tooltip"><b>{product.product_name}</b><span>Revenue: {formatCurrency(product.revenue)}</span><span>Profit: {formatCurrency(product.profit)}</span><span>Cluster: {product.cluster + 1}</span></div> : null; }} /><Scatter data={validScatterData} fill="#796656" shape="circle">{validScatterData.map((product) => <Cell key={product.product_id} fill={["#3e3028", "#746456", "#9a8975", "#b9aa98", "#bda48d", "#847267", "#a18c7b", "#5d4c40", "#c7b8a8", "#6d5b4d"][product.cluster % 10]} />)}</Scatter></ScatterChart></ResponsiveContainer>{!canUseLogScale && <p className="chart-note">Logarithmic scale is unavailable because revenue or profit includes zero, negative, or missing values. All valid products remain on the linear scale.</p>}{validScatterData.length < scatterData.length && <p className="chart-note">{scatterData.length - validScatterData.length} product(s) have missing numeric values and are retained in the table but cannot be plotted.</p>}</div></div>
    <div className="table-panel"><table><thead><tr><th>Product</th><th>Category</th><th>Cluster</th><th>Units sold</th><th>Revenue</th><th>Profit</th></tr></thead><tbody>{data.products.map((product) => <tr key={product.product_id}><td><b>{product.product_name}</b><small>{product.product_id}</small></td><td>{product.category}</td><td><span className="tag">Cluster {product.cluster + 1}</span></td><td>{product.units_sold.toLocaleString("en-IN")}</td><td>{formatCurrency(product.revenue)}</td><td>{formatCurrency(product.profit)}</td></tr>)}</tbody></table></div>
  </section>;
}
