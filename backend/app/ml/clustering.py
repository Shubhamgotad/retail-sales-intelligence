import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


def cluster_products(db: Session, n_clusters: int = 3):
    rows = db.execute(text("""
        SELECT p.product_id, p.product_name, p.category,
               COALESCE(SUM(f.quantity), 0) AS units_sold,
               COALESCE(SUM(f.total_amount), 0) AS revenue,
               COALESCE(SUM(f.profit), 0) AS profit
        FROM dim_product p
        LEFT JOIN fact_sales f ON f.product_id = p.product_id
        GROUP BY p.product_id, p.product_name, p.category
        ORDER BY p.product_id
    """)).mappings().all()

    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return {"clusters": [], "products": [], "message": "No products found."}

    features = ["units_sold", "revenue", "profit"]
    X = df[features].astype(float)
    k = min(max(1, n_clusters), len(df))

    if k == 1:
        labels = [0] * len(df)
    else:
        scaled = StandardScaler().fit_transform(X)
        labels = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(scaled)

    df["cluster"] = labels
    products = df.to_dict(orient="records")
    clusters = []
    for label, group in df.groupby("cluster"):
        clusters.append({
            "cluster": int(label),
            "product_count": int(len(group)),
            "total_revenue": round(float(group["revenue"].sum()), 2),
            "total_units_sold": int(group["units_sold"].sum()),
            "products": group["product_name"].tolist(),
        })

    return {
        "algorithm": "K-Means",
        "features": features,
        "cluster_count": k,
        "clusters": clusters,
        "products": products,
    }