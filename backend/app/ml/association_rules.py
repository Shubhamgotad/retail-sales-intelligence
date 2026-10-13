import pandas as pd
from mlxtend.frequent_patterns import apriori, association_rules
from mlxtend.preprocessing import TransactionEncoder
from sqlalchemy import text
from sqlalchemy.orm import Session


def find_association_rules(
    db: Session, min_support: float = 0.01, min_confidence: float = 0.2
):
    rows = db.execute(text("""
        SELECT f.transaction_id, p.product_name
        FROM fact_sales f
        JOIN dim_product p ON p.product_id = f.product_id
        ORDER BY f.transaction_id, p.product_name
    """)).mappings().all()

    baskets = {}
    for row in rows:
        baskets.setdefault(row["transaction_id"], set()).add(row["product_name"])

    transactions = [sorted(items) for items in baskets.values() if len(items) >= 2]
    if not transactions:
        return {
            "algorithm": "Apriori",
            "transaction_count": len(baskets),
            "multi_item_transaction_count": 0,
            "frequent_itemsets": [],
            "rules": [],
            "message": (
                "No transactions contain multiple distinct products. "
                "Apriori needs multi-item baskets. Update the demo seed data "
                "to assign the same transaction_id to multiple product rows."
            ),
        }

    encoder = TransactionEncoder()
    encoded = encoder.fit(transactions).transform(transactions)
    basket_df = pd.DataFrame(encoded, columns=encoder.columns_)

    itemsets = apriori(
        basket_df, min_support=min_support, use_colnames=True
    )
    frequent = [
        {
            "items": sorted(list(row["itemsets"])),
            "support": round(float(row["support"]), 4),
        }
        for _, row in itemsets.iterrows()
    ]

    if itemsets.empty or not itemsets["itemsets"].map(len).ge(2).any():
        rules_df = pd.DataFrame()
    else:
        rules_df = association_rules(
            itemsets, metric="confidence", min_threshold=min_confidence
        )

    rules = []
    if not rules_df.empty:
        for _, row in rules_df.sort_values(
            ["confidence", "lift"], ascending=False
        ).head(50).iterrows():
            rules.append({
                "antecedents": sorted(list(row["antecedents"])),
                "consequents": sorted(list(row["consequents"])),
                "support": round(float(row["support"]), 4),
                "confidence": round(float(row["confidence"]), 4),
                "lift": round(float(row["lift"]), 4),
            })

    return {
        "algorithm": "Apriori",
        "transaction_count": len(baskets),
        "multi_item_transaction_count": len(transactions),
        "frequent_itemsets": frequent,
        "rules": rules,
    }