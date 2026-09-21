"""CU28 - Exportador XLSX con openpyxl (sin pandas).

Una hoja ``Resumen`` con la cabecera institucional + primer bloque, y una hoja
por cada seccion restante. Los valores monetarios se escriben como numeros
(con formato ``#,##0.00``) para que Excel pueda sumarlos.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

TITULO_HOJA_POR_DEFECTO = "Resumen"
CARACTERES_INVALIDOS = ('\\', '/', '*', '?', ':', '[', ']')


def _nombre_hoja(texto: str, usados: set[str]) -> str:
    limpio = texto
    for caracter in CARACTERES_INVALIDOS:
        limpio = limpio.replace(caracter, "-")
    limpio = limpio[:28].strip() or TITULO_HOJA_POR_DEFECTO
    candidato = limpio
    contador = 2
    while candidato.lower() in usados:
        sufijo = f"-{contador}"
        candidato = f"{limpio[: 28 - len(sufijo)]}{sufijo}"
        contador += 1
    usados.add(candidato.lower())
    return candidato


def _celda(valor):
    if valor is None:
        return None
    if isinstance(valor, bool):
        return "SI" if valor else "NO"
    if isinstance(valor, (int, float, Decimal)):
        return valor
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y %H:%M")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    texto = str(valor)
    if texto.startswith("Bs "):
        try:
            return float(
                Decimal(texto[3:].replace(".", "").replace(",", "."))
            )
        except (InvalidOperation, ValueError):
            return texto
    return texto


def _es_importe(valor) -> bool:
    return isinstance(valor, (Decimal, float)) or (
        isinstance(valor, str) and valor.startswith("Bs ")
    )


def _escribir_seccion(
    hoja,
    fila_inicial: int,
    seccion,
    *,
    titulo: str | None = None,
    meta: list[str] | None = None,
) -> int:
    fila = fila_inicial
    if titulo is not None:
        celda = hoja.cell(row=fila, column=1, value="VANTER MEN")
        celda.font = Font(size=14, bold=True)
        fila += 1
        celda = hoja.cell(row=fila, column=1, value=titulo)
        celda.font = Font(size=12, bold=True)
        fila += 1
        for linea in meta or []:
            hoja.cell(row=fila, column=1, value=linea)
            fila += 1
        fila += 1

    celda = hoja.cell(row=fila, column=1, value=seccion.titulo)
    celda.font = Font(bold=True)
    fila += 1
    for columna, encabezado in enumerate(seccion.encabezados, start=1):
        celda = hoja.cell(row=fila, column=columna, value=encabezado)
        celda.font = Font(bold=True)
    fila += 1
    for datos in seccion.filas:
        for columna, valor in enumerate(datos, start=1):
            celda = hoja.cell(
                row=fila, column=columna, value=_celda(valor)
            )
            if _es_importe(valor):
                celda.number_format = "#,##0.00"
                celda.alignment = Alignment(horizontal="right")
        fila += 1
    if not seccion.filas:
        hoja.cell(row=fila, column=1, value="Sin datos")
        fila += 1
    return fila + 1


def _ajustar_ancho(hoja) -> None:
    for columna in range(1, hoja.max_column + 1):
        letra = get_column_letter(columna)
        largo = 0
        for celda in hoja[letra]:
            if celda.value is not None:
                largo = max(largo, len(str(celda.value)))
        hoja.column_dimensions[letra].width = min(max(largo + 2, 10), 45)


def exportar_xlsx(secciones, *, titulo: str, meta: list[str]) -> bytes:
    libro = Workbook()
    hoja_inicial = libro.active
    hoja_inicial.title = TITULO_HOJA_POR_DEFECTO
    usados = {TITULO_HOJA_POR_DEFECTO.lower()}

    fila = _escribir_seccion(
        hoja_inicial, 1, secciones[0], titulo=titulo, meta=meta
    )
    for seccion in secciones[1:]:
        fila = _escribir_seccion(hoja_inicial, fila, seccion)

    for seccion in secciones[1:]:
        hoja = libro.create_sheet(_nombre_hoja(seccion.titulo, usados))
        _escribir_seccion(hoja, 1, seccion)

    for hoja in libro.worksheets:
        _ajustar_ancho(hoja)

    buffer = BytesIO()
    libro.save(buffer)
    return buffer.getvalue()
