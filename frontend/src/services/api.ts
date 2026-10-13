import type { BasketResponse, CategorySale, ClusterResponse, DemandResponse, MonthlySale, Product, RegionSale, Summary, TopProduct } from "../types";
const baseUrl = import.meta.env.VITE_API_URL ?? "";
export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}
async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`);
  if (!response.ok) {
    let detail = `Request failed with HTTP ${response.status}.`;
    try {
      const body = await response.json() as { detail?: string | Array<{ msg?: string }> };
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((item) => item.msg ?? "Invalid value").join("; ");
    } catch {
      // Keep the status-based message when an error response is not JSON.
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}
export const api = {
  summary: () => get<Summary>("/api/analytics/summary"),
  monthlySales: () => get<MonthlySale[]>("/api/analytics/monthly-sales"),
  categorySales: () => get<CategorySale[]>("/api/analytics/sales-by-category"),
  regionSales: () => get<RegionSale[]>("/api/analytics/sales-by-region"),
  topProducts: (limit = 10) => get<TopProduct[]>(`/api/analytics/top-products?limit=${limit}`),
  products: () => get<Product[]>("/api/products?limit=500"),
  demand: () => get<DemandResponse>("/api/classification/demand"),
  clusters: (count: number) => get<ClusterResponse>(`/api/clustering/products?n_clusters=${count}`),
  basketRules: (support: number, confidence: number) => {
    const params = new URLSearchParams({ min_support: String(support), min_confidence: String(confidence) });
    return get<BasketResponse>(`/api/basket/rules?${params.toString()}`);
  },
};
