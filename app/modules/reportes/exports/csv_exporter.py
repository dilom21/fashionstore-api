"""CU28 - Exportador CSV (libreria estandar, sin pandas).

- UTF-8 con BOM (``utf-8-sig``) para que Excel en Windows reconozca acentos,
  ñ y Bs.
- Estructura legible en Excel: cabecera (empresa, titulo, periodo), y luego
  cada seccion separada por una linea vacia con su titulo y encabezados.
- Los montos se normalizan a numero plano (1234.56) para que sean analizables;
  el JSON conserva Decimal y el PDF/XLSX pueden mostrarlos formateados.
"""

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO


def _texto(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "SI" if valor else "NO"
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y %H:%M")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    texto = str(valor)
    return _numero_plano(texto)


def _numero_plano(texto: str) -> str:
    """Convierte una presentacion monetaria (Bs 1.234,56) a decimal plano."""
    if not texto.startswith("Bs "):
        return texto
    numero = texto[3:].replace(".", "").replace(",", ".")
    try:
        return str(Decimal(numero).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        return texto


def exportar_csv(secciones, *, titulo: str, meta: list[str]) -> bytes:
    buffer = StringIO(newline="")
    escritor = csv.writer(buffer, delimiter=",", quoting=csv.QUOTE_MINIMAL)

    escritor.writerow(["VANTER MEN"])
    escritor.writerow([titulo])
    for linea in meta:
        concepto, _, valor = linea.partition(": ")
        escritor.writerow([concepto, valor])

    for seccion in secciones:
        escritor.writerow([])
        escritor.writerow([seccion.titulo])
        escritor.writerow(seccion.encabezados)
        for fila in seccion.filas:
            escritor.writerow([_texto(valor) for valor in fila])

    return buffer.getvalue().encode("utf-8-sig")
