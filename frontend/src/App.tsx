import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Boxes, ChevronDown, CircleHelp, LayoutDashboard, Menu, Network, Search, ShoppingBag, Sparkles, Target, X, BarChart3 } from "lucide-react";
import { api } from "./services/api";
import type { RealRetailCountry, RealRetailProduct } from "./types";
import { isValidProductDescription } from "./utils/productDescription";
import Dashboard from "./pages/Dashboard";
import ProductExplorer from "./pages/ProductExplorer";
import DemandAnalysis from "./pages/DemandAnalysis";
import BasketAnalysis from "./pages/BasketAnalysis";
import Clustering from "./pages/Clustering";

const nav = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/products", label: "Products", icon: Boxes },
  { to: "/demand", label: "Demand analysis", icon: Target },
  { to: "/clustering", label: "Customer clustering", icon: BarChart3 },
  { to: "/basket", label: "Basket analysis", icon: Network },
];

type SearchResult = { kind: "product" | "region"; name: string; detail: string };

function HeaderSearch() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [regions, setRegions] = useState<string[]>([]);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const requestId = useRef(0);
  useEffect(() => {
    if (!open || regions.length) return;
    let active = true;
    api.realRetail.countries("value", 200)
      .then((rows: RealRetailCountry[]) => { if (active) setRegions(rows.map((row) => row.country)); })
      .catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [open, regions.length]);

  useEffect(() => {
    const normalized = query.trim().toLocaleLowerCase();
    setActiveIndex(-1);
    if (!open || normalized.length < 2) {
      setResults([]);
      setLoading(false);
      return;
    }
    const request = ++requestId.current;
    const timeout = window.setTimeout(() => {
      setLoading(true);
      setError("");
      api.realRetail.products("value", 10, 0, query).then((rows: RealRetailProduct[]) => rows
        .filter((row) => isValidProductDescription(row.source_description))
        .map((row) => ({
          kind: "product" as const,
          name: row.source_description as string,
          detail: row.source_product_key ? `Product · ID ${row.source_product_key}` : "Product",
        }))).then((matchedProducts) => {
        if (request !== requestId.current) return;
        const matchedRegions = regions.filter((region) => region.toLocaleLowerCase().includes(normalized)).slice(0, 5)
          .map((name) => ({ kind: "region" as const, name, detail: "Country · Overview" }));
        setResults([...matchedProducts, ...matchedRegions]);
      }).catch((e: Error) => {
        if (request === requestId.current) { setResults([]); setError(e.message); }
      }).finally(() => { if (request === requestId.current) setLoading(false); });
    }, 250);
    return () => window.clearTimeout(timeout);
  }, [query, open, regions]);

  const choose = (result: SearchResult) => {
    setOpen(false);
    setQuery("");
    if (result.kind === "product") navigate(`/products?productSearch=${encodeURIComponent(result.name)}`);
    else navigate(`/?country=${encodeURIComponent(result.name)}`);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") { setOpen(false); setActiveIndex(-1); }
    else if (event.key === "ArrowDown" && results.length) { event.preventDefault(); setActiveIndex((current) => (current + 1) % results.length); }
    else if (event.key === "ArrowUp" && results.length) { event.preventDefault(); setActiveIndex((current) => (current <= 0 ? results.length - 1 : current - 1)); }
    else if (event.key === "Enter" && activeIndex >= 0) { event.preventDefault(); choose(results[activeIndex]); }
  };

  return <div className="global-search"><div className="search"><Search size={17} /><input aria-label="Search products and regions" aria-expanded={open} aria-controls="global-search-results" aria-activedescendant={activeIndex >= 0 ? `global-search-result-${activeIndex}` : undefined} role="combobox" placeholder="Search products, regions..." value={query} onFocus={() => setOpen(true)} onChange={(event) => { setQuery(event.target.value); setOpen(true); }} onKeyDown={onKeyDown} />{query && <button type="button" aria-label="Clear search" onClick={() => { setQuery(""); setResults([]); }}>×</button>}</div>
    {open && query.trim().length >= 2 && <div className="global-search-results" id="global-search-results" role="listbox" aria-label="Search results">
      {loading && <div className="search-status">Searching…</div>}
      {!loading && error && <div className="search-status search-error">{error}</div>}
      {!loading && !error && results.map((result, index) => <button type="button" role="option" aria-selected={index === activeIndex} id={`global-search-result-${index}`} key={`${result.kind}:${result.name}`} className={index === activeIndex ? "search-result active" : "search-result"} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(result)}><b>{result.name}</b><small>{result.detail}</small></button>)}
      {!loading && !error && !results.length && <div className="search-status">No matching products or regions.</div>}
    </div>}
  </div>;
}

export default function App() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const title = nav.find((item) => item.to === location.pathname)?.label ?? "Overview";
  return <div className="app-shell">
    <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
      <div className="brand"><span className="brand-mark"><Sparkles size={16} /></span><span>Retail Intelligence</span></div>
      <div className="workspace-switcher"><span className="workspace-dot" />Retail intelligence <ChevronDown size={14} /></div>
      <nav>{nav.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} onClick={() => setOpen(false)}><Icon size={18} />{label}</NavLink>)}</nav>
      <div className="sidebar-footer"><NavLink to="/products"><ShoppingBag size={18} />Catalog</NavLink><a href="mailto:analytics@example.com"><CircleHelp size={18} />Help & support</a><div className="profile"><span className="avatar">RS</span><span><b>Retail team</b><small>Workspace admin</small></span><ChevronDown size={15} /></div></div>
    </aside>
    <main className="main">
      <header className="topbar"><button className="mobile-menu" onClick={() => setOpen(!open)} aria-label="Toggle navigation">{open ? <X size={20} /> : <Menu size={20} />}</button><HeaderSearch /><div className="top-actions"><button aria-label="Help"><CircleHelp size={19} /></button><button aria-label="Notifications">◔</button><span className="top-avatar">RS</span><ChevronDown size={15} /></div></header>
      <div className="page-heading"><div><p className="eyebrow">Retail intelligence / {title}</p><h1>{title}</h1></div></div>
      <div className="page-content"><Routes><Route path="/" element={<Dashboard />} /><Route path="/products" element={<ProductExplorer />} /><Route path="/demand" element={<DemandAnalysis />} /><Route path="/clustering" element={<Clustering />} /><Route path="/basket" element={<BasketAnalysis />} /><Route path="/real-retail-analytics" element={<Navigate to="/" replace />} /></Routes></div>
    </main>
  </div>;
}
