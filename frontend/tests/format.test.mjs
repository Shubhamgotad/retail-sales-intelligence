import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { createServer } from "vite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

const vite = await createServer({
  configFile: false,
  root: fileURLToPath(new URL("..", import.meta.url)),
  server: { middlewareMode: true },
  appType: "custom",
});
const formatters = await vite.ssrLoadModule("/src/utils/format.ts");
const productDescriptions = await vite.ssrLoadModule("/src/utils/productDescription.ts");
const dashboard = await vite.ssrLoadModule("/src/pages/Dashboard.tsx");
const { getTopCountryChartData } = dashboard;
const productExplorer = await vite.ssrLoadModule("/src/pages/ProductExplorer.tsx");
const demandPage = await vite.ssrLoadModule("/src/pages/DemandAnalysis.tsx");
const clusteringPage = await vite.ssrLoadModule("/src/pages/Clustering.tsx");
const basketPage = await vite.ssrLoadModule("/src/pages/BasketAnalysis.tsx");
const App = (await vite.ssrLoadModule("/src/App.tsx")).default;
const { paginate } = await vite.ssrLoadModule("/src/utils/pagination.ts");
const { api } = await vite.ssrLoadModule("/src/services/api.ts");
after(() => vite.close());

test("formats long decimal strings without converting them through floating point", () => {
  assert.equal(formatters.formatDecimal("12345678901234567890.987654321"), "12,345,678,901,234,567,890.9877");
});

test("trims unnecessary fractional zeros", () => {
  assert.equal(formatters.formatDecimal("1200.340000"), "1,200.34");
  assert.equal(formatters.formatDecimal("42.0000"), "42");
});

test("formats signed and small decimal values to four places", () => {
  assert.equal(formatters.formatDecimal("-12.345678"), "-12.3457");
  assert.equal(formatters.formatDecimal("0.00005"), "0.0001");
  assert.equal(formatters.formatDecimal("0.00001"), "<0.0001");
  assert.equal(formatters.formatDecimal("-0.00001"), "-<0.0001");
});

test("uses compact notation for large counts", () => {
  assert.deepEqual([1000, 12500, 1000000, 2450000].map(formatters.formatCompactNumber), ["1K", "12.5K", "1M", "2.45M"]);
});

test("formats GBP strings consistently without losing source precision early", () => {
  assert.equal(formatters.formatGBP("-1234.567890"), "GBP £-1,234.5679");
  assert.equal(formatters.formatDetailedGBP("19287250.568000"), "GBP £19,287,250.57");
  assert.equal(formatters.formatCompactGBP("19287250.568000"), "£19.29M");
});

test("real dashboard retains sales analytics without profitability fields", () => {
  const real = dashboard.normalizeReal({
    total_sales_lines: 2,
    distinct_invoices: 2,
    distinct_customers: 2,
    distinct_products: 2,
    missing_customer_lines: 0,
    missing_customer_quantity_total: "0",
    missing_customer_monetary_total: "0",
    signed_quantity_total: "3",
    source_monetary_total: "80.00",
    cancellation_candidates: 0,
    potential_returns: 0,
    missing_value_lines: 0,
    missing_invoice_lines: 0,
    negative_price_anomalies: 0,
    zero_price_lines: 0,
    missing_descriptions: 0,
    missing_customer_ids: 0,
    duplicate_content_rows: 0,
    currency_code: "GBP",
    warnings: [],
  }, [], [], [
    { source_product_key: "P1", source_description: " Tea & cup! ", line_count: 1, missing_description_lines: 0, potential_return_lines: 0, zero_price_lines: 0, quantity_total: "2", monetary_total: "12", currency_code: "GBP" },
    { source_product_key: "P2", source_description: "??", line_count: 1, missing_description_lines: 0, potential_return_lines: 0, zero_price_lines: 0, quantity_total: "2", monetary_total: "12", currency_code: "GBP" },
  ]);
  assert.deepEqual(real.overview.cards.map(([label]) => label), [
    "Source monetary value",
    "Source sales lines",
    "Distinct invoices",
    "Distinct customers",
  ]);
  assert.equal(real.overview.cards[0][1], "£80.00");
  assert.deepEqual(real.overview.products.map(({ name }) => name), [" Tea & cup! "]);
  assert.doesNotMatch(JSON.stringify(real), /profit|cost|margin/i);
});

