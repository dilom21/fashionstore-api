"""Reglas puras de CU15, independientes de PostgreSQL.

Este módulo concentra reglas que deben ser idénticas en API, pruebas y nota:
dinero decimal, restock únicamente APTO e idempotencia de operación.
"""
from decimal import Decimal, ROUND_HALF_UP

CENTAVOS = Decimal("0.01")
PORCENTAJE_REEMBOLSO = Decimal("0.50")


def importe_reembolso_sin_cambio(lineas: list[tuple[int, Decimal]]) -> dict[str, Decimal]:
    elegible = sum((Decimal(cantidad) * Decimal(precio) for cantidad, precio in lineas), Decimal("0"))
    elegible = elegible.quantize(CENTAVOS, rounding=ROUND_HALF_UP)
    reembolso = (elegible * PORCENTAJE_REEMBOLSO).quantize(CENTAVOS, rounding=ROUND_HALF_UP)
    return {"importe_elegible": elegible, "importe_reembolso": reembolso, "importe_retenido": elegible - reembolso}


def puede_restock(condicion: str, decision: bool) -> bool:
    return condicion.strip().upper() == "APTO" and decision is True


def diferencia_cambio(precio_nuevo: Decimal, precio_original: Decimal, cantidad: int) -> Decimal:
    """Diferencia que se puede cobrar; un cambio más barato no genera pago."""
    return max(
        Decimal("0"),
        (Decimal(precio_nuevo) * cantidad - Decimal(precio_original) * cantidad).quantize(
            CENTAVOS, rounding=ROUND_HALF_UP
        ),
    )


def fecha_limite_reclamo(fecha_compra):
    """Límite inclusivo de 168 horas, calculado en servidor."""
    from datetime import timedelta
    return fecha_compra + timedelta(days=7)


def elegible_reclamo(fecha_compra, ahora) -> bool:
    return fecha_compra <= ahora <= fecha_limite_reclamo(fecha_compra)


TRANSICIONES = {
    "SOLICITADA": {"APROBADA", "RECHAZADA"},
    "APROBADA": {"INSPECCIONADA", "RECHAZADA"},
    "INSPECCIONADA": {"COMPLETADA", "RECHAZADA"},
    "COMPLETADA": set(),
    "RECHAZADA": set(),
}


def validar_transicion(actual: str, siguiente: str) -> None:
    if siguiente not in TRANSICIONES.get(actual.upper(), set()):
        raise ValueError("Transición de CU15 no permitida")
