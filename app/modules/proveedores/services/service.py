from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.proveedores.models.models import Proveedor, ProveedorProducto
from app.modules.proveedores.repositories.repository import (
    ProveedorRepository,
    registrar_evento_bitacora,
)
from app.modules.proveedores.schemas.schemas import (
    ProveedorCreate,
    ProveedorDetalleResponse,
    ProveedorEstadoUpdate,
    ProveedorProductoResponse,
    ProveedorProductosResponse,
    ProveedorProductosUpdate,
    ProveedorUpdate,
)

ACCION_CREAR = "CREAR"
ACCION_MODIFICAR = "MODIFICAR"

ENTIDAD_PROVEEDOR = "proveedor"
ENTIDAD_PROVEEDOR_PRODUCTO = "proveedor_producto"

# Estados de orden_compra que representan ordenes activas/no finalizadas.
# Finalizadas: RECIBIDA, CANCELADA.
ESTADOS_ORDEN_COMPRA_ACTIVA = ("BORRADOR", "ENVIADA", "PARCIAL")


# ---------------------------------------------------------------------------
# Errores tipados de CU11
# ---------------------------------------------------------------------------


class ProveedorNoEncontradoError(Exception):
    pass


class ProveedorRazonSocialInvalidaError(Exception):
    pass


class ProveedorCorreoInvalidoError(Exception):
    pass


class ProveedorNitDuplicadoError(Exception):
    pass


class ProveedorDependenciaActivaError(Exception):
    """El proveedor tiene ordenes de compra activas/no finalizadas."""


class ProveedorRegistroInvalidoError(Exception):
    pass


class ProveedorProductoNoEncontradoError(Exception):
    """Alguno de los productos informados no existe."""


class ProveedorProductoInactivoError(Exception):
    """Se intenta crear una nueva relacion con un producto inactivo."""


class ProveedorProductoCostoInvalidoError(Exception):
    pass


class ProveedorProductoRegistroInvalidoError(Exception):
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


def _normalizar_correo(valor: str | None) -> str | None:
    limpio = _normalizar_opcional(valor)
    if limpio is None:
        return None
    correo = limpio.lower()
    if "@" not in correo or correo.startswith("@") or correo.endswith("@"):
        raise ProveedorCorreoInvalidoError()
    return correo


def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
    db.execute(
        text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
        {"usuario_id": str(usuario_id)},
    )


def _construir_productos_response(
    proveedor_id: int, items: list[ProveedorProducto]
) -> ProveedorProductosResponse:
    productos = [
        ProveedorProductoResponse(
            producto_id=item.producto_id,
            nombre=item.producto.nombre,
            descripcion=item.producto.descripcion,
            precio=item.producto.precio,
            categoria_id=item.producto.categoria_id,
            producto_estado=item.producto.estado,
            costo_referencia=item.costo_referencia,
            estado=item.estado,
        )
        for item in items
    ]
    return ProveedorProductosResponse(
        proveedor_id=proveedor_id,
        total=len(productos),
        productos=productos,
    )


# ---------------------------------------------------------------------------
# Proveedores
# ---------------------------------------------------------------------------


