from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.catalogo.models.models import Producto, Temporada
from app.modules.temporadas_colecciones.models.models import Coleccion
from app.modules.temporadas_colecciones.repositories.repository import (
    ColeccionRepository,
    TemporadaRepository,
    registrar_evento_bitacora,
)
from app.modules.temporadas_colecciones.schemas.schemas import (
    ColeccionCreate,
    ColeccionDetalleResponse,
    ColeccionEstadoUpdate,
    ColeccionUpdate,
    TemporadaCreate,
    TemporadaEstadoUpdate,
    TemporadaResumenResponse,
    TemporadaUpdate,
)

ACCION_CREAR = "CREAR"
ACCION_MODIFICAR = "MODIFICAR"

ENTIDAD_TEMPORADA = "temporada"
ENTIDAD_COLECCION = "coleccion"
ENTIDAD_PRODUCTO_COLECCION = "producto_coleccion"


# ---------------------------------------------------------------------------
# Errores tipados de CU08
# ---------------------------------------------------------------------------


class TemporadaNoEncontradaError(Exception):
    pass


class TemporadaNombreInvalidoError(Exception):
    pass


class TemporadaNombreDuplicadoError(Exception):
    pass


class TemporadaFechasInvalidasError(Exception):
    pass


class TemporadaConColeccionesActivasError(Exception):
    """La temporada tiene colecciones activas y no puede deshabilitarse."""


class TemporadaConInventarioError(Exception):
    """La temporada tiene inventario con stock o reservas y no puede deshabilitarse."""


class TemporadaRegistroInvalidoError(Exception):
    pass


class ColeccionNoEncontradaError(Exception):
    pass


class ColeccionNombreInvalidoError(Exception):
    pass


class ColeccionNombreDuplicadoError(Exception):
    pass


class ColeccionTemporadaNoEncontradaError(Exception):
    pass


class ColeccionTemporadaInactivaError(Exception):
    pass


class ColeccionRegistroInvalidoError(Exception):
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


# ---------------------------------------------------------------------------
# Temporadas
# ---------------------------------------------------------------------------


