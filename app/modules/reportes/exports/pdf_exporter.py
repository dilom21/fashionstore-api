"""CU28 - Exportador PDF con ReportLab (sin WeasyPrint ni graficos externos).

- Encabezado VANTER MEN, titulo, periodo, alcance y fecha de generacion.
- Tablas legibles con encabezado repetido por pagina y numeracion al pie.
- Orientacion horizontal automatica cuando alguna seccion es ancha.
"""

from datetime import date, datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)

MAX_CARACTERES_CELDA = 48
COLUMNAS_LANDSCAPE = 7

COLOR_MARCA = colors.HexColor("#1F2A44")
COLOR_CABECERA = colors.HexColor("#E8EAF0")
COLOR_LINEA = colors.HexColor("#B9BEC9")
COLOR_ALTERNA = colors.HexColor("#F7F8FA")


def _texto(valor, limite: int = MAX_CARACTERES_CELDA) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "SI" if valor else "NO"
    if isinstance(valor, (datetime, date)):
        return valor.strftime("%d/%m/%Y")
    texto = " ".join(str(valor).split())
    if len(texto) <= limite:
        return texto
    return texto[: limite - 3] + "..."


def _anchos(
    encabezados: list[str], filas: list[list[object]], disponible: float
) -> list[float]:
    largos = []
    for indice, encabezado in enumerate(encabezados):
        largo = len(str(encabezado))
        for fila in filas:
            if indice < len(fila):
                largo = max(largo, len(_texto(fila[indice])))
        largos.append(max(largo, 6))
    total = sum(largos) or 1
    return [max(disponible * largo / total, 1.1 * cm) for largo in largos]


def _numerar(canvas, documento) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(COLOR_MARCA)
    canvas.drawString(
        1.5 * cm,
        0.9 * cm,
        "VANTER MEN - Reporte de solo lectura generado por el sistema",
    )
    canvas.drawRightString(
        documento.pagesize[0] - 1.5 * cm,
        0.9 * cm,
        f"Pagina {documento.page}",
    )
    canvas.restoreState()


def exportar_pdf(secciones, *, titulo: str, meta: list[str]) -> bytes:
    columnas = max((len(s.encabezados) for s in secciones), default=1)
    tamano = landscape(A4) if columnas >= COLUMNAS_LANDSCAPE else A4
    disponible = tamano[0] - 3 * cm

    estilo_marca = ParagraphStyle(
        "marca",
        fontName="Helvetica-Bold",
        fontSize=16,
        textColor=COLOR_MARCA,
        spaceAfter=2,
    )
    estilo_titulo = ParagraphStyle(
        "titulo",
        fontName="Helvetica-Bold",
        fontSize=12,
        textColor=colors.black,
        spaceAfter=6,
    )
    estilo_meta = ParagraphStyle(
        "meta",
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#3B4252"),
    )
    estilo_seccion = ParagraphStyle(
        "seccion",
        fontName="Helvetica-Bold",
        fontSize=10.5,
        textColor=COLOR_MARCA,
        spaceBefore=10,
        spaceAfter=4,
    )

    elementos = [
        Paragraph("VANTER MEN", estilo_marca),
        Paragraph(titulo, estilo_titulo),
    ]
    elementos.extend(Paragraph(linea, estilo_meta) for linea in meta)

    for seccion in secciones:
        datos = [[str(h) for h in seccion.encabezados]]
        datos.extend(
            [_texto(valor) for valor in fila] for fila in seccion.filas
        )
        if not seccion.filas:
            datos.append(
                ["Sin datos"] + [""] * (len(seccion.encabezados) - 1)
            )
        tabla = Table(
            datos,
            colWidths=_anchos(seccion.encabezados, seccion.filas, disponible),
            repeatRows=1,
        )
        tabla.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), COLOR_CABECERA),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.2),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
                    ("GRID", (0, 0), (-1, -1), 0.25, COLOR_LINEA),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, COLOR_ALTERNA],
                    ),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        elementos.append(
            KeepTogether(
                [Paragraph(seccion.titulo, estilo_seccion), tabla]
            )
        )

    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer,
        pagesize=tamano,
        title=titulo,
        author="VANTER MEN",
        subject=titulo,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.6 * cm,
    )
    documento.build(elementos, onFirstPage=_numerar, onLaterPages=_numerar)
    return buffer.getvalue()
