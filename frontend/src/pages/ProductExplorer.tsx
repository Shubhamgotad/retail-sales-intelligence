import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Filter, RefreshCw } from "lucide-react";
import { api } from "../services/api";
import type { RealRetailProduct } from "../types";
import { ErrorState } from "./Dashboard";
import { formatCompactNumber, formatGBP } from "../utils/format";
import { isValidProductDescription } from "../utils/productDescription";

type ProductView = {
  id: string;
  name: string;
  units: number;
  sourceValue: string;
};

export const fromReal = (item: RealRetailProduct): ProductView | null => {
  if (!isValidProductDescription(item.source_description)) return null;
  return {
    id: item.source_product_key ?? "[missing ID]",
    name: item.source_description,
    units: Number(item.quantity_total),
    sourceValue: item.monetary_total,
  };
};

export default function ProductExplorer() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [items, setItems] = useState<ProductView[]>([]);
  const query = searchParams.get("productSearch") ?? "";
  const [minimum, setMinimum] = useState("");
  const [maximum, setMaximum] = useState("");
  const [sort, setSort] = useState("name");
  const [page, setPage] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reloadCount, setReloadCount] = useState(0);
  const requestId = useRef(0);

  useEffect(() => {
    const current = ++requestId.current;
    setError("");
    setItems([]);
    setPage(0);
    setLoading(true);
    const timer = window.setTimeout(() => {
      const request = api.realRetail.products("value", 200, 0, query)
        .then((rows) => rows.map(fromReal).filter((item): item is ProductView => item !== null));
      request.then((nextItems) => {
        if (current === requestId.current) setItems(nextItems);
      }).catch((e: Error) => {
        if (current === requestId.current) setError(e.message);
      }).finally(() => {
        if (current === requestId.current) setLoading(false);
      });
    }, query.trim() ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [query, reloadCount]);

  const loadMore = () => {
    if (loadingMore || items.length < 200) return;
    const current = requestId.current;
    setLoadingMore(true);
    api.realRetail.products("value", 200, items.length, query).then((rows) => {
      if (current === requestId.current) setItems((existing) => [
        ...existing,
        ...rows.map(fromReal).filter((item): item is ProductView => item !== null),
      ]);
    }).catch((e: Error) => {
      if (current === requestId.current) setError(e.message);
    }).finally(() => {
      if (current === requestId.current) setLoadingMore(false);
    });
  };

  const filtered = useMemo(() => {
    const min = minimum.trim() ? Number(minimum) : null;
    const max = maximum.trim() ? Number(maximum) : null;
    return items.filter((item) => {
      const amount = Number(item.sourceValue);
      return `${item.name} ${item.id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()) &&
        (min === null || (amount !== null && amount >= min)) &&
        (max === null || (amount !== null && amount <= max));
    }).sort((left, right) => {
      if (sort === "name") return left.name.localeCompare(right.name);
      if (sort === "units") return right.units - left.units;
      return Number(right.sourceValue) - Number(left.sourceValue);
    });
  }, [items, query, minimum, maximum, sort]);
  const pageSize = 25;
  const visible = filtered.slice(page * pageSize, (page + 1) * pageSize);
  const clearFilters = () => {
    setMinimum("");
    setMaximum("");
    setSort("name");
    setPage(0);
    const params = new URLSearchParams(searchParams);
    params.delete("productSearch");
    setSearchParams(params);
  };
  if (error && !items.length) return <ErrorState message={error} retry={() => setReloadCount((value) => value + 1)} />;
  return <section className="page-section">
    <div className="section-intro"><div><h2>Product catalog</h2><p>Explore Online Retail II product performance. Source monetary values are in GBP (£).</p></div><span className="count-badge">{formatCompactNumber(filtered.length)} matching products</span></div>
    <div className="disclaimer">No currency conversion is applied. C-prefixed cancellation candidates are excluded; other negative quantities retain their signed values as return adjustments.</div>
    <div className="filters product-filters">
      <label className="filter-control">Min source total (GBP)<input aria-label="Minimum source monetary total" type="number" value={minimum} onChange={(event) => { setMinimum(event.target.value); setPage(0); }} /></label>
      <label className="filter-control">Max source total (GBP)<input aria-label="Maximum source monetary total" type="number" value={maximum} onChange={(event) => { setMaximum(event.target.value); setPage(0); }} /></label>
      <label className="filter-control">Sort by<select aria-label="Sort products" value={sort} onChange={(event) => { setSort(event.target.value); setPage(0); }}><option value="name">Product name</option><option value="units">Signed quantity</option><option value="value">Source monetary total</option></select></label>
      <button className="button secondary filter-reset" onClick={clearFilters}><Filter size={15} /> Clear filters</button>
    </div>
    {error && <div className="inline-error" role="alert">{error}</div>}
    <div className="table-panel"><table><thead><tr><th>Product</th><th>Signed quantity</th><th>Source value</th></tr></thead><tbody>{visible.map((item) => <tr key={item.id}><td><b>{item.name}</b><small>{item.id}</small></td><td>{formatCompactNumber(item.units)}</td><td>{formatGBP(item.sourceValue)}</td></tr>)}</tbody></table>{!loading && !visible.length && <div className="empty-state">No products match your filters.</div>}{loading && <div className="loading-state">Loading products…</div>}</div>
    <div className="pagination"><button className="button secondary" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>Previous</button><span>Page {page + 1} · {formatCompactNumber(filtered.length)} matches</span><button className="button secondary" disabled={(page + 1) * pageSize >= filtered.length} onClick={() => setPage((value) => value + 1)}>Next</button>{items.length >= 200 && <button className="button secondary" disabled={loadingMore} onClick={loadMore}><RefreshCw size={14} />{loadingMore ? "Loading…" : "Load more products"}</button>}</div>
  </section>;
}