class ProveedorService:
    @staticmethod
    def listar(
        db: Session,
        buscar: str | None = None,
        estado: bool | None = None,
    ) -> list[Proveedor]:
        return ProveedorRepository.listar(db, buscar=buscar, estado=estado)

    @staticmethod
    def obtener(db: Session, proveedor_id: int) -> Proveedor | None:
        return ProveedorRepository.obtener_por_id(db, proveedor_id)

    @staticmethod
    def obtener_detalle(
        db: Session, proveedor_id: int
    ) -> ProveedorDetalleResponse | None:
        proveedor = ProveedorRepository.obtener_por_id(db, proveedor_id)
        if proveedor is None:
            return None
        return ProveedorDetalleResponse(
            id=proveedor.id,
            razon_social=proveedor.razon_social,
            nit=proveedor.nit,
            correo=proveedor.correo,
            telefono=proveedor.telefono,
            direccion=proveedor.direccion,
            estado=proveedor.estado,
            total_productos=ProveedorRepository.contar_productos(
                db, proveedor.id
            ),
        )

    @staticmethod
    def crear(
        db: Session, datos: ProveedorCreate, admin: Usuario
    ) -> Proveedor:
        razon_social = _limpiar_texto(datos.razon_social)
        if not razon_social:
            raise ProveedorRazonSocialInvalidaError()

        nit = _normalizar_opcional(datos.nit)
        if nit is not None and ProveedorRepository.buscar_por_nit(db, nit):
            raise ProveedorNitDuplicadoError()

        correo = _normalizar_correo(datos.correo)
        telefono = _normalizar_opcional(datos.telefono)
        direccion = _normalizar_opcional(datos.direccion)

        try:
            _establecer_contexto_bitacora(db, admin.id)
            proveedor = ProveedorRepository.crear(
                db,
                razon_social=razon_social,
                nit=nit,
                correo=correo,
                telefono=telefono,
                direccion=direccion,
            )
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_CREAR,
                entidad_afectada=ENTIDAD_PROVEEDOR,
                descripcion=(
                    f"Proveedor creado: {proveedor.razon_social} "
                    f"(id {proveedor.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ProveedorNitDuplicadoError() from exc

        proveedor = ProveedorRepository.obtener_por_id(db, proveedor.id)
        if proveedor is None:
            raise ProveedorNoEncontradoError()
        return proveedor

    @staticmethod
    def actualizar(
        db: Session,
        proveedor_id: int,
        datos: ProveedorUpdate,
        admin: Usuario,
    ) -> Proveedor:
        proveedor = ProveedorRepository.obtener_por_id(db, proveedor_id)
        if proveedor is None:
            raise ProveedorNoEncontradoError()

        campos = datos.model_fields_set
        hay_cambios = False

        if "razon_social" in campos:
            if datos.razon_social is None:
                raise ProveedorRazonSocialInvalidaError()
            razon_social = _limpiar_texto(datos.razon_social)
            if not razon_social:
                raise ProveedorRazonSocialInvalidaError()
            if razon_social != proveedor.razon_social:
                proveedor.razon_social = razon_social
                hay_cambios = True

        if "nit" in campos:
            nit = _normalizar_opcional(datos.nit)
            if nit != proveedor.nit:
                if nit is not None:
                    existente = ProveedorRepository.buscar_por_nit(
                        db, nit, excluir_id=proveedor.id
                    )
                    if existente is not None:
                        raise ProveedorNitDuplicadoError()
                proveedor.nit = nit
                hay_cambios = True

        if "correo" in campos:
            correo = _normalizar_correo(datos.correo)
            if correo != proveedor.correo:
                proveedor.correo = correo
                hay_cambios = True

        if "telefono" in campos:
            telefono = _normalizar_opcional(datos.telefono)
            if telefono != proveedor.telefono:
                proveedor.telefono = telefono
                hay_cambios = True

        if "direccion" in campos:
            direccion = _normalizar_opcional(datos.direccion)
            if direccion != proveedor.direccion:
                proveedor.direccion = direccion
                hay_cambios = True

        if not hay_cambios:
            return proveedor

        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_PROVEEDOR,
                descripcion=(
                    f"Proveedor actualizado: {proveedor.razon_social} "
                    f"(id {proveedor.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ProveedorNitDuplicadoError() from exc

        proveedor = ProveedorRepository.obtener_por_id(db, proveedor.id)
        if proveedor is None:
            raise ProveedorNoEncontradoError()
        return proveedor

    @staticmethod
    def cambiar_estado(
        db: Session,
        proveedor_id: int,
        datos: ProveedorEstadoUpdate,
        admin: Usuario,
    ) -> Proveedor:
        proveedor = ProveedorRepository.obtener_por_id(db, proveedor_id)
        if proveedor is None:
            raise ProveedorNoEncontradoError()

        if proveedor.estado == datos.estado:
            return proveedor

        if not datos.estado:
            activas = ProveedorRepository.contar_ordenes_compra_activas(
                db, proveedor.id, ESTADOS_ORDEN_COMPRA_ACTIVA
            )
            if activas > 0:
                raise ProveedorDependenciaActivaError()

        proveedor.estado = datos.estado
        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_PROVEEDOR,
                descripcion=(
                    f"Proveedor "
                    f"{'habilitado' if datos.estado else 'deshabilitado'}: "
                    f"{proveedor.razon_social} (id {proveedor.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ProveedorRegistroInvalidoError() from exc

        proveedor = ProveedorRepository.obtener_por_id(db, proveedor.id)
        if proveedor is None:
            raise ProveedorNoEncontradoError()
        return proveedor

    # ------------------------------------------------------------------
    # Productos del proveedor
    # ------------------------------------------------------------------

    @staticmethod
    def listar_productos(
        db: Session, proveedor_id: int
    ) -> ProveedorProductosResponse | None:
        proveedor = ProveedorRepository.obtener_por_id(db, proveedor_id)
        if proveedor is None:
            return None
        items = ProveedorRepository.listar_productos(db, proveedor_id)
        return _construir_productos_response(proveedor_id, items)

    @staticmethod
    def reemplazar_productos(
        db: Session,
        proveedor_id: int,
        datos: ProveedorProductosUpdate,
        admin: Usuario,
    ) -> ProveedorProductosResponse:
        """Reemplaza de forma atomica e idempotente los productos asociados."""
        proveedor = ProveedorRepository.obtener_por_id(db, proveedor_id)
        if proveedor is None:
            raise ProveedorNoEncontradoError()

        # Deduplicacion por producto_id conservando la ultima ocurrencia.
        normalizados: dict[int, dict] = {}
        for item in datos.productos:
            costo = item.costo_referencia
            if costo is None:
                costo = Decimal("0")
            if costo < 0:
                raise ProveedorProductoCostoInvalidoError()
            normalizados[item.producto_id] = {
                "producto_id": item.producto_id,
                "costo_referencia": costo,
                "estado": item.estado,
            }

        ids = list(normalizados.keys())
        productos = ProveedorRepository.obtener_productos_por_ids(db, ids)
        encontrados = {producto.id: producto for producto in productos}

        faltantes = [producto_id for producto_id in ids if producto_id not in encontrados]
        if faltantes:
            raise ProveedorProductoNoEncontradoError()

        actuales_items = ProveedorRepository.listar_productos(db, proveedor_id)
        actuales_map = {item.producto_id: item for item in actuales_items}
        actuales = set(actuales_map)

        inactivos = [
            producto_id
            for producto_id in ids
            if producto_id not in actuales
            and not encontrados[producto_id].estado
        ]
        if inactivos:
            raise ProveedorProductoInactivoError()

        sin_cambios = actuales == set(ids) and all(
            actuales_map[producto_id].costo_referencia
            == normalizados[producto_id]["costo_referencia"]
            and actuales_map[producto_id].estado
            == normalizados[producto_id]["estado"]
            for producto_id in ids
        )
        if sin_cambios:
            return _construir_productos_response(proveedor_id, actuales_items)

        try:
            _establecer_contexto_bitacora(db, admin.id)
            ProveedorRepository.reemplazar_productos(
                db,
                proveedor_id=proveedor_id,
                items=list(normalizados.values()),
            )
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_PROVEEDOR_PRODUCTO,
                descripcion=(
                    f"Productos del proveedor {proveedor.id} "
                    f"reemplazados: {sorted(ids)}"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ProveedorProductoRegistroInvalidoError() from exc

        items = ProveedorRepository.listar_productos(db, proveedor_id)
        return _construir_productos_response(proveedor_id, items)
