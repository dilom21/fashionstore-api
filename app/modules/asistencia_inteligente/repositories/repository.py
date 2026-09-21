"""Acceso a datos de Asistencia Inteligente (solo lectura).

Construye el conjunto de CANDIDATOS REALES a nivel inventario/variante. Solo
incluye producto activo, variante activa, talla/color/sucursal activos y stock
disponible > 0. Nunca modifica inventario ni escribe en la base de datos.

La IA no puede ampliar este conjunto: cualquier id que no provenga de aqui se
descarta en el service.
"""

from dataclasses import dataclass, replace
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import (
    Categoria,
    Color,
    Producto,
    RecursoProducto,
    Talla,
    Temporada,
    VarianteProducto,
)
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal

# Limite defensivo de candidatos enviados al modelo (los filtros estructurados
# se aplican antes, por lo que en la practica el conjunto es mucho menor).
MAX_CANDIDATOS = 120


@dataclass(frozen=True)
class CandidatoInventario:
    """Candidato real (inventario con stock) listo para el modelo/respuesta."""

    producto_id: int
    nombre: str
    descripcion: str | None
    categoria: str
    precio: Decimal
    variante_id: int
    talla: str
    color: str
    color_id: int
    inventario_id: int
    sucursal_id: int
    sucursal: str
    temporada: str
    stock_disponible: int
    imagen: str | None = None


def _columnas():
    return (
        Producto.id,
        Producto.nombre,
        Producto.descripcion,
        Categoria.nombre,
        Producto.precio,
        VarianteProducto.id,
        Talla.nombre,
        Color.nombre,
        Color.id,
        Inventario.id,
        Sucursal.id,
        Sucursal.nombre,
        Temporada.nombre,
        (Inventario.stock_actual - Inventario.stock_reservado).label(
            "stock_disponible"
        ),
    )


def _fila_a_candidato(fila) -> CandidatoInventario:
    return CandidatoInventario(
        producto_id=int(fila[0]),
        nombre=str(fila[1]),
        descripcion=fila[2],
        categoria=str(fila[3]),
        precio=Decimal(fila[4]),
        variante_id=int(fila[5]),
        talla=str(fila[6]),
        color=str(fila[7]),
        color_id=int(fila[8]),
        inventario_id=int(fila[9]),
        sucursal_id=int(fila[10]),
        sucursal=str(fila[11]),
        temporada=str(fila[12]),
        stock_disponible=int(fila[13]),
    )


def _statement_base():
    """SELECT de candidatos con las reglas de disponibilidad publicas.

    Mismas condiciones que CU09/CU15: producto, variante, categoria, talla,
    color y sucursal activos, y stock disponible > 0.
    """
    return (
        select(*_columnas())
        .select_from(Inventario)
        .join(
            VarianteProducto,
            Inventario.variante_producto_id == VarianteProducto.id,
        )
        .join(Producto, VarianteProducto.producto_id == Producto.id)
        .join(Categoria, Producto.categoria_id == Categoria.id)
        .join(Talla, VarianteProducto.talla_id == Talla.id)
        .join(Color, VarianteProducto.color_id == Color.id)
        .join(Sucursal, Inventario.sucursal_id == Sucursal.id)
        .join(Temporada, Inventario.temporada_id == Temporada.id)
        .where(
            Producto.estado.is_(True),
            VarianteProducto.estado.is_(True),
            Categoria.estado.is_(True),
            Talla.estado.is_(True),
            Color.estado.is_(True),
            Sucursal.estado.is_(True),
            (Inventario.stock_actual - Inventario.stock_reservado) > 0,
        )
    )


