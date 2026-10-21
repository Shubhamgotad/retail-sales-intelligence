import { useEffect, useRef, useState } from "react";
import { MoreHorizontal, RefreshCw } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../services/api";
import { useSearchParams } from "react-router-dom";
import type { RealRetailCountry, RealRetailMonthly, RealRetailOverview, RealRetailProduct } from "../types";
import { formatCompactGBP, formatCompactNumber, formatGBP, formatMonth } from "../utils/format";
import { isValidProductDescription } from "../utils/productDescription";
const palette = ["#3e3028", "#746456", "#9a8975", "#b9aa98", "#d4c8b9", "#e7dfd4"];
const formatGBPCompact = (value: number) => `£${formatCompactNumber(value)}`;

type DashboardData = {
  overview: { cards: [string, string, string][]; monthly: { month: string; value: number; exact: string }[]; groups: { name: string; value: number; exact: string }[]; products: { id: string; name: string; detail: string; value: string }[] };
  missingInvoiceLines: number;
};

type CountryChartRow = { name: string; value: number; exact: string };

function sumExactDecimals(values: string[]): string {
  const parts = values.map((value) => value.match(/^([+-]?)(\d+)(?:\.(\d+))?$/));
  if (parts.some((part) => part === null)) {
    return String(values.reduce((total, value) => total + Number(value), 0));
  }
  const decimals = parts as RegExpMatchArray[];
  const scale = Math.max(0, ...decimals.map((part) => (part[3] ?? "").length));
  const total = decimals.reduce((sum, part) => {
    const whole = part[2];
    const fraction = (part[3] ?? "").padEnd(scale, "0");
    const magnitude = BigInt(`${whole}${fraction}`);
    return sum + (part[1] === "-" ? -magnitude : magnitude);
  }, 0n);
  const negative = total < 0n;
  const digits = (negative ? -total : total).toString().padStart(scale + 1, "0");
  const whole = scale ? digits.slice(0, -scale) : digits;
  const fraction = scale ? digits.slice(-scale).replace(/0+$/, "") : "";
  return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}

function compareExactDecimals(left: string, right: string): number {
  const leftParts = left.match(/^([+-]?)(\d+)(?:\.(\d+))?$/);
  const rightParts = right.match(/^([+-]?)(\d+)(?:\.(\d+))?$/);
  if (!leftParts || !rightParts) return Number(right) - Number(left);
  const scale = Math.max(leftParts[3]?.length ?? 0, rightParts[3]?.length ?? 0);
  const toScaledInteger = (parts: RegExpMatchArray) => {
    const magnitude = BigInt(`${parts[2]}${(parts[3] ?? "").padEnd(scale, "0")}`);
    return parts[1] === "-" ? -magnitude : magnitude;
  };
  const leftValue = toScaledInteger(leftParts);
  const rightValue = toScaledInteger(rightParts);
  return leftValue < rightValue ? 1 : leftValue > rightValue ? -1 : 0;
}

export function getTopCountryChartData(
  groups: CountryChartRow[],
  selectedCountry?: string,
): CountryChartRow[] {
  const grouped = new Map<string, { name: string; amounts: string[] }>();
  const selectedValue = selectedCountry?.trim();
  const selectedName = !selectedValue || selectedValue === "[missing]"
    ? selectedValue ? "Unknown country" : undefined
    : selectedValue;
  const selected = selectedName?.toLocaleLowerCase();
  for (const row of groups) {
    const cleanName = row.name.trim();
    const name = !cleanName || cleanName === "[missing]" ? "Unknown country" : cleanName;
    if (selected && name.toLocaleLowerCase() !== selected) continue;
    const existing = grouped.get(name);
    if (existing) existing.amounts.push(row.exact);
    else grouped.set(name, { name, amounts: [row.exact] });
  }
  return Array.from(grouped.values())
    .map(({ name, amounts }): CountryChartRow => {
      const exact = sumExactDecimals(amounts);
      return { name, exact, value: Number(exact) };
    })
    .sort((left, right) => compareExactDecimals(left.exact, right.exact) || left.name.localeCompare(right.name))
    .slice(0, 10);
}

