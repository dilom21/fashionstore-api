from datetime import date
from types import SimpleNamespace

import pytest

from app.modules.predicciones.services import demand_model
from app.modules.predicciones.services.train_demand_model import train
from app.modules.predicciones.schemas import PrediccionRequest
from app.modules.reportes.api.tenant_router import prediccion_demanda
from fastapi import HTTPException


def test_entrenamiento_reproducible_y_metricas(tmp_path, monkeypatch):
    monkeypatch.setattr(demand_model, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(demand_model, "ARTIFACT_PATH", tmp_path / "cu24_demand_v1.joblib")
    monkeypatch.setattr(demand_model, "METADATA_PATH", tmp_path / "cu24_demand_v1.metadata.json")
    first = train()
    second = train()
    assert first["seed"] == second["seed"] == 24024
    assert first["metrics_test"].keys() == {"MAE", "RMSE", "R2"}
    assert first["baseline_metrics_test"].keys() == {"MAE", "RMSE", "R2"}
    assert first["split"]["test"] > 0


def test_artefacto_versionado_real_carga_y_predice():
    model, metadata = demand_model.load_verified_artifact()
    assert metadata["version"] == "cu24-demand-v1"
    assert metadata["algorithm"] == "StandardScaler+Ridge"
    assert metadata["metrics_test"].keys() == {"MAE", "RMSE", "R2"}
    valor = demand_model.predict([5] * 90, date(2026, 11, 10), 7)
    assert valor >= 0
    assert valor == valor  # no NaN


def test_prediccion_finitas_no_negativas(monkeypatch):
    monkeypatch.setattr(demand_model, "load_verified_artifact", lambda: (
        SimpleNamespace(predict=lambda _: [-4.0]), {"version": "v"}))
    assert demand_model.predict([2] * 30, date(2026, 1, 1), 7) == 0.0


def test_esquema_rechaza_empresa_id():
    with pytest.raises(ValueError):
        PrediccionRequest(producto_id=1, empresa_id=99)


def test_plan_bloquea_antes_de_consulta():
    tenant = SimpleNamespace(permite_reportes_ia=False)
    with pytest.raises(HTTPException) as error:
        prediccion_demanda(PrediccionRequest(producto_id=1), tenant=tenant, db=None)
    assert error.value.status_code == 403
