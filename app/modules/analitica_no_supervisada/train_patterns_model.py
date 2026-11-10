"""Entrenamiento reproducible offline de CU25 con datos inequívocamente sintéticos."""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SEED = 25025
FEATURE_NAMES = ["unidades_vendidas", "ingresos", "dias_con_ventas", "stock_disponible"]
ARTIFACT_DIR = Path(__file__).with_name("artifacts")


def synthetic_dataset(seed: int = SEED):
    rng = np.random.default_rng(seed)
    centers = np.asarray([[8, 240, 8, 5], [45, 1600, 24, 18], [16, 500, 14, 90]], dtype=float)
    rows = np.vstack([center + rng.normal(0, [2, 80, 2, 4], size=(30, 4)) for center in centers])
    return np.clip(rows, 0, None)


def train() -> dict:
    x = synthetic_dataset()
    model = Pipeline([("scale", StandardScaler()), ("cluster", KMeans(n_clusters=3, n_init=20, random_state=SEED))])
    labels = model.fit_predict(x)
    metadata = {
        "version": "cu25-patterns-v1", "algorithm": "StandardScaler+KMeans", "seed": SEED,
        "features": FEATURE_NAMES, "training_provenance": "SINTETICO",
        "dataset_rows": int(len(x)), "n_clusters": 3,
        "cluster_sizes": [int((labels == value).sum()) for value in range(3)],
        "silhouette": round(float(silhouette_score(model.named_steps["scale"].transform(x), labels)), 6),
        "inertia": round(float(model.named_steps["cluster"].inertia_), 6),
        "warning": "La evaluación sintética no valida patrones comerciales reales.",
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, ARTIFACT_DIR / "cu25_patterns_v1.joblib", compress=3)
    (ARTIFACT_DIR / "cu25_patterns_v1.metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


if __name__ == "__main__":
    print(json.dumps(train(), indent=2))