export default function Dashboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");
  const requestId = useRef(0);
  const load = () => {
    const currentRequest = ++requestId.current;
    setError(""); setData(null);
    Promise.all([api.realRetail.overview(), api.realRetail.monthly(), api.realRetail.countries("value", 200), api.realRetail.products("value", 5, 0)])
      .then(([overview, monthly, countries, products]) => { if (currentRequest === requestId.current) setData(normalizeReal(overview, monthly, countries, products)); })
      .catch((e: Error) => { if (currentRequest === requestId.current) setError(e.message); });
  };
  useEffect(() => { load(); }, []);
  if (error) return <ErrorState message={error} retry={load} />;
  if (!data) return <section className="dashboard"><div className="metric-grid">{[1,2,3,4].map((n) => <div className="metric-card skeleton" key={n} />)}</div></section>;
  const { overview } = data;
  const selectedCountry = searchParams.get("country");
  const displayedGroups = selectedCountry
    ? overview.groups.filter((group) => group.name.toLocaleLowerCase() === selectedCountry.toLocaleLowerCase())
    : overview.groups;
  const countryChartData = getTopCountryChartData(overview.groups, selectedCountry ?? undefined);
  const positiveCountryValues = countryChartData.map((item) => item.value).filter((value) => value > 0);
  const useLogCountryAxis = positiveCountryValues.length === countryChartData.length &&
    positiveCountryValues.length > 1 &&
    Math.max(...positiveCountryValues) / Math.min(...positiveCountryValues) >= 20;
  return <section className="dashboard">
    <div className="toolbar"><div><h2>Online Retail II intelligence</h2><p>Read-only source analytics from the isolated real-retail warehouse.</p></div><div className="toolbar-actions"><button className="button secondary" onClick={load}><RefreshCw size={15} /> Refresh</button></div></div>
    <div className="disclaimer">Monetary values are in <b>GBP (£)</b> per the UCI Online Retail II documentation. No currency conversion is applied. C-prefixed cancellation candidates are excluded, other negative quantities retain their signed values, and lines without invoice IDs remain included as ambiguous records ({formatCompactNumber(data.missingInvoiceLines)} lines).</div>
    <div className="metric-grid overview-kpi-grid">{overview.cards.map(([label, value, note]) => <div className="metric-card" key={label}><div className="metric-label">{label}<MoreHorizontal size={16} /></div><strong title={value}>{value}</strong><small>{note}</small></div>)}</div>
    <div className="content-grid overview-grid">
      {selectedCountry && <div className="disclaimer selected-region-filter">Overview region filter: <b>{selectedCountry}</b><button type="button" className="button secondary" onClick={() => { const params = new URLSearchParams(searchParams); params.delete("country"); setSearchParams(params); }}>Clear region</button></div>}
      <div className="panel sales-panel"><PanelHead title="Source monetary values over time" detail="Monthly source amounts · GBP (£)" /><div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><LineChart data={overview.monthly}><CartesianGrid vertical={false} stroke="#dfd7cc" /><XAxis dataKey="month" tickFormatter={formatMonth} tickLine={false} axisLine={false} /><YAxis tickLine={false} axisLine={false} tickFormatter={(v) => formatGBPCompact(Number(v))} width={70} /><Tooltip labelFormatter={(label) => `Month: ${formatMonth(String(label))}`} formatter={(_, __, item) => [formatGBP(item.payload.exact), "Source value"]} /><Line type="monotone" dataKey="value" stroke="#332820" strokeWidth={2} dot={false} /></LineChart></ResponsiveContainer></div></div>
      <div className="panel source-panel"><PanelHead title="Source value by country" detail="Country share of source monetary values · GBP (£)" /><div className="donut-layout"><ResponsiveContainer width="54%" height={190}><PieChart><Pie data={displayedGroups} dataKey="value" nameKey="name" innerRadius={53} outerRadius={78} paddingAngle={2}>{displayedGroups.map((_, i) => <Cell key={i} fill={palette[i % palette.length]} />)}</Pie><Tooltip formatter={(_, __, item) => formatGBP(item.payload.exact)} /></PieChart></ResponsiveContainer><div className="legend">{displayedGroups.slice(0, 6).map((group, i) => <div key={group.name}><i style={{ background: palette[i % palette.length] }} /><span>{group.name}</span><b>{overview.groups.reduce((sum, item) => sum + item.value, 0) ? `${((group.value / overview.groups.reduce((sum, item) => sum + item.value, 0)) * 100).toFixed(0)}%` : "—"}</b></div>)}</div></div></div>
      <div className="panel region-panel"><PanelHead title="Top 10 countries by source value" detail={`Source monetary value · GBP (£)${useLogCountryAxis ? " · logarithmic scale" : ""}${selectedCountry ? ` · ${selectedCountry}` : ""}`} /><ResponsiveContainer width="100%" height={340}><BarChart data={countryChartData} layout="vertical" margin={{ left: 8, right: 16, top: 4, bottom: 4 }}><CartesianGrid horizontal={false} stroke="#e2dbd2" /><XAxis type="number" scale={useLogCountryAxis ? "log" : "auto"} domain={useLogCountryAxis ? [Math.min(...positiveCountryValues), "auto"] : ["auto", "auto"]} tickFormatter={(value) => formatGBPCompact(Number(value))} tickLine={false} axisLine={false} allowDataOverflow /><YAxis type="category" dataKey="name" width={130} tickLine={false} axisLine={false} interval={0} /><Tooltip labelFormatter={(label) => String(label)} formatter={(_, __, item) => [formatGBP(item.payload.exact), "Source monetary value"]} /><Bar dataKey="value" fill="#887565" radius={[0, 4, 4, 0]} barSize={18} /></BarChart></ResponsiveContainer></div>
      <div className="panel product-panel"><PanelHead title="Top source products" detail="By source monetary value" /><div className="product-list">{overview.products.map((item, i) => <div className="product-row" key={item.id}><span className="rank">{String(i + 1).padStart(2, "0")}</span><span className="product-name"><b>{item.name}</b><small>{item.detail}</small></span><span className="product-value">{item.value}</span></div>)}</div></div>
    </div>
  </section>;
}