class AsistenciaRepository:
    """Consultas de solo lectura para Asistencia Inteligente."""

    @staticmethod
    def obtener_candidatos(
        db: Session,
        *,
        talla: str | None = None,
        color: str | None = None,
        presupuesto_max: Decimal | None = None,
        sucursal_id: int | None = None,
        sucursal: str | None = None,
    ) -> list[CandidatoInventario]:
        """Candidatos reales tras aplicar los filtros estructurados.

        ``talla``, ``color``, ``presupuesto_max``, ``sucursal_id`` y
        ``sucursal`` son restricciones DURAS: se aplican en SQL antes de
        enviar cualquier candidato al modelo. Las preferencias blandas no
        entran aqui.
        """
        statement = _statement_base()
        if talla:
            statement = statement.where(
                func.lower(Talla.nombre) == talla.strip().lower()
            )
        if color:
            statement = statement.where(
                func.lower(Color.nombre) == color.strip().lower()
            )
        if presupuesto_max is not None:
            statement = statement.where(Producto.precio <= presupuesto_max)
        if sucursal_id is not None:
            statement = statement.where(Inventario.sucursal_id == sucursal_id)
        if sucursal:
            statement = statement.where(
                func.lower(Sucursal.nombre) == sucursal.strip().lower()
            )

        statement = statement.order_by(
            Producto.id, VarianteProducto.id, Inventario.id
        ).limit(MAX_CANDIDATOS)
        return [_fila_a_candidato(fila) for fila in db.execute(statement).all()]

    @staticmethod
    def obtener_candidato_por_inventario(
        db: Session, inventario_id: int
    ) -> CandidatoInventario | None:
        """Revalida un inventario concreto contra la BD (activo y con stock).

        Es la unica fuente valida para reconstruir un candidato seleccionado por
        la IA. Devuelve ``None`` si ya no cumple las reglas.
        """
        statement = (
            _statement_base().where(Inventario.id == inventario_id).limit(1)
        )
        fila = db.execute(statement).first()
        return _fila_a_candidato(fila) if fila is not None else None

    @staticmethod
    def resolver_imagenes(
        db: Session, candidatos: list[CandidatoInventario]
    ) -> list[CandidatoInventario]:
        """Completa ``imagen`` con el recurso activo mas afin al color.

        Prioridad: recurso activo del mismo color de la variante recomendada
        (principal primero, luego ``id``) y, si no existe, la imagen general
        del producto (principal primero, luego ``id``). Una sola consulta para
        todos los productos.
        """
        producto_ids = {c.producto_id for c in candidatos}
        if not producto_ids:
            return list(candidatos)

        statement = (
            select(
                RecursoProducto.producto_id,
                RecursoProducto.color_id,
                RecursoProducto.url,
                RecursoProducto.es_principal,
            )
            .where(
                RecursoProducto.producto_id.in_(producto_ids),
                RecursoProducto.estado.is_(True),
            )
            .order_by(
                RecursoProducto.es_principal.desc(), RecursoProducto.id
            )
        )
        por_producto: dict[int, list[tuple[int | None, bool, str]]] = {}
        for producto_id, color_id, url, principal in db.execute(
            statement
        ).all():
            por_producto.setdefault(int(producto_id), []).append(
                (color_id, bool(principal), str(url))
            )

        resultado: list[CandidatoInventario] = []
        for candidato in candidatos:
            recursos = por_producto.get(candidato.producto_id, [])
            url = _elegir_imagen(recursos, candidato.color_id)
            resultado.append(replace(candidato, imagen=url))
        return resultado


def _elegir_imagen(
    recursos: list[tuple[int | None, bool, str]], color_id: int
) -> str | None:
    """Elige el recurso del color de la variante revalidada.

    ``recursos`` ya viene ordenado por ``es_principal`` descendente e ``id``
    ascendente, por lo que el primer recurso del color pedido es el principal
    (o el de menor ``id``). Si no hay recurso de ese color se usa la imagen
    general del producto; sin recursos afines devuelve ``None``.
    """
    for recurso_color_id, _principal, url in recursos:
        if recurso_color_id == color_id:
            return url
    for recurso_color_id, _principal, url in recursos:
        if recurso_color_id is None:
            return url
    return None
