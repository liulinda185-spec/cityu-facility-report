"""Train the classifier on seed_training_data.csv.
Usage: python ml/train.py
Output: ml/model.pkl"""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd, joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report

BASE = Path(__file__).resolve().parent.parent
df = pd.read_csv(BASE / "seed_training_data.csv")

# Fold live officer corrections into training. On Render DATABASE_URL is set,
# so every deploy retrains on everything the officer has re-labelled; locally
# (no DATABASE_URL) this is a no-op and the seed CSV is used alone.
try:
    if os.getenv("DATABASE_URL"):
        from app.db import get_conn
        conn = get_conn()
        rows = conn.execute(
            "SELECT text, label FROM training_examples WHERE length(trim(text)) >= 3"
        ).fetchall()
        conn.close()
        if rows:
            corr = pd.DataFrame([{"text": r["text"], "category": r["label"], "source": "officer_correction"} for r in rows])
            df = pd.concat([df, corr], ignore_index=True)
            print(f"folded {len(corr)} officer corrections from live DB")
except Exception as e:  # noqa: BLE001
    print(f"[train] live corrections skipped: {e}")

# Down-weight synthetic rows: duplicate seed rows 3x
seed = df[df.source == "seed"]
synth = df[df.source == "synthetic"] if "synthetic" in df.source.values else pd.DataFrame()
df = pd.concat([seed, seed, seed, synth]) if len(synth) else pd.concat([seed, seed, seed])

X_train, X_test, y_train, y_test = train_test_split(
    df.text, df.category, test_size=0.2, random_state=42, stratify=df.category)

pipe = Pipeline([
    ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(2,5), min_df=2)),
    ("clf", LogisticRegression(class_weight="balanced", max_iter=1000))
])
pipe.fit(X_train, y_train)
pred = pipe.predict(X_test)
print(classification_report(y_test, pred))

joblib.dump(pipe, BASE / "ml" / "model.pkl")
print(f"Saved model to {BASE / 'ml' / 'model.pkl'}")
