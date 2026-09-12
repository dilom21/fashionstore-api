from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.catalogo.models.models import Producto
from app.modules.promociones.models.models import Promocion
from app.modules.promociones.repositories.repository import (
    PromocionRepository,
    registrar_evento_bitacora,
)
from app.modules.promociones.schemas.schemas import (
    PromocionCreate,
    PromocionDetalleResponse,
    PromocionEstadoUpdate,
    PromocionUpdate,
)

ACCION_CREAR = "CREAR"
ACCION_MODIFICAR = "MODIFICAR"

ENTIDAD_PROMOCION = "promocion"
ENTIDAD_PROMOCION_PRODUCTO = "promocion_producto"

TIPOS_DESCUENTO = ("PORCENTAJE", "MONTO")


# ---------------------------------------------------------------------------
# Errores tipados de CU10
# ---------------------------------------------------------------------------


class PromocionNoEncontradaError(Exception):
    pass


class PromocionNombreInvalidoError(Exception):
    pass


class PromocionTipoDescuentoInvalidoError(Exception):
    pass


class PromocionValorInvalidoError(Exception):
    pass


class PromocionFechasInvalidasError(Exception):
    pass


class PromocionRegistroInvalidoError(Exception):
    pass


class AsignacionProductoNoEncontradoError(Exception):
    pass


class AsignacionProductoInactivoError(Exception):
    pass


class AsignacionRegistroInvalidoError(Exception):
    pass


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _limpiar_texto(valor: str | None) -> str:
    if valor is None:
        return ""
    return " ".join(str(valor).split())


def _normalizar_opcional(valor: str | None) -> str | None:
    if valor is None:
        return None
    limpio = str(valor).strip()
    return limpio or None


def _deduplicar(ids: list[int]) -> list[int]:
    vistos: set[int] = set()
    resultado: list[int] = []
    for valor in ids:
        if valor not in vistos:
            vistos.add(valor)
            resultado.append(valor)
    return resultado


def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
    db.execute(
        text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
        {"usuario_id": str(usuario_id)},
    )


def _normalizar_tipo_descuento(valor: str | None) -> str:
    tipo = (valor or "").strip().upper()
    if tipo not in TIPOS_DESCUENTO:
        raise PromocionTipoDescuentoInvalidoError()
    return tipo


def _validar_valor(tipo_descuento: str, valor: Decimal | None) -> None:
    if valor is None or valor <= 0:
        raise PromocionValorInvalidoError()
    if tipo_descuento == "PORCENTAJE" and valor > 100:
        raise PromocionValorInvalidoError()


# ---------------------------------------------------------------------------
# Promociones
# ---------------------------------------------------------------------------