test("country chart data is sorted, limited, and shared by labels and exact tooltip values", () => {
  const countries = [
    ...Array.from({ length: 12 }, (_, index) => ({
      name: `Country ${index + 1}`,
      exact: String((12 - index) * 100),
      value: (12 - index) * 100,
    })),
    { name: "", exact: "1.10", value: 1.1 },
    { name: "[missing]", exact: "2.20", value: 2.2 },
  ];
  const chart = getTopCountryChartData(countries);
  assert.equal(chart.length, 10);
  assert.deepEqual(chart.map(({ name }) => name), [
    "Country 1",
    "Country 2",
    "Country 3",
    "Country 4",
    "Country 5",
    "Country 6",
    "Country 7",
    "Country 8",
    "Country 9",
    "Country 10",
  ]);
  assert.deepEqual(chart.map(({ value }) => value), chart.map(({ exact }) => Number(exact)));
  assert.equal(getTopCountryChartData(countries.slice(-2))[0].name, "Unknown country");
  assert.equal(getTopCountryChartData(countries.slice(-2))[0].exact, "3.3");
  assert.deepEqual(getTopCountryChartData(countries, "Country 12").map(({ name }) => name), ["Country 12"]);
  assert.equal(getTopCountryChartData([
    { name: "Lower", exact: "9007199254740992", value: Number("9007199254740992") },
    { name: "Higher", exact: "9007199254740993", value: Number("9007199254740993") },
  ])[0].name, "Higher");
});

test("product explorer uses real retail data and renders no profitability UI", () => {
  const markup = renderToStaticMarkup(createElement(
    MemoryRouter,
    null,
    createElement(productExplorer.default),
  ));
  assert.match(markup, /Product catalog/);
  assert.match(markup, /Signed quantity/);
  assert.match(markup, /Source value/);
  assert.match(markup, /Sort products/);
  assert.doesNotMatch(markup, /profit|gross.?margin|actual.?cost|unit.?cost|cost coverage|COGS/i);
  assert.doesNotMatch(markup, /Unnamed product|Synthetic demo|Data source/i);
  assert.equal(productExplorer.fromReal({
    source_product_key: "P1", source_description: "??", line_count: 1,
    missing_description_lines: 0, potential_return_lines: 0, zero_price_lines: 0,
    quantity_total: "1", monetary_total: "1", currency_code: "GBP",
  }), null);
  assert.equal(productExplorer.fromReal({
    source_product_key: "P2", source_description: "Tea & cup!",
    line_count: 1, missing_description_lines: 0, potential_return_lines: 0,
    zero_price_lines: 0, quantity_total: "1", monetary_total: "1", currency_code: "GBP",
  }).name, "Tea & cup!");
});

test("real-only app has no data-source selector and labels clustering as customer-based", () => {
  const markup = renderToStaticMarkup(createElement(
    MemoryRouter,
    { initialEntries: ["/clustering"] },
    createElement(App),
  ));
  assert.match(markup, /Customer clustering/);
  assert.doesNotMatch(markup, /Data source|Synthetic demo|Product clustering/i);
});

test("product-description validation rejects unusable labels and accepts punctuation names", () => {
  for (const name of [null, undefined, "", " \t\r\n ", "?", "??", "????", "!?.,-", "[missing description]", " [ Missing \tdescription ] "]) {
    assert.equal(productDescriptions.isValidProductDescription(name), false);
  }
  for (const name of ["Tea & cup!", "Product #12 (large)", " café-12 "]) {
    assert.equal(productDescriptions.isValidProductDescription(name), true);
  }
  assert.equal(demandPage.normalize({
    product_id: "bad", product_name: "??", last_observed_month: "2011-01",
    last_month_units: 2, predicted_demand: "low",
  }), null);
  assert.equal(demandPage.normalize({
    product_id: "good", product_name: "Tea & cup!", last_observed_month: "2011-01",
    last_month_units: 2, predicted_demand: "low",
  }).name, "Tea & cup!");
});

test("Demand table renderer excludes missing and placeholder descriptions", () => {
  const markup = renderToStaticMarkup(createElement(demandPage.DemandTableRows, {
    rows: [
      { id: "16044", name: "[missing description]", month: "2011-01", units: 1, label: "low" },
      { id: "16047", name: "??", month: "2011-01", units: 1, label: "low" },
      { id: "16049", name: "   ", month: "2011-01", units: 1, label: "low" },
      { id: "16119", name: "Tea & cup!", month: "2011-01", units: 1, label: "high" },
    ],
  }));
  assert.doesNotMatch(markup, /missing description|16044|16047|16049/);
  assert.match(markup, /Tea &amp; cup!/);
  assert.match(markup, /16119/);
});