export function normalizeReal(summary: RealRetailOverview, monthly: RealRetailMonthly[], countries: RealRetailCountry[], products: RealRetailProduct[]): DashboardData {
  return { missingInvoiceLines: summary.missing_invoice_lines, overview: { cards: [
    ["Source monetary value", formatCompactGBP(summary.source_monetary_total), "Source total · GBP"],
    ["Source sales lines", formatCompactNumber(summary.total_sales_lines), "Rows preserved"],
    ["Distinct invoices", formatCompactNumber(summary.distinct_invoices), "Source invoice values"],
    ["Distinct customers", formatCompactNumber(summary.distinct_customers), "Identifiable customers"],
  ],
  monthly: monthly.map((item) => ({ month: item.month, value: Number(item.source_monetary_total), exact: item.source_monetary_total })), groups: countries.map((item) => ({ name: item.country, value: Number(item.monetary_total), exact: item.monetary_total })), products: products.filter((item): item is RealRetailProduct & { source_description: string } =>
    isValidProductDescription(item.source_description),
  ).map((item) => ({ id: item.source_product_key ?? "[missing ID]", name: item.source_description, detail: item.source_product_key ?? "Product identifier unavailable", value: formatGBP(item.monetary_total) })) } };
}
function PanelHead({ title, detail }: { title: string; detail: string }) { return <div className="panel-head"><div><h3>{title}</h3><small>{detail}</small></div><MoreHorizontal size={18} /></div>; }
export function ErrorState({ message, retry }: { message: string; retry: () => void }) { return <div className="error-state"><h3>We couldn't reach the warehouse</h3><p>{message}. Start the FastAPI service and try again.</p><button className="button secondary" onClick={retry}>Try again</button></div>; }
