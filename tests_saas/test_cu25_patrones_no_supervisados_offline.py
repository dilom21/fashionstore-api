from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

from app.core.tenant_context import TenantContext
from app.modules.analitica_no_supervisada.schemas import PatronesRequest
from app.modules.analitica_no_supervisada.train_patterns_model import synthetic_dataset, train
from app.modules.reportes.api.tenant_router import patrones_no_supervisados


def _tenant(**changes):
    values = dict(usuario_id=10, empresa_id=7, empresa_slug="empresa-a", empresa_nombre="A",
                  rol_id=2, rol_nombre="ADMINISTRADOR", contexto="personal",
                  plan_codigo="PROFESIONAL", permite_reportes_ia=True)
    values.update(changes)
    return TenantContext(**values)


def test_dataset_sintetico_y_entrenamiento_reproducibles(tmp_path, monkeypatch):
    first = synthetic_dataset()
    second = synthetic_dataset()
    assert np.array_equal(first, second)
    monkeypatch.setattr("app.modules.analitica_no_supervisada.train_patterns_model.ARTIFACT_DIR", tmp_path)
    metadata = train()
    assert metadata["training_provenance"] == "SINTETICO"
    assert metadata["seed"] == 25025
    assert metadata["n_clusters"] == 3
    assert 0 < metadata["silhouette"] <= 1


def test_dto_estricto_no_acepta_empresa_id():
    with pytest.raises(ValueError):
        PatronesRequest(empresa_id=99)


def test_plan_bloquea_antes_de_consulta():
    with pytest.raises(HTTPException) as error:
        patrones_no_supervisados(
            PatronesRequest(),
            tenant=_tenant(permite_reportes_ia=False),
            db=None,
        )
    assert error.value.status_code == 403


def test_sucursal_ajena_se_rechaza_antes_de_agregar(monkeypatch):
    monkeypatch.setattr(
        "app.modules.reportes.api.tenant_router._actor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail="No autorizado")
        ),
    )
    with pytest.raises(HTTPException) as error:
        patrones_no_supervisados(
            PatronesRequest(sucursal_id=99), tenant=_tenant(), db=SimpleNamespace()
        )
    assert error.value.status_code == 403