test("demand summary counts returned classifications and paginates filtered sorted data", () => {
  const rows = Array.from({ length: 123 }, (_, index) => ({
    id: String(index + 1),
    name: `Product ${String(123 - index).padStart(3, "0")}`,
    month: "2011-01",
    units: index - 30,
    label: ["high", "medium", "low"][index % 3],
  }));
  assert.deepEqual(demandPage.getDemandDistribution(rows).map(({ key, products }) => [key, products]), [
    ["high", 41], ["medium", 41], ["low", 41],
  ]);
  const matches = demandPage.filterAndSortDemandRows(rows, "product", "medium", "units");
  assert.equal(matches.length, 41);
  assert.equal(matches[0].units, 91);
  const first = paginate(matches, 1, 50);
  const second = paginate(matches, 2, 50);
  assert.equal(first.rows.length, 41);
  assert.equal(second.page, 1);
  assert.deepEqual([first.start, first.end, first.pageCount], [1, 41, 1]);
  const all = demandPage.filterAndSortDemandRows(rows, "product", null, "units");
  const pages = [paginate(all, 1, 50), paginate(all, 2, 50), paginate(all, 3, 50)];
  assert.deepEqual(pages.map(({ rows: pageRows }) => pageRows.length), [50, 50, 23]);
  assert.deepEqual(new Set(pages.flatMap(({ rows: pageRows }) => pageRows.map(({ id }) => id))).size, 123);
  assert.equal(pages[0].rows[0].units, 92);
  assert.equal(pages[2].rows[22].units, -30);
  assert.equal(paginate(all, 3, 50).start, 101);
  assert.equal(paginate(all, 3, 50).end, 123);
});

test("cluster filtering and pagination follow returned assignments and selection toggles", () => {
  const rows = Array.from({ length: 121 }, (_, index) => ({
    cluster: index % 3,
    id: String(index + 1),
    name: `Customer ${index + 1}`,
    category: "Customer",
    primary: index * 10,
    secondary: index - 60,
  }));
  const selected = clusteringPage.filterAndSortClusterRows(rows, "", 1, "primary");
  assert.equal(selected.length, 40);
  assert.ok(selected.every(({ cluster }) => cluster === 1));
  assert.deepEqual([
    clusteringPage.toggleClusterSelection(null, 1),
    clusteringPage.toggleClusterSelection(1, 1),
    clusteringPage.toggleClusterSelection(1, 2),
  ], [1, null, 2]);
  assert.equal(clusteringPage.clusterColor(0), "#3e3028");
  assert.notEqual(clusteringPage.clusterColor(0), clusteringPage.clusterColor(1));
  const first = paginate(rows, 1, 50);
  const second = paginate(rows, 2, 50);
  const third = paginate(rows, 3, 50);
  assert.deepEqual([first.rows.length, second.rows.length, third.rows.length], [50, 50, 21]);
  assert.equal(new Set([...first.rows, ...second.rows, ...third.rows].map(({ id }) => id)).size, 121);
  assert.equal(paginate(selected, 1, 50).rows.length, 40);
});

test("customer-cluster normalization preserves source customer identity and assignments", () => {
  const normalized = clusteringPage.normalizeCustomerClusters({
    algorithm: "K-Means",
    features: ["invoice_count", "line_count", "signed_quantity", "monetary_value"],
    cluster_count: 1,
    excluded_missing_customer_lines: 2,
    currency_code: "GBP",
    clusters: [{ cluster: 0, customer_count: 1, total_monetary_value: "42.50", total_signed_quantity: -1, customers: ["100"] }],
    customers: [{ customer_id: "100", cluster: 0, invoice_count: 2, line_count: 3, signed_quantity: -1, monetary_value: "42.50" }],
    note: "Source customer clustering.",
  });
  assert.equal(normalized.rows[0].id, "100");
  assert.equal(normalized.rows[0].category, "Customer");
  assert.equal(normalized.rows[0].cluster, 0);
});

test("basket rules cannot display missing or invalid product labels", () => {
  const rules = [
    { antecedents: ["valid"], consequents: ["punctuation"], support: 1, confidence: 1, lift: 1 },
    { antecedents: ["valid"], consequents: ["missing"], support: 1, confidence: 1, lift: 1 },
  ];
  assert.deepEqual(
    basketPage.filterRulesWithValidProductNames(rules, {
      valid: "Tea & cup!", punctuation: "Product #2!", missing: "??",
    }),
    [rules[0]],
  );
});

test("identical concurrent API requests share one network call without caching completed responses", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    await new Promise((resolve) => setTimeout(resolve, 5));
    return new Response(JSON.stringify({ predictions: [], source_warnings: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const [first, second] = await Promise.all([api.realRetail.demand(), api.realRetail.demand()]);
    assert.equal(calls, 1);
    assert.deepEqual(first, second);
    await api.realRetail.demand();
    assert.equal(calls, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
