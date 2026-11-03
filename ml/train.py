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
