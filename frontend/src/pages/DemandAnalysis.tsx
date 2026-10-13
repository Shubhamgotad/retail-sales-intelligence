import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../services/api";
import type { DemandResponse } from "../types";
import { ErrorState } from "./Dashboard";

export default function DemandAnalysis() {
  const [data, setData] = useState<DemandResponse | null>(null);
  const [error, setError] = useState("");
  const load = () => { setError(""); api.demand().then(setData).catch((e: Error) => setError(e.message)); };
  useEffect(load, []);
  const distribution = useMemo(() => {
    const total = data?.predictions.length ?? 0;
    return Object.entries((data?.predictions ?? []).reduce<Record<string, number>>((counts, row) => { counts[row.predicted_demand] = (counts[row.predicted_demand] ?? 0) + 1; return counts; }, {})).map(([name, products]) => ({ name, products, percentage: total ? (products / total) * 100 : 0 }));
  }, [data]);
  if (error) return <ErrorState message={error} retry={load} />;
  if (!data) return <div className="loading-state">Loading demand signals…</div>;
  return <section className="page-section">
    <div className="section-intro"><div><h2>Demand signals</h2><p>Distribution of {data.predictions.length} product predictions by next-month demand class.</p></div><span className="count-badge">{data.predictions.length} products analyzed</span></div>
    <div className="analysis-grid"><div className="panel chart-panel"><div className="panel-head"><div><h3>Demand distribution</h3><small>Count and share of product predictions assigned to each class</small></div></div>{distribution.length ? <ResponsiveContainer width="100%" height={270}><BarChart data={distribution}><CartesianGrid vertical={false} stroke="#e2dbd2" /><XAxis dataKey="name" tickLine={false} axisLine={false} /><YAxis allowDecimals={false} tickLine={false} axisLine={false} label={{ value: "Products predicted", angle: -90, position: "insideLeft", style: { fontSize: 10, fill: "#847970" } }} /><Tooltip content={({ active, payload }) => { const entry = payload?.[0]?.payload as (typeof distribution)[number] | undefined; return active && entry ? <div className="chart-tooltip"><b>{entry.name.charAt(0).toUpperCase() + entry.name.slice(1)} demand</b><span>{entry.products} products predicted</span><span>{entry.percentage.toFixed(1)}% of predictions</span></div> : null; }} /><Bar dataKey="products" name="Products predicted" fill="#796656" radius={[4,4,0,0]} /></BarChart></ResponsiveContainer> : <div className="empty-state">The API returned no usable predictions.</div>}</div><div className="insight-card"><span className="insight-icon">✦</span><h3>Model context</h3><p>{data.note}</p><p>Algorithm: {data.algorithm}. Training rows: {data.training_rows}. Reported in-sample accuracy: {(data.training_accuracy * 100).toFixed(1)}%.</p></div></div>
    <div className="table-panel"><table><thead><tr><th>Product</th><th>Observed month</th><th>Last month units</th><th>Predicted demand</th></tr></thead><tbody>{data.predictions.map((row) => <tr key={row.product_id}><td><b>{row.product_name}</b><small>{row.product_id}</small></td><td>{row.last_observed_month}</td><td>{row.last_month_units.toLocaleString("en-IN")}</td><td><span className={`tag ${row.predicted_demand}`}>{row.predicted_demand}</span></td></tr>)}</tbody></table>{!data.predictions.length && <div className="empty-state">No predictions are available for the current sales history.</div>}</div>
  </section>;
}
