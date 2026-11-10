from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import joblib
import numpy as np
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.tenant_context import TenantContext
from .repositories import obtener_productos_features, obtener_sucursal_autorizada
from .schemas import ClusterResumen, PatronesRequest, PatronesResponse, ProductoCluster

FEATURE_NAMES = ["unidades_vendidas", "ingresos", "dias_con_ventas", "stock_disponible"]
ARTIFACT_DIR = Path(__file__).with_name("artifacts")
ARTIFACT_PATH = ARTIFACT_DIR / "cu25_patterns_v1.joblib"
METADATA_PATH = ARTIFACT_DIR / "cu25_patterns_v1.metadata.json"


class PatternArtifactError(RuntimeError):
    pass


def load_verified_artifact():
    try:
        metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        if metadata.get("features") != FEATURE_NAMES or metadata.get("version") != "cu25-patterns-v1":
            raise PatternArtifactError("schema de características incompatible")
        model = joblib.load(ARTIFACT_PATH)
        if not hasattr(model, "predict") or not hasattr(model, "named_steps"):
            raise PatternArtifactError("artefacto inválido")
        return model, metadata
    except Exception as exc:
        raise PatternArtifactError("artefacto ausente o ilegible") from exc


def _etiquetas(features: np.ndarray, labels: np.ndarray) -> dict[int, str]:
    """Descripción heurística, no causal, basada únicamente en promedios observados."""
    result: dict[int, str] = {}
    for cluster in sorted(set(int(value) for value in labels)):
        values = features[labels == cluster]
        units, revenue, _, stock = values.mean(axis=0)
        if units >= np.percentile(features[:, 0], 67):
            result[cluster] = "Alta rotación"
        elif stock >= np.percentile(features[:, 3], 67):
            result[cluster] = "Stock elevado"
        elif revenue <= np.percentile(features[:, 1], 33):
            result[cluster] = "Baja actividad"
        else:
            result[cluster] = "Rotación intermedia"
    return result


def analizar(db: Session, tenant: TenantContext, datos: PatronesRequest) -> PatronesResponse:
    fecha_hasta = date.today()
    fecha_desde = fecha_hasta - timedelta(days=datos.periodo_dias - 1)
    sucursal_nombre = None
    if datos.sucursal_id is not None:
        sucursal = obtener_sucursal_autorizada(
            db, empresa_id=tenant.empresa_id, sucursal_id=datos.sucursal_id
        )
        if sucursal is None:
            raise HTTPException(status_code=404, detail="Sucursal no encontrada")
        if tenant.rol_nombre == "ENCARGADO_SUCURSAL":
            # El router ya valida el actor; esta defensa mantiene el servicio seguro
            # si se reutiliza fuera del adaptador HTTP.
            from app.modules.autenticacion_seguridad.models.models import Empleado
            empleado = db.query(Empleado).filter(
                Empleado.usuario_id == tenant.usuario_id,
                Empleado.empresa_id == tenant.empresa_id,
                Empleado.estado.is_(True),
            ).first()
            if empleado is None or empleado.sucursal_id != datos.sucursal_id:
                raise HTTPException(status_code=403, detail="No autorizado para la sucursal solicitada")
        sucursal_nombre = sucursal.nombre

    filas = obtener_productos_features(
        db,
        empresa_id=tenant.empresa_id,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        sucursal_id=datos.sucursal_id,
        limite=datos.max_productos,
    )
    if len(filas) < 3:
        raise HTTPException(status_code=422, detail={"estado": "SIN_DATOS", "mensaje": "Se requieren al menos 3 productos observables"})
    model, metadata = load_verified_artifact()
    features = np.asarray([[float(row[2]), float(row[3]), float(row[4]), float(row[5])] for row in filas], dtype=float)
    if not np.isfinite(features).all() or (features < 0).any():
        raise HTTPException(status_code=503, detail="Datos agregados inválidos para clustering")
    labels = model.predict(features)
    labels = np.asarray(labels, dtype=int)
    nombres = _etiquetas(features, labels)
    productos = [
        ProductoCluster(
            producto_id=int(row[0]), producto_nombre=str(row[1]), cluster_id=int(label),
            etiqueta=nombres[int(label)], unidades_vendidas=round(float(row[2]), 2),
            ingresos=round(float(row[3]), 2), dias_con_ventas=int(row[4]),
            stock_disponible=round(float(row[5]), 2),
        ) for row, label in zip(filas, labels)
    ]
    resumen = []
    for cluster in sorted(nombres):
        values = features[labels == cluster]
        resumen.append(ClusterResumen(
            cluster_id=cluster, etiqueta=nombres[cluster], productos=len(values),
            unidades_promedio=round(float(values[:, 0].mean()), 2),
            ingresos_promedio=round(float(values[:, 1].mean()), 2),
            stock_promedio=round(float(values[:, 3].mean()), 2),
        ))
    return PatronesResponse(
        tipo="PATRONES_NO_SUPERVISADOS", analisis_id=str(uuid4()), generado_en=datetime.now(timezone.utc),
        periodo_dias=datos.periodo_dias, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
        sucursal_id=datos.sucursal_id, sucursal_nombre=sucursal_nombre,
        modelo=metadata["algorithm"], version_modelo=metadata["version"],
        procedencia_entrenamiento="SINTETICO", procedencia_datos="REALES_TENANT",
        features=FEATURE_NAMES, observaciones=len(productos),
        metricas_evaluacion={"silhouette": metadata.get("silhouette"), "inertia": metadata.get("inertia"), "clusters_entrenamiento": metadata.get("n_clusters")},
        clusters=resumen, productos=productos,
        advertencias=["Las etiquetas son descripciones heurísticas de agregados observados; no implican causalidad ni predicción comercial."],
        estado="OK",
    )