class PromocionService:
    @staticmethod
    def listar(
        db: Session,
        buscar: str | None = None,
        estado: bool | None = None,
        tipo_descuento: str | None = None,
    ) -> list[Promocion]:
        tipo = None
        if tipo_descuento is not None:
            tipo = tipo_descuento.strip().upper() or None
        return PromocionRepository.listar(
            db, buscar=buscar, estado=estado, tipo_descuento=tipo
        )

    @staticmethod
    def obtener(db: Session, promocion_id: int) -> Promocion | None:
        return PromocionRepository.obtener_por_id(db, promocion_id)

    @staticmethod
    def obtener_detalle(
        db: Session, promocion_id: int
    ) -> PromocionDetalleResponse | None:
        promocion = PromocionRepository.obtener_por_id(db, promocion_id)
        if promocion is None:
            return None
        return PromocionDetalleResponse(
            id=promocion.id,
            nombre=promocion.nombre,
            descripcion=promocion.descripcion,
            tipo_descuento=promocion.tipo_descuento,
            valor_descuento=promocion.valor_descuento,
            fecha_inicio=promocion.fecha_inicio,
            fecha_fin=promocion.fecha_fin,
            estado=promocion.estado,
            total_productos=PromocionRepository.contar_productos(
                db, promocion.id
            ),
        )

    @staticmethod
    def crear(
        db: Session, datos: PromocionCreate, admin: Usuario
    ) -> Promocion:
        nombre = _limpiar_texto(datos.nombre)
        if not nombre:
            raise PromocionNombreInvalidoError()

        tipo_descuento = _normalizar_tipo_descuento(datos.tipo_descuento)
        _validar_valor(tipo_descuento, datos.valor_descuento)

        if datos.fecha_inicio > datos.fecha_fin:
            raise PromocionFechasInvalidasError()

        descripcion = _normalizar_opcional(datos.descripcion)

        try:
            _establecer_contexto_bitacora(db, admin.id)
            promocion = PromocionRepository.crear(
                db,
                nombre=nombre,
                descripcion=descripcion,
                tipo_descuento=tipo_descuento,
                valor_descuento=datos.valor_descuento,
                fecha_inicio=datos.fecha_inicio,
                fecha_fin=datos.fecha_fin,
            )
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_CREAR,
                entidad_afectada=ENTIDAD_PROMOCION,
                descripcion=(
                    f"Promocion creada: {promocion.nombre} "
                    f"(id {promocion.id}, {tipo_descuento} "
                    f"{datos.valor_descuento})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise PromocionRegistroInvalidoError() from exc

        promocion = PromocionRepository.obtener_por_id(db, promocion.id)
        if promocion is None:
            raise PromocionNoEncontradaError()
        return promocion

    @staticmethod
    def actualizar(
        db: Session,
        promocion_id: int,
        datos: PromocionUpdate,
        admin: Usuario,
    ) -> Promocion:
        promocion = PromocionRepository.obtener_por_id(db, promocion_id)
        if promocion is None:
            raise PromocionNoEncontradaError()

        nombre = promocion.nombre
        if datos.nombre is not None:
            nombre = _limpiar_texto(datos.nombre)
            if not nombre:
                raise PromocionNombreInvalidoError()

        tipo_descuento = promocion.tipo_descuento
        if datos.tipo_descuento is not None:
            tipo_descuento = _normalizar_tipo_descuento(datos.tipo_descuento)

        valor_descuento = (
            datos.valor_descuento
            if datos.valor_descuento is not None
            else promocion.valor_descuento
        )
        _validar_valor(tipo_descuento, valor_descuento)

        fecha_inicio = (
            datos.fecha_inicio
            if datos.fecha_inicio is not None
            else promocion.fecha_inicio
        )
        fecha_fin = (
            datos.fecha_fin
            if datos.fecha_fin is not None
            else promocion.fecha_fin
        )
        if fecha_inicio > fecha_fin:
            raise PromocionFechasInvalidasError()

        descripcion = promocion.descripcion
        if "descripcion" in datos.model_fields_set:
            descripcion = _normalizar_opcional(datos.descripcion)

        hay_cambios = (
            nombre != promocion.nombre
            or descripcion != promocion.descripcion
            or tipo_descuento != promocion.tipo_descuento
            or valor_descuento != promocion.valor_descuento
            or fecha_inicio != promocion.fecha_inicio
            or fecha_fin != promocion.fecha_fin
        )
        if not hay_cambios:
            return promocion

        promocion.nombre = nombre
        promocion.descripcion = descripcion
        promocion.tipo_descuento = tipo_descuento
        promocion.valor_descuento = valor_descuento
        promocion.fecha_inicio = fecha_inicio
        promocion.fecha_fin = fecha_fin

        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_PROMOCION,
                descripcion=(
                    f"Promocion actualizada: {promocion.nombre} "
                    f"(id {promocion.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise PromocionRegistroInvalidoError() from exc

        promocion = PromocionRepository.obtener_por_id(db, promocion.id)
        if promocion is None:
            raise PromocionNoEncontradaError()
        return promocion

    @staticmethod
    def cambiar_estado(
        db: Session,
        promocion_id: int,
        datos: PromocionEstadoUpdate,
        admin: Usuario,
    ) -> Promocion:
        promocion = PromocionRepository.obtener_por_id(db, promocion_id)
        if promocion is None:
            raise PromocionNoEncontradaError()

        if promocion.estado == datos.estado:
            return promocion

        promocion.estado = datos.estado
        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_PROMOCION,
                descripcion=(
                    f"Promocion "
                    f"{'habilitada' if datos.estado else 'deshabilitada'}: "
                    f"{promocion.nombre} (id {promocion.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise PromocionRegistroInvalidoError() from exc

        promocion = PromocionRepository.obtener_por_id(db, promocion.id)
        if promocion is None:
            raise PromocionNoEncontradaError()
        return promocion

    @staticmethod
    def listar_productos(
        db: Session, promocion_id: int
    ) -> list[Producto] | None:
        promocion = PromocionRepository.obtener_por_id(db, promocion_id)
        if promocion is None:
            return None
        return PromocionRepository.listar_productos(db, promocion_id)

    @staticmethod
    def reemplazar_productos(
        db: Session,
        promocion_id: int,
        producto_ids: list[int],
        admin: Usuario,
    ) -> list[Producto]:
        """Reemplaza de forma atomica e idempotente los productos asignados."""
        promocion = PromocionRepository.obtener_por_id(db, promocion_id)
        if promocion is None:
            raise PromocionNoEncontradaError()

        ids = _deduplicar(producto_ids)

        productos = PromocionRepository.obtener_productos_por_ids(db, ids)
        encontrados = {producto.id: producto for producto in productos}

        faltantes = [
            producto_id for producto_id in ids if producto_id not in encontrados
        ]
        if faltantes:
            raise AsignacionProductoNoEncontradoError()

        inactivos = [
            producto_id
            for producto_id in ids
            if not encontrados[producto_id].estado
        ]
        if inactivos:
            raise AsignacionProductoInactivoError()

        actuales = set(
            PromocionRepository.listar_producto_ids(db, promocion_id)
        )
        if actuales == set(ids):
            return PromocionRepository.listar_productos(db, promocion_id)

        try:
            _establecer_contexto_bitacora(db, admin.id)
            PromocionRepository.reemplazar_productos(
                db, promocion_id=promocion_id, producto_ids=ids
            )
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_PROMOCION_PRODUCTO,
                descripcion=(
                    f"Productos asignados a promocion {promocion.id}: "
                    f"{sorted(ids)}"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise AsignacionRegistroInvalidoError() from exc

        return PromocionRepository.listar_productos(db, promocion_id)
