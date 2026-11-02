import type { AprioriRecommendationsResponse, BasketResponse, RandomForestDemandForecast, RealRetailOverview, RealRetailMonthly, RealRetailCountry, RealRetailProduct, RealRetailCustomer, RealRetailQuality, RealRetailDemandResponse, RealRetailClusterResponse } from "../types";
const baseUrl = import.meta.env.VITE_API_URL ?? "";
const pendingRequests = new Map<string, Promise<unknown>>();
export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}
async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const url = `${baseUrl}${path}`;
  const pending = signal ? undefined : pendingRequests.get(url);
  if (pending) return pending as Promise<T>;
  const request = (async () => {
    const response = signal ? await fetch(url, { signal }) : await fetch(url);
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
  })();
  if (!signal) {
    pendingRequests.set(url, request);
    const clearPending = () => {
      if (pendingRequests.get(url) === request) pendingRequests.delete(url);
    };
    request.then(clearPending, clearPending);
  }
  return request;
}
export const api = {
  realRetail: {
    overview: () => get<RealRetailOverview>("/api/real-retail/analytics/overview"),
    monthly: (startDate?: string, endDate?: string) => {
      const params = new URLSearchParams();
      if (startDate) params.set("start_date", startDate);
      if (endDate) params.set("end_date", endDate);
      return get<RealRetailMonthly[]>(`/api/real-retail/analytics/monthly${params.toString() ? `?${params}` : ""}`);
    },
    countries: (sort = "value", limit = 25) => get<RealRetailCountry[]>(`/api/real-retail/analytics/countries?sort=${sort}&limit=${limit}`),
    products: (sort = "value", limit = 25, offset = 0, search?: string, signal?: AbortSignal) => {
      const params = new URLSearchParams({ sort, limit: String(limit), offset: String(offset) });
      if (search?.trim()) params.set("search", search.trim());
      return get<RealRetailProduct[]>(`/api/real-retail/analytics/products?${params}`, signal);
    },
    customers: (sort = "value", limit = 25, offset = 0) => get<RealRetailCustomer[]>(`/api/real-retail/analytics/customers?sort=${sort}&limit=${limit}&offset=${offset}`),
    quality: () => get<RealRetailQuality>("/api/real-retail/analytics/quality"),
    demand: () => get<RealRetailDemandResponse>("/api/real-retail/features/demand"),
    demandForecast: (productCode: string) => get<RandomForestDemandForecast>(`/api/real-retail/features/demand-forecast/${encodeURIComponent(productCode.trim())}`),
    clusters: () => get<RealRetailClusterResponse>("/api/real-retail/features/clustering"),
    basketRules: (support: number, confidence: number) => get<BasketResponse>(`/api/real-retail/features/basket?min_support=${support}&min_confidence=${confidence}`),
    aprioriRecommendations: (productCode: string) => get<AprioriRecommendationsResponse>(`/api/real-retail/features/recommendations/${encodeURIComponent(productCode.trim())}`),
  },
};
