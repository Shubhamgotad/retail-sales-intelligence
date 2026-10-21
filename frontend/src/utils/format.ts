const currency = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
const compactCurrency = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", notation: "compact", maximumFractionDigits: 1 });
const compactNumber = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });
const compactGBP = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", notation: "compact", maximumFractionDigits: 2 });
const detailedGBP = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const decimalNumber = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 });
export const formatCurrency = (value: number) => currency.format(value);
export const formatCompactCurrency = (value: number) => compactCurrency.format(value);
export const formatCompactNumber = (value: number | string) => {
  const numericValue = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numericValue)) return String(value);
  return Math.abs(numericValue) >= 1000 ? compactNumber.format(numericValue) : formatDecimal(value);
};
export const formatDecimal = (value: number | string) => {
  if (typeof value === "number") {
    return Number.isFinite(value) ? decimalNumber.format(value) : String(value);
  }
  const match = value.trim().match(/^([+-]?)(\d+)(?:\.(\d*))?$/);
  if (!match) {
    const numericValue = Number(value);
    return Number.isFinite(numericValue) ? decimalNumber.format(numericValue) : value;
  }
  const [, sign, integer, fraction = ""] = match;
  const digits = `${integer}${fraction.padEnd(4, "0").slice(0, 4)}`;
  const rounded = BigInt(digits) + (fraction.length > 4 && fraction[4] >= "5" ? 1n : 0n);
  if (rounded === 0n && /[1-9]/.test(fraction)) {
    return `${sign === "-" ? "-" : ""}<0.0001`;
  }
  const fixed = rounded.toString().padStart(5, "0");
  const whole = fixed.slice(0, -4).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const decimal = fixed.slice(-4).replace(/0+$/, "");
  return `${sign === "-" && rounded !== 0n ? "-" : ""}${whole}${decimal ? `.${decimal}` : ""}`;
};
export const formatGBP = (value: number | string) => `GBP £${formatDecimal(value)}`;
export const formatCompactGBP = (value: number | string) => {
  const numericValue = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numericValue) ? compactGBP.format(numericValue) : String(value);
};
export const formatDetailedGBP = (value: number | string) => {
  const numericValue = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numericValue) ? `GBP ${detailedGBP.format(numericValue)}` : String(value);
};
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
