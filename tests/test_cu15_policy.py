from decimal import Decimal
from datetime import datetime, timedelta, timezone
import pytest
from app.modules.devoluciones.services.cu15_policy import (
    diferencia_cambio, elegible_reclamo, importe_reembolso_sin_cambio, puede_restock, validar_transicion,
)

def test_reembolso_es_50_por_ciento_del_importe_devuelto():
    assert importe_reembolso_sin_cambio([(1, Decimal("200")), (2, Decimal("80"))]) == {
        "importe_elegible": Decimal("360.00"), "importe_reembolso": Decimal("180.00"), "importe_retenido": Decimal("180.00")}

def test_cambio_m_a_l_calcula_diferencia_por_unidades():
    assert diferencia_cambio(Decimal("230"), Decimal("200"), 1) == Decimal("30.00")
    assert diferencia_cambio(Decimal("180"), Decimal("200"), 1) == Decimal("0.00")


def test_plazo_inclusivo_de_siete_dias_y_rechaza_dia_ocho():
    compra = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert elegible_reclamo(compra, compra + timedelta(days=6))
    assert elegible_reclamo(compra, compra + timedelta(days=7))
    assert not elegible_reclamo(compra, compra + timedelta(days=8))

def test_restock_solo_apto():
    assert puede_restock("APTO", True)
    assert not puede_restock("NO_APTO", True)
    assert not puede_restock("APTO", False)

def test_transiciones_terminales_no_se_repiten():
    validar_transicion("SOLICITADA", "APROBADA")
    with pytest.raises(ValueError):
        validar_transicion("COMPLETADA", "COMPLETADA")
