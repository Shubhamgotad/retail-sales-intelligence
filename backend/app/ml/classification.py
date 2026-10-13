import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score


def classify_demand(db: Session):
    rows = db.execute(text("""
        SELECT f.product_id, p.product_name,
               EXTRACT(YEAR FROM d.full_date)::int AS year,
               EXTRACT(MONTH FROM d.full_date)::int AS month,
               SUM(f.quantity) AS units_sold,
               SUM(f.total_amount) AS revenue
        FROM fact_sales f
        JOIN dim_date d ON d.date_id = f.date_id
        JOIN dim_product p ON p.product_id = f.product_id
        GROUP BY f.product_id, p.product_name,
                 EXTRACT(YEAR FROM d.full_date),
                 EXTRACT(MONTH FROM d.full_date)
        ORDER BY f.product_id, year, month
    """)).mappings().all()

    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return {"predictions": [], "message": "No sales history found."}

    df["period"] = pd.to_datetime(
        df["year"].astype(str) + "-" + df["month"].astype(str) + "-01"
    )
    df = df.sort_values(["product_id", "period"]).copy()
    grouped = df.groupby("product_id", group_keys=False)

    df["previous_units"] = grouped["units_sold"].shift(1)
    df["previous_revenue"] = grouped["revenue"].shift(1)
    df["previous_3m_avg"] = grouped["units_sold"].transform(
        lambda s: s.shift(1).rolling(3, min_periods=1).mean()
    )
    df["target_units"] = grouped["units_sold"].shift(-1)
    df["target_period"] = grouped["period"].shift(-1)

    features = ["previous_units", "previous_revenue", "previous_3m_avg", "month"]
    train = df.dropna(subset=features + ["target_units"]).copy()

    if len(train) < 6:
        return {
            "predictions": [],
            "message": "Not enough consecutive monthly sales history to train."
        }

    q1, q2 = train["target_units"].quantile([1 / 3, 2 / 3]).tolist()

    def label(value):
        if value <= q1:
            return "low"
        if value <= q2:
            return "medium"
        return "high"

    train["demand_class"] = train["target_units"].map(label)
    model = RandomForestClassifier(
        n_estimators=150, random_state=42, class_weight="balanced"
    )
    model.fit(train[features], train["demand_class"])
    train_accuracy = accuracy_score(
        train["demand_class"], model.predict(train[features])
    )

    latest = df.dropna(subset=features).sort_values("period").groupby(
        "product_id", as_index=False
    ).tail(1).copy()

    if latest.empty:
        return {"predictions": [], "message": "No usable recent history."}

    latest["predicted_demand"] = model.predict(latest[features])
    predictions = []
    for row in latest.sort_values("product_id").to_dict(orient="records"):
        predictions.append({
            "product_id": int(row["product_id"]),
            "product_name": row["product_name"],
            "last_observed_month": row["period"].strftime("%Y-%m"),
            "last_month_units": float(row["units_sold"]),
            "predicted_demand": row["predicted_demand"],
        })

    return {
        "algorithm": "Random Forest",
        "target": "next-month demand class",
        "training_rows": int(len(train)),
        "training_accuracy": round(float(train_accuracy), 4),
        "class_thresholds_units": {
            "low_max": round(float(q1), 2),
            "medium_max": round(float(q2), 2),
            "high": "above medium_max",
        },
        "predictions": predictions,
        "note": "Training accuracy is measured on training data, not a held-out test set."
    }