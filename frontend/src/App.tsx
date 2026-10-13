import { useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { Boxes, ChevronDown, CircleHelp, LayoutDashboard, Menu, Network, Search, ShoppingBag, Sparkles, Target, X, BarChart3 } from "lucide-react";
import Dashboard from "./pages/Dashboard";
import ProductExplorer from "./pages/ProductExplorer";
import DemandAnalysis from "./pages/DemandAnalysis";
import BasketAnalysis from "./pages/BasketAnalysis";
import Clustering from "./pages/Clustering";

const nav = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/products", label: "Products", icon: Boxes },
  { to: "/demand", label: "Demand analysis", icon: Target },
  { to: "/clustering", label: "Product clustering", icon: BarChart3 },
  { to: "/basket", label: "Basket analysis", icon: Network },
];
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
      <header className="topbar"><button className="mobile-menu" onClick={() => setOpen(!open)} aria-label="Toggle navigation">{open ? <X size={20} /> : <Menu size={20} />}</button><div className="search"><Search size={17} /><input placeholder="Search products, regions..." /></div><div className="top-actions"><button aria-label="Help"><CircleHelp size={19} /></button><button aria-label="Notifications">◔</button><span className="top-avatar">RS</span><ChevronDown size={15} /></div></header>
      <div className="page-heading"><div><p className="eyebrow">Retail intelligence / {title}</p><h1>{title}</h1></div><span className="updated"><span className="status-dot" />Live warehouse data</span></div>
      <div className="page-content"><Routes><Route path="/" element={<Dashboard />} /><Route path="/products" element={<ProductExplorer />} /><Route path="/demand" element={<DemandAnalysis />} /><Route path="/clustering" element={<Clustering />} /><Route path="/basket" element={<BasketAnalysis />} /></Routes></div>
    </main>
  </div>;
}