class TemporadaService:
    @staticmethod
    def listar(
        db: Session,
        buscar: str | None = None,
        estado: bool | None = None,
    ) -> list[Temporada]:
        return TemporadaRepository.listar(db, buscar=buscar, estado=estado)

    @staticmethod
    def obtener(db: Session, temporada_id: int) -> Temporada | None:
        return TemporadaRepository.obtener_por_id(db, temporada_id)

    @staticmethod
    def crear(
        db: Session, datos: TemporadaCreate, admin: Usuario
    ) -> Temporada:
        nombre = _limpiar_texto(datos.nombre)
        if not nombre:
            raise TemporadaNombreInvalidoError()

        if datos.fecha_inicio > datos.fecha_fin:
            raise TemporadaFechasInvalidasError()

        if (
            TemporadaRepository.buscar_por_nombre_normalizado(db, nombre)
            is not None
        ):
            raise TemporadaNombreDuplicadoError()

        try:
            _establecer_contexto_bitacora(db, admin.id)
            temporada = TemporadaRepository.crear(
                db,
                nombre=nombre,
                fecha_inicio=datos.fecha_inicio,
                fecha_fin=datos.fecha_fin,
            )
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_CREAR,
                entidad_afectada=ENTIDAD_TEMPORADA,
                descripcion=(
                    f"Temporada creada: {temporada.nombre} "
                    f"(id {temporada.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise TemporadaNombreDuplicadoError() from exc

        temporada = TemporadaRepository.obtener_por_id(db, temporada.id)
        if temporada is None:
            raise TemporadaNoEncontradaError()
        return temporada

    @staticmethod
    def actualizar(
        db: Session,
        temporada_id: int,
        datos: TemporadaUpdate,
        admin: Usuario,
    ) -> Temporada:
        temporada = TemporadaRepository.obtener_por_id(db, temporada_id)
        if temporada is None:
            raise TemporadaNoEncontradaError()

        nombre = temporada.nombre
        if datos.nombre is not None:
            nombre = _limpiar_texto(datos.nombre)
            if not nombre:
                raise TemporadaNombreInvalidoError()

        fecha_inicio: date = (
            datos.fecha_inicio
            if datos.fecha_inicio is not None
            else temporada.fecha_inicio
        )
        fecha_fin: date = (
            datos.fecha_fin
            if datos.fecha_fin is not None
            else temporada.fecha_fin
        )
        if fecha_inicio > fecha_fin:
            raise TemporadaFechasInvalidasError()

        if nombre != temporada.nombre:
            existente = TemporadaRepository.buscar_por_nombre_normalizado(
                db, nombre, excluir_id=temporada.id
            )
            if existente is not None:
                raise TemporadaNombreDuplicadoError()

        hay_cambios = (
            nombre != temporada.nombre
            or fecha_inicio != temporada.fecha_inicio
            or fecha_fin != temporada.fecha_fin
        )
        if not hay_cambios:
            return temporada

        temporada.nombre = nombre
        temporada.fecha_inicio = fecha_inicio
        temporada.fecha_fin = fecha_fin

        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_TEMPORADA,
                descripcion=(
                    f"Temporada actualizada: {temporada.nombre} "
                    f"(id {temporada.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise TemporadaNombreDuplicadoError() from exc

        temporada = TemporadaRepository.obtener_por_id(db, temporada.id)
        if temporada is None:
            raise TemporadaNoEncontradaError()
        return temporada

    @staticmethod
    def cambiar_estado(
        db: Session,
        temporada_id: int,
        datos: TemporadaEstadoUpdate,
        admin: Usuario,
    ) -> Temporada:
        temporada = TemporadaRepository.obtener_por_id(db, temporada_id)
        if temporada is None:
            raise TemporadaNoEncontradaError()

        if temporada.estado == datos.estado:
            return temporada

        if not datos.estado:
            if (
                TemporadaRepository.contar_colecciones_activas(
                    db, temporada.id
                )
                > 0
            ):
                raise TemporadaConColeccionesActivasError()
            if (
                TemporadaRepository.contar_inventario_con_stock(
                    db, temporada.id
                )
                > 0
            ):
                raise TemporadaConInventarioError()

        temporada.estado = datos.estado
        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_TEMPORADA,
                descripcion=(
                    f"Temporada {'habilitada' if datos.estado else 'deshabilitada'}: "
                    f"{temporada.nombre} (id {temporada.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise TemporadaRegistroInvalidoError() from exc

        temporada = TemporadaRepository.obtener_por_id(db, temporada.id)
        if temporada is None:
            raise TemporadaNoEncontradaError()
        return temporada


# ---------------------------------------------------------------------------
# Colecciones
# ---------------------------------------------------------------------------


class ColeccionService:
    @staticmethod
    def listar(
        db: Session,
        buscar: str | None = None,
        temporada_id: int | None = None,
        estado: bool | None = None,
    ) -> list[Coleccion]:
        return ColeccionRepository.listar(
            db, buscar=buscar, temporada_id=temporada_id, estado=estado
        )

    @staticmethod
    def obtener(db: Session, coleccion_id: int) -> Coleccion | None:
        return ColeccionRepository.obtener_por_id(db, coleccion_id)

    @staticmethod
    def obtener_detalle(
        db: Session, coleccion_id: int
    ) -> ColeccionDetalleResponse | None:
        coleccion = ColeccionRepository.obtener_por_id(db, coleccion_id)
        if coleccion is None:
            return None
        return ColeccionDetalleResponse(
            id=coleccion.id,
            temporada_id=coleccion.temporada_id,
            nombre=coleccion.nombre,
            descripcion=coleccion.descripcion,
            estado=coleccion.estado,
            temporada=TemporadaResumenResponse.model_validate(
                coleccion.temporada
            ),
            total_productos=ColeccionRepository.contar_productos(
                db, coleccion.id
            ),
        )

    @staticmethod
    def _validar_temporada_activa(db: Session, temporada_id: int) -> Temporada:
        temporada = TemporadaRepository.obtener_por_id(db, temporada_id)
        if temporada is None:
            raise ColeccionTemporadaNoEncontradaError()
        if not temporada.estado:
            raise ColeccionTemporadaInactivaError()
        return temporada

    @staticmethod
    def crear(
        db: Session, datos: ColeccionCreate, admin: Usuario
    ) -> Coleccion:
        ColeccionService._validar_temporada_activa(db, datos.temporada_id)

        nombre = _limpiar_texto(datos.nombre)
        if not nombre:
            raise ColeccionNombreInvalidoError()

        if (
            ColeccionRepository.buscar_por_nombre_en_temporada(
                db,
                temporada_id=datos.temporada_id,
                nombre=nombre,
            )
            is not None
        ):
            raise ColeccionNombreDuplicadoError()

        descripcion = _normalizar_opcional(datos.descripcion)

        try:
            _establecer_contexto_bitacora(db, admin.id)
            coleccion = ColeccionRepository.crear(
                db,
                temporada_id=datos.temporada_id,
                nombre=nombre,
                descripcion=descripcion,
            )
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_CREAR,
                entidad_afectada=ENTIDAD_COLECCION,
                descripcion=(
                    f"Coleccion creada: {coleccion.nombre} "
                    f"(id {coleccion.id}, temporada {coleccion.temporada_id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ColeccionNombreDuplicadoError() from exc

        coleccion = ColeccionRepository.obtener_por_id(db, coleccion.id)
        if coleccion is None:
            raise ColeccionNoEncontradaError()
        return coleccion

    @staticmethod
    def actualizar(
        db: Session,
        coleccion_id: int,
        datos: ColeccionUpdate,
        admin: Usuario,
    ) -> Coleccion:
        coleccion = ColeccionRepository.obtener_por_id(db, coleccion_id)
        if coleccion is None:
            raise ColeccionNoEncontradaError()

        temporada_id = coleccion.temporada_id
        if (
            datos.temporada_id is not None
            and datos.temporada_id != coleccion.temporada_id
        ):
            ColeccionService._validar_temporada_activa(
                db, datos.temporada_id
            )
            temporada_id = datos.temporada_id

        nombre = coleccion.nombre
        if datos.nombre is not None:
            nombre = _limpiar_texto(datos.nombre)
            if not nombre:
                raise ColeccionNombreInvalidoError()

        if temporada_id != coleccion.temporada_id or nombre != coleccion.nombre:
            existente = ColeccionRepository.buscar_por_nombre_en_temporada(
                db,
                temporada_id=temporada_id,
                nombre=nombre,
                excluir_id=coleccion.id,
            )
            if existente is not None:
                raise ColeccionNombreDuplicadoError()

        hay_cambios = (
            temporada_id != coleccion.temporada_id
            or nombre != coleccion.nombre
        )

        descripcion = coleccion.descripcion
        if "descripcion" in datos.model_fields_set:
            descripcion = _normalizar_opcional(datos.descripcion)
            if descripcion != coleccion.descripcion:
                hay_cambios = True

        if not hay_cambios:
            return coleccion

        coleccion.temporada_id = temporada_id
        coleccion.nombre = nombre
        coleccion.descripcion = descripcion

        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_COLECCION,
                descripcion=(
                    f"Coleccion actualizada: {coleccion.nombre} "
                    f"(id {coleccion.id}, temporada {coleccion.temporada_id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ColeccionNombreDuplicadoError() from exc

        coleccion = ColeccionRepository.obtener_por_id(db, coleccion.id)
        if coleccion is None:
            raise ColeccionNoEncontradaError()
        return coleccion

    @staticmethod
    def cambiar_estado(
        db: Session,
        coleccion_id: int,
        datos: ColeccionEstadoUpdate,
        admin: Usuario,
    ) -> Coleccion:
        coleccion = ColeccionRepository.obtener_por_id(db, coleccion_id)
        if coleccion is None:
            raise ColeccionNoEncontradaError()

        if coleccion.estado == datos.estado:
            return coleccion

        # Deshabilitar una coleccion conserva sus relaciones de producto como
        # historico (regla 9); no hay dependencia que lo impida.
        coleccion.estado = datos.estado
        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_COLECCION,
                descripcion=(
                    f"Coleccion {'habilitada' if datos.estado else 'deshabilitada'}: "
                    f"{coleccion.nombre} (id {coleccion.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ColeccionRegistroInvalidoError() from exc

        coleccion = ColeccionRepository.obtener_por_id(db, coleccion.id)
        if coleccion is None:
            raise ColeccionNoEncontradaError()
        return coleccion

    @staticmethod
    def listar_productos(
        db: Session, coleccion_id: int
    ) -> list[Producto] | None:
        coleccion = ColeccionRepository.obtener_por_id(db, coleccion_id)
        if coleccion is None:
            return None
        return ColeccionRepository.listar_productos(db, coleccion_id)

    @staticmethod
    def reemplazar_productos(
        db: Session,
        coleccion_id: int,
        producto_ids: list[int],
        admin: Usuario,
    ) -> list[Producto]:
        """Reemplaza de forma atomica e idempotente los productos asignados."""
        coleccion = ColeccionRepository.obtener_por_id(db, coleccion_id)
        if coleccion is None:
            raise ColeccionNoEncontradaError()

        ids = _deduplicar(producto_ids)

        productos = ColeccionRepository.obtener_productos_por_ids(db, ids)
        encontrados = {producto.id: producto for producto in productos}

        faltantes = [producto_id for producto_id in ids if producto_id not in encontrados]
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
            ColeccionRepository.listar_producto_ids(db, coleccion_id)
        )
        if actuales == set(ids):
            return ColeccionRepository.listar_productos(db, coleccion_id)

        try:
            _establecer_contexto_bitacora(db, admin.id)
            ColeccionRepository.reemplazar_productos(
                db, coleccion_id=coleccion_id, producto_ids=ids
            )
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_PRODUCTO_COLECCION,
                descripcion=(
                    f"Productos asignados a coleccion {coleccion.id}: "
                    f"{sorted(ids)}"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise AsignacionRegistroInvalidoError() from exc

        return ColeccionRepository.listar_productos(db, coleccion_id)
