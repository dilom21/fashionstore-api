"""Modelo local y verificable de demanda de CU24.

El artefacto es entrenado offline con series sintéticas etiquetadas. La API
solo usa este modelo general con features comerciales agregadas y tenant-scoped.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from joblib import load

ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "artifacts"
ARTIFACT_PATH = ARTIFACT_DIR / "cu24_demand_v1.joblib"
METADATA_PATH = ARTIFACT_DIR / "cu24_demand_v1.metadata.json"
FEATURE_NAMES = (
    "lag_1", "lag_7", "lag_14", "rolling_7", "rolling_28",
    "dow_sin", "dow_cos", "month_sin", "month_cos", "horizon",
)
HORIZONS = (7, 30)


class PredictionArtifactError(Exception):
    pass


def _features(history: list[float], cutoff: date, horizon: int) -> np.ndarray:
    if len(history) < 28:
        raise ValueError("Se requieren al menos 28 días de histórico")
    values = np.asarray(history[-28:], dtype=float)
    angle_day = 2 * np.pi * cutoff.weekday() / 7
    angle_month = 2 * np.pi * cutoff.month / 12
    return np.asarray([[
        values[-1], values[-7], values[-14], values[-7:].mean(), values.mean(),
        np.sin(angle_day), np.cos(angle_day), np.sin(angle_month),
        np.cos(angle_month), horizon,
    ]], dtype=float)


def load_verified_artifact():
    if not ARTIFACT_PATH.is_file() or not METADATA_PATH.is_file():
        raise PredictionArtifactError("Artefacto CU24 ausente")
    try:
        metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        if (metadata.get("version") != "cu24-demand-v1"
                or metadata.get("features") != list(FEATURE_NAMES)
                or metadata.get("training_provenance") != "SINTETICO"):
            raise PredictionArtifactError("Metadatos de artefacto no autorizados")
        return load(ARTIFACT_PATH), metadata
    except PredictionArtifactError:
        raise
    except Exception as exc:
        raise PredictionArtifactError("Artefacto CU24 inválido") from exc


def predict(history: list[int], cutoff: date, horizon: int) -> float:
    if horizon not in HORIZONS:
        raise ValueError("Horizonte no soportado")
    model, _ = load_verified_artifact()
    value = float(model.predict(_features(history, cutoff, horizon))[0])
    if not np.isfinite(value):
        raise PredictionArtifactError("Predicción no finita")
    return max(0.0, value)


def build_feature_row(history: list[float], cutoff: date, horizon: int) -> list[float]:
    return _features(history, cutoff, horizon)[0].tolist()
