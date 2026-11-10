"""Entrenamiento reproducible offline de CU24.

Uso: ``python -m app.modules.predicciones.services.train_demand_model``.
No importa la configuración de FastAPI ni abre conexiones de datos.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import joblib
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .demand_model import ARTIFACT_DIR, FEATURE_NAMES, HORIZONS

SEED = 24024


def synthetic_series() -> list[tuple[date, float]]:
    rng = np.random.default_rng(SEED)
    start, days = date(2023, 1, 1), 1095
    rows = []
    for series in range(8):
        base = 4.0 + series * 0.8
        for offset in range(days):
            day = start + timedelta(days=offset)
            seasonal = 1.5 * np.sin(2 * np.pi * day.timetuple().tm_yday / 365.25)
            weekly = 0.8 if day.weekday() in (4, 5) else 0
            trend = offset / days
            rows.append((day, float(max(0, rng.poisson(base + seasonal + weekly + trend)))))
    return rows


def make_dataset():
    rows = synthetic_series()
    by_series = [rows[i * 1095:(i + 1) * 1095] for i in range(8)]
    records = []
    for series in by_series:
        values = [value for _, value in series]
        for index in range(28, len(series) - 30):
            cutoff = series[index][0]
            for horizon in HORIZONS:
                history = np.asarray(values[index - 28:index], dtype=float)
                angle_day = 2 * np.pi * cutoff.weekday() / 7
                angle_month = 2 * np.pi * cutoff.month / 12
                features = [history[-1], history[-7], history[-14], history[-7:].mean(),
                            history.mean(), np.sin(angle_day), np.cos(angle_day),
                            np.sin(angle_month), np.cos(angle_month), horizon]
                records.append((cutoff, features, sum(values[index:index + horizon])))
    records.sort(key=lambda row: row[0])
    return np.asarray([row[1] for row in records]), np.asarray([row[2] for row in records])


def _metrics(actual, predicted):
    return {"MAE": round(float(mean_absolute_error(actual, predicted)), 6),
            "RMSE": round(float(np.sqrt(mean_squared_error(actual, predicted))), 6),
            "R2": round(float(r2_score(actual, predicted)), 6)}


def train() -> dict:
    x, y = make_dataset()
    n = len(x)
    train_end, valid_end = int(n * .70), int(n * .85)
    x_train, y_train = x[:train_end], y[:train_end]
    x_valid, y_valid = x[train_end:valid_end], y[train_end:valid_end]
    x_test, y_test = x[valid_end:], y[valid_end:]
    model = Pipeline([("scale", StandardScaler()), ("regressor", Ridge(alpha=1.0))])
    model.fit(x_train, y_train)
    predicted = model.predict(x_test)
    baseline = x_test[:, 3] * x_test[:, 9]
    metadata = {
        "version": "cu24-demand-v1", "algorithm": "StandardScaler+Ridge",
        "seed": SEED, "target": "unidades_vendidas_en_horizonte",
        "horizons": list(HORIZONS), "features": list(FEATURE_NAMES),
        "training_provenance": "SINTETICO", "synthetic_series": 8,
        "dataset_rows": int(n), "split": {"train": len(x_train), "validation": len(x_valid), "test": len(x_test)},
        "metrics_validation": _metrics(y_valid, model.predict(x_valid)),
        "metrics_test": _metrics(y_test, predicted),
        "baseline_metrics_test": _metrics(y_test, baseline),
        "non_negative_clamp": True,
        "warning": "Estas métricas no demuestran precisión sobre datos comerciales reales.",
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, ARTIFACT_DIR / "cu24_demand_v1.joblib", compress=3)
    (ARTIFACT_DIR / "cu24_demand_v1.metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return metadata


if __name__ == "__main__":
    print(json.dumps(train(), indent=2, ensure_ascii=False))
