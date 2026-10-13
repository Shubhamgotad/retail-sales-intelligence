import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { ApiError, api } from "../services/api";
import type { BasketResponse } from "../types";
export default function BasketAnalysis() {
  const [data, setData] = useState<BasketResponse | null>(null), [support, setSupport] = useState("0.01"), [confidence, setConfidence] = useState("0.2"), [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const validate = (raw: string, label: string, minimum: number, inclusiveMinimum: boolean) => {
    const value = Number(raw.trim());
    if (raw.trim() === "" || !Number.isFinite(value) || (inclusiveMinimum ? value < minimum : value <= minimum) || value > 1) {
      throw new Error(`${label} must be a number ${inclusiveMinimum ? "from" : "greater than"} ${minimum} and no more than 1.`);
    }
    return value;
  };
  const load = () => {
    try {
      const supportValue = validate(support, "Minimum support", 0, false);
      const confidenceValue = validate(confidence, "Minimum confidence", 0, true);
      setError(""); setLoading(true);
      api.basketRules(supportValue, confidenceValue).then(setData).catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 422) setError(`The basket-analysis filters are invalid. ${e.message}`);
        else if (e instanceof ApiError && e.status >= 500) setError(`The basket-analysis service failed. ${e.message}`);
        else setError("The basket-analysis service is unavailable. Check that FastAPI is running and try again.");
      }).finally(() => setLoading(false));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Enter valid basket-analysis filters.");
    }
  };
  useEffect(load, []);
  if (!data && error) return <section className="page-section"><div className="inline-error" role="alert">{error}</div><button className="button secondary" onClick={load}>Try again</button></section>;
  if (!data) return <div className="loading-state">Loading basket rules...</div>;
  return <section className="page-section"><div className="section-intro"><div><h2>Basket analysis</h2><p>Discover products that are frequently purchased together.</p></div><span className="count-badge">{data.rules.length} rules</span></div><div className="disclaimer">Synthetic demonstration data: these baskets do not represent genuine customer purchasing behavior.</div><div className="basket-stats"><div><b>{data.transaction_count.toLocaleString("en-IN")}</b><span>Transactions analyzed</span></div><div><b>{data.frequent_itemsets.length.toLocaleString("en-IN")}</b><span>Frequent itemsets</span></div><div><b>{data.rules.length.toLocaleString("en-IN")}</b><span>Association rules</span></div></div><div className="filters rule-filters"><label>Minimum support <input aria-describedby="support-help" type="number" inputMode="decimal" min=".001" max="1" step=".001" value={support} onChange={(e) => setSupport(e.target.value)} /><small id="support-help">Fraction: {support && Number.isFinite(Number(support)) ? `${(Number(support) * 100).toFixed(0)}%` : "enter 0 to 1"}</small></label><label>Minimum confidence <input aria-describedby="confidence-help" type="number" inputMode="decimal" min="0" max="1" step=".01" value={confidence} onChange={(e) => setConfidence(e.target.value)} /><small id="confidence-help">Fraction: {confidence && Number.isFinite(Number(confidence)) ? `${(Number(confidence) * 100).toFixed(0)}%` : "enter 0 to 1"}</small></label><button className="button primary" onClick={load} disabled={loading}><RefreshCw size={15} /> {loading ? "Running..." : "Run analysis"}</button></div>{error && <div className="inline-error" role="alert">{error}</div>}<details className="how-it-works"><summary>How it works</summary><div><p><b>Support:</b> fraction of all baskets containing an itemset.</p><p><b>Confidence:</b> fraction of baskets containing the antecedent that also contain the consequent.</p><p><b>Lift:</b> confidence divided by the consequent's overall support; lift above 1 indicates a positive association.</p><small>Higher support and confidence thresholds generally reduce the number of returned rules.</small></div></details><div className="table-panel"><table><thead><tr><th>Products bought</th><th>Often with</th><th>Support</th><th>Confidence</th><th>Lift</th></tr></thead><tbody>{data.rules.map((rule, i) => <tr key={i}><td><b>{rule.antecedents.join(", ")}</b></td><td>{rule.consequents.join(", ")}</td><td>{(rule.support * 100).toFixed(2)}%</td><td>{(rule.confidence * 100).toFixed(1)}%</td><td><span className="lift">{rule.lift.toFixed(2)}×</span></td></tr>)}</tbody></table>{!data.rules.length && <div className="empty-state">{data.message ?? "No rules meet these thresholds. Try lowering support or confidence."}</div>}</div><div className="subtle-note">Multi-item baskets: {data.multi_item_transaction_count.toLocaleString("en-IN")}. Frequent itemsets returned: {data.frequent_itemsets.length.toLocaleString("en-IN")}.</div></section>;
}
