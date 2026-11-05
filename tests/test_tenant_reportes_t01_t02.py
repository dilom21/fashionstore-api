"""Focused contract tests for tenant dynamic reports and local analytics."""
from app.modules.reportes.services.tenant_ml_service import TenantMLService
from app.modules.reportes.services.tenant_reportes_service import (
    ReporteTenantError, TenantReportesService,
)


def test_local_ml_is_reproducible_and_has_real_metrics():
    first = TenantMLService.analizar(7)
    assert first == TenantMLService.analizar(7)
    assert 0 <= first["supervisado"]["accuracy"] <= 1
    assert -1 <= first["no_supervisado"]["silhouette"] <= 1


def test_dynamic_report_is_aggregate_only(monkeypatch):
    monkeypatch.setattr(TenantReportesService, "resumen", lambda *args, **kwargs: {
        "periodo": {"fecha_desde": None, "fecha_hasta": None},
        "ventas_completadas": 2, "ventas_pendientes": 1, "total_vendido": 10,
        "ticket_promedio": 5, "reservas_total": 0, "reservas_pendientes": 0,
        "productos_activos": 3, "stock_actual": 4, "stock_reservado": 1,
        "stock_disponible": 3,
    })
    report = TenantReportesService.dinamico(object(), empresa_id=1, tipo="ventas",
                                             pregunta="ventas del período", fecha_desde=None,
                                             fecha_hasta=None, sucursal_id=None)
    assert report["tipo"] == "ventas"
    assert all("email" not in row and "telefono" not in row for row in report["filas"])


def test_dynamic_report_rejects_unknown_type():
    try:
        TenantReportesService.dinamico(object(), empresa_id=1, tipo="desconocido",
                                       pregunta=None, fecha_desde=None, fecha_hasta=None,
                                       sucursal_id=None)
    except ReporteTenantError as error:
        assert error.codigo == 422
    else:
        raise AssertionError("unknown report type must be rejected")
