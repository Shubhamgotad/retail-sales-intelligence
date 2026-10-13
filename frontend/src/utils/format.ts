const currency = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
const compactCurrency = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", notation: "compact", maximumFractionDigits: 1 });
export const formatCurrency = (value: number) => currency.format(value);
export const formatCompactCurrency = (value: number) => compactCurrency.format(value);
export const formatCompactINRTick = (value: number) => {
  if (!Number.isFinite(value)) return "₹—";
  const sign = value < 0 ? "-" : "";
  const absolute = Math.abs(value);
  if (absolute >= 10000000) return `${sign}₹${(absolute / 10000000).toFixed(absolute >= 100000000 ? 0 : 1)}Cr`;
  if (absolute >= 100000) return `${sign}₹${(absolute / 100000).toFixed(absolute >= 1000000 ? 0 : 1)}L`;
  if (absolute >= 1000) return `${sign}₹${(absolute / 1000).toFixed(absolute >= 100000 ? 0 : 1)}K`;
  return `${sign}₹${Math.round(absolute)}`;
};
export const formatMonth = (value: string) => {
  const [year, month] = value.split("-").map(Number);
  if (!year || !month) return value;
  return new Intl.DateTimeFormat("en-IN", { month: "short", year: "2-digit" }).format(new Date(year, month - 1, 1));
};
export const sortMonthly = <T extends { month: string }>(values: T[]) => [...values].sort((a, b) => a.month.localeCompare(b.month));
