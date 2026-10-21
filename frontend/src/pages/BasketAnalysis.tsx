import { useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import { ApiError, api } from "../services/api";
import type { BasketResponse, BasketRule } from "../types";
import { formatCompactNumber, formatDecimal } from "../utils/format";
import { isValidProductDescription } from "../utils/productDescription";

export function filterRulesWithValidProductNames(
  rules: BasketRule[],
  productNames: Record<string, string | null> | undefined,
): BasketRule[] {
  return rules.filter((rule) =>
    [...rule.antecedents, ...rule.consequents]
      .every((id) => isValidProductDescription(productNames?.[id])),
  );
}

export default function BasketAnalysis() {
  const [data, setData] = useState<BasketResponse | null>(null);
  const [support, setSupport] = useState("0.01");
  const [confidence, setConfidence] = useState("0.2");
  const [ruleSearch, setRuleSearch] = useState("");
  const [ruleSide, setRuleSide] = useState("either");
  const [ruleLimit, setRuleLimit] = useState(50);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const requestId = useRef(0);
  const validate = (raw: string, label: string, minimum: number, inclusiveMinimum: boolean) => {
    const value = Number(raw.trim());
    if (raw.trim() === "" || !Number.isFinite(value) || (inclusiveMinimum ? value < minimum : value <= minimum) || value > 1) {
      throw new Error(`${label} must be a number ${inclusiveMinimum ? "from" : "greater than"} ${minimum} and no more than 1.`);
    }
    return value;
  };
  const load = () => {
    const currentRequest = ++requestId.current;
    try {
      const supportValue = validate(support, "Minimum support", 0, false);
      const confidenceValue = validate(confidence, "Minimum confidence", 0, true);
      setError(""); setData(null); setLoading(true);
      api.realRetail.basketRules(supportValue, confidenceValue).then((nextData) => {
        if (currentRequest === requestId.current) setData(nextData);
      }).catch((e: unknown) => {
        if (currentRequest !== requestId.current) return;
        if (e instanceof ApiError && e.status === 422) setError(`The basket-analysis filters are invalid. ${e.message}`);
        else if (e instanceof ApiError && e.status >= 500) setError(`The basket-analysis service failed. ${e.message}`);
        else setError("The basket-analysis service is unavailable. Check that FastAPI is running and try again.");
      }).finally(() => { if (currentRequest === requestId.current) setLoading(false); });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Enter valid basket-analysis filters.");
    }
  };
  useEffect(load, []);
  const itemLabel = (value: string) => data?.product_names?.[value] ?? "";
  const visibleRules = useMemo(() => {
    const term = ruleSearch.trim().toLocaleLowerCase();
    return filterRulesWithValidProductNames(data?.rules ?? [], data?.product_names).filter((rule) => {
      if (!term) return true;
      const antecedents = rule.antecedents.map(itemLabel);
      const consequents = rule.consequents.map(itemLabel);
      const sideItems = ruleSide === "antecedent" ? antecedents : ruleSide === "consequent" ? consequents : [...antecedents, ...consequents];
      return sideItems.some((name) => name.toLocaleLowerCase().includes(term));
    }).slice(0, ruleLimit);
  }, [data?.rules, data?.product_names, ruleSearch, ruleSide, ruleLimit]);
  if (!data && error) return <section className="page-section"><div className="inline-error" role="alert">{error}</div><button className="button secondary" onClick={load}>Try again</button></section>;
  if (!data) return <div className="loading-state">Loading basket rules...</div>;

  const clearRuleFilters = () => { setRuleSearch(""); setRuleSide("either"); setRuleLimit(50); };
  return <section className="page-section">
    <div className="section-intro"><div><h2>Basket analysis</h2><p>Discover products that are frequently purchased together.</p></div><span className="count-badge">{formatCompactNumber(visibleRules.length)} rules</span></div>
    <div className="disclaimer">Real Online Retail II baskets. Cancellation-invoice candidates, non-positive quantities, and products without valid descriptions are excluded from co-occurrence calculations.</div>
    <div className="basket-stats"><div><b>{formatCompactNumber(data.transaction_count)}</b><span>Transactions analyzed</span></div><div><b>{formatCompactNumber(data.frequent_itemsets.length)}</b><span>Frequent itemsets</span></div><div><b>{formatCompactNumber(visibleRules.length)}</b><span>Association rules</span></div></div>
    <div className="filters rule-filters">
      <label>Minimum support <input aria-describedby="support-help" type="number" inputMode="decimal" min=".001" max="1" step=".001" value={support} onChange={(e) => setSupport(e.target.value)} /><small id="support-help">Fraction: {support && Number.isFinite(Number(support)) ? `${formatDecimal(Number(support) * 100)}%` : "enter 0 to 1"}</small></label>
      <label>Minimum confidence <input aria-describedby="confidence-help" type="number" inputMode="decimal" min="0" max="1" step=".01" value={confidence} onChange={(e) => setConfidence(e.target.value)} /><small id="confidence-help">Fraction: {confidence && Number.isFinite(Number(confidence)) ? `${formatDecimal(Number(confidence) * 100)}%` : "enter 0 to 1"}</small></label>
      <button className="button primary" onClick={load} disabled={loading}><RefreshCw size={15} /> {loading ? "Running..." : "Run analysis"}</button>
    </div>
    <div className="filters basket-rule-filters">
      <label className="filter-search"><Search size={16} /><input aria-label="Search rule products" placeholder="Search product names in rules..." value={ruleSearch} onChange={(event) => setRuleSearch(event.target.value)} /></label>
      <label className="filter-control">Product appears on<select aria-label="Filter rule side" value={ruleSide} onChange={(event) => setRuleSide(event.target.value)}><option value="either">Either side</option><option value="antecedent">Left side (bought with)</option><option value="consequent">Right side (often with)</option></select></label>
      <label className="filter-control">Show rules<select aria-label="Maximum displayed rules" value={ruleLimit} onChange={(event) => setRuleLimit(Number(event.target.value))}><option value={10}>Top 10</option><option value={25}>Top 25</option><option value={50}>Top 50</option></select></label>
      <button className="button secondary filter-reset" onClick={clearRuleFilters}>Clear rule filters</button>
      <span className="count-badge">{formatCompactNumber(visibleRules.length)} shown</span>
    </div>
    {error && <div className="inline-error" role="alert">{error}</div>}
    <details className="how-it-works"><summary>How it works</summary><div><p><b>Support:</b> fraction of all baskets containing an itemset.</p><p><b>Confidence:</b> fraction of baskets containing the antecedent that also contain the consequent.</p><p><b>Lift:</b> confidence divided by the consequent's overall support; lift above 1 indicates a positive association.</p><small>Higher support and confidence thresholds generally reduce the number of returned rules.</small></div></details>
    <div className="table-panel"><table><thead><tr><th>Products bought</th><th>Often with</th><th>Support</th><th>Confidence</th><th>Lift</th></tr></thead><tbody>{visibleRules.map((rule, i) => <tr key={i}><td><b>{rule.antecedents.map(itemLabel).join(", ")}</b></td><td>{rule.consequents.map(itemLabel).join(", ")}</td><td>{formatDecimal(rule.support * 100)}%</td><td>{formatDecimal(rule.confidence * 100)}%</td><td><span className="lift">{formatDecimal(rule.lift)}×</span></td></tr>)}</tbody></table>{!visibleRules.length && <div className="empty-state">{data.rules.length ? "No rules match this product search and side filter." : data.message ?? "No rules meet these thresholds. Try lowering support or confidence."}</div>}</div>
    <div className="subtle-note">Multi-item baskets: {formatCompactNumber(data.multi_item_transaction_count)}. Frequent itemsets returned: {formatCompactNumber(data.frequent_itemsets.length)}.</div>
  </section>;
}
