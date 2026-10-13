import { useEffect, useMemo, useState } from "react";
import { Filter, Search } from "lucide-react";
import { api } from "../services/api";
import type { Product } from "../types";
import { ErrorState } from "./Dashboard";
import { formatCurrency } from "../utils/format";
export default function ProductExplorer() {
  const [items, setItems] = useState<Product[]>([]), [query, setQuery] = useState(""), [category, setCategory] = useState("All categories"), [error, setError] = useState("");
  const load = () => { api.products().then(setItems).catch((e: Error) => setError(e.message)); };
  useEffect(load, []);
  const categories = ["All categories", ...Array.from(new Set(items.map((p) => p.category)))];
  const filtered = useMemo(() => items.filter((p) => (category === "All categories" || p.category === category) && `${p.product_name} ${p.brand} ${p.category}`.toLowerCase().includes(query.toLowerCase())), [items, query, category]);
  if (error) return <ErrorState message={error} retry={load} />;
  return <section className="page-section"><div className="section-intro"><div><h2>Product catalog</h2><p>Explore performance across every product in the warehouse.</p></div><span className="count-badge">{filtered.length} products</span></div><div className="filters"><div className="filter-search"><Search size={16} /><input placeholder="Search catalog..." value={query} onChange={(e) => setQuery(e.target.value)} /></div><select value={category} onChange={(e) => setCategory(e.target.value)}>{categories.map((c) => <option key={c}>{c}</option>)}</select><button className="button secondary"><Filter size={15} /> More filters</button></div><div className="table-panel"><table><thead><tr><th>Product</th><th>Category</th><th>Brand</th><th>Units sold</th><th>Revenue</th><th>Profit</th></tr></thead><tbody>{filtered.map((p) => <tr key={p.product_id}><td><b>{p.product_name}</b><small>{p.product_id}</small></td><td>{p.category}</td><td>{p.brand}</td><td>{p.units_sold.toLocaleString("en-IN")}</td><td>{formatCurrency(p.revenue)}</td><td>{formatCurrency(p.profit)}</td></tr>)}</tbody></table>{!filtered.length && <div className="empty-state">No products match your filters.</div>}</div></section>;
}
