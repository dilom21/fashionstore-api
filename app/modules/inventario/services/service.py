from datetime import date, datetime, time, timezone

from sqlalchemy.orm import Session

from app.core.tenant_context import TenantContext
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    TenantAuthRepository,
)
from app.modules.inventario.models.models import Inventario
from app.modules.inventario.repositories.repository import InventarioRepository
from app.modules.inventario.schemas.schemas import (
    DisponibilidadFiltro,
    InventarioConsultaResponse,
    InventarioFiltrosResponse,
    InventarioItemResponse,
    MovimientoInventarioConsultaResponse,
    MovimientoInventarioItemResponse,
    TipoMovimiento,
)

ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROL_ENCARGADO_SUCURSAL = "ENCARGADO_SUCURSAL"
ROLES_INVENTARIO_SAAS = frozenset({ROL_ADMINISTRADOR, ROL_ENCARGADO_SUCURSAL})

DISPONIBILIDADES_VALIDAS = frozenset(
    {
        DisponibilidadFiltro.TODOS.value,
        DisponibilidadFiltro.CON_STOCK.value,
        DisponibilidadFiltro.SIN_STOCK.value,
        DisponibilidadFiltro.STOCK_BAJO.value,
    }
)

TIPOS_MOVIMIENTO_VALIDOS = frozenset(tipo.value for tipo in TipoMovimiento)


class SucursalScopeError(Exception):
    """El usuario no puede consultar la sucursal solicitada."""


class SucursalNoEncontradaError(Exception):
    pass


class DisponibilidadInvalidaError(Exception):
    pass


class TipoMovimientoInvalidoError(Exception):
    pass


class RangoFechasInvalidoError(Exception):
    pass


class RolInventarioTenantError(Exception):
    """El rol empresarial no puede consultar CU08."""


def _es_administrador(usuario: Usuario) -> bool:
    return str(usuario.rol.nombre).strip().upper() == ROL_ADMINISTRADOR


def _sucursal_scope(usuario: Usuario) -> int | None:
    """Alcance de sucursal del usuario autenticado.

    ADMINISTRADOR: None (puede consultar cualquier sucursal).
    Otros roles (ENCARGADO_SUCURSAL, CAJERO): la sucursal de su empleado activo.
    Sin empleado activo asociado: acceso denegado.
    """
    if _es_administrador(usuario):
        return None
    empleado = usuario.empleado
    if empleado is None or not empleado.estado:
        raise SucursalScopeError()
    return empleado.sucursal_id


def _sucursal_scope_tenant(db: Session, tenant: TenantContext) -> int | None:
    """Alcance CU08 basado solamente en membresia/rol empresarial vigentes."""
    rol = tenant.rol_nombre.strip().upper()
    if tenant.contexto != "personal" or rol not in ROLES_INVENTARIO_SAAS:
        raise RolInventarioTenantError()
    if rol == ROL_ADMINISTRADOR:
        return None
    perfil = TenantAuthRepository.obtener_perfil_personal(
        db, usuario_id=tenant.usuario_id, empresa_id=tenant.empresa_id
    )
    if perfil is None:
        raise SucursalScopeError()
    return perfil.sucursal_id


def _resolver_sucursal_tenant(
    db: Session, tenant: TenantContext, sucursal_id: int | None
) -> int | None:
    scope = _sucursal_scope_tenant(db, tenant)
    if scope is not None:
        if sucursal_id is not None and sucursal_id != scope:
            raise SucursalScopeError()
        return scope
    if sucursal_id is not None and InventarioRepository.obtener_sucursal_activa(
        db, sucursal_id, empresa_id=tenant.empresa_id
    ) is None:
        raise SucursalNoEncontradaError()
    return sucursal_id


def _construir_item(inventario: Inventario) -> InventarioItemResponse:
    variante = inventario.variante_producto
    producto = variante.producto
    categoria = producto.categoria
    return InventarioItemResponse(
        inventario_id=inventario.id,
        sucursal_id=inventario.sucursal_id,
        sucursal_nombre=inventario.sucursal.nombre,
        producto_id=producto.id,
        producto_nombre=producto.nombre,
        precio=producto.precio,
        categoria_id=categoria.id,
        categoria_nombre=categoria.nombre,
        variante_producto_id=variante.id,
        sku=variante.sku,
        talla_id=variante.talla.id,
        talla_nombre=variante.talla.nombre,
        color_id=variante.color.id,
        color_nombre=variante.color.nombre,
        temporada_id=inventario.temporada_id,
        temporada_nombre=inventario.temporada.nombre,
        stock_actual=inventario.stock_actual,
        stock_reservado=inventario.stock_reservado,
        stock_disponible=inventario.stock_actual - inventario.stock_reservado,
        fecha_actualizacion=inventario.fecha_actualizacion,
    )


def _rango_fechas(
    fecha_desde: date | None, fecha_hasta: date | None
) -> tuple[datetime | None, datetime | None]:
    """Convierte fechas (inclusive) a rango de datetimes UTC."""
    if (
        fecha_desde is not None
        and fecha_hasta is not None
        and fecha_desde > fecha_hasta
    ):
        raise RangoFechasInvalidoError()
    desde = (
        datetime.combine(fecha_desde, time.min, tzinfo=timezone.utc)
        if fecha_desde is not None
        else None
    )
    hasta = (
        datetime.combine(fecha_hasta, time.max, tzinfo=timezone.utc)
        if fecha_hasta is not None
        else None
    )
    return desde, hasta


def _construir_movimiento_item(fila) -> MovimientoInventarioItemResponse:
    (
        movimiento,
        inventario,
        sucursal,
        variante,
        producto,
        talla,
        color,
        temporada,
        usuario,
    ) = fila
    return MovimientoInventarioItemResponse(
        movimiento_id=movimiento.id,
        inventario_id=movimiento.inventario_id,
        usuario_id=movimiento.usuario_id,
        usuario_correo=usuario.correo if usuario is not None else None,
        tipo=movimiento.tipo,
        cantidad=movimiento.cantidad,
        fecha_hora=movimiento.fecha_hora,
        observaciones=movimiento.observaciones,
        referencia_tipo=movimiento.referencia_tipo,
        referencia_id=movimiento.referencia_id,
        sucursal_id=inventario.sucursal_id,
        sucursal_nombre=sucursal.nombre,
        producto_id=producto.id,
        producto_nombre=producto.nombre,
        variante_producto_id=variante.id,
        sku=variante.sku,
        talla_id=talla.id,
        talla_nombre=talla.nombre,
        color_id=color.id,
        color_nombre=color.nombre,
        temporada_id=inventario.temporada_id,
        temporada_nombre=temporada.nombre,
    )


class InventarioService:
    @staticmethod
    def listar_inventario(
        db: Session,
        sucursal_id: int | None = None,
        producto_id: int | None = None,
        categoria_id: int | None = None,
        talla_id: int | None = None,
        color_id: int | None = None,
        temporada_id: int | None = None,
    ) -> list[Inventario]:
        return InventarioRepository.listar(
            db,
            sucursal_id=sucursal_id,
            producto_id=producto_id,
            categoria_id=categoria_id,
            talla_id=talla_id,
            color_id=color_id,
            temporada_id=temporada_id,
        )

    @staticmethod
    def obtener_inventario(db: Session, inventario_id: int) -> Inventario | None:
        return InventarioRepository.obtener_por_id(db, inventario_id)

    @staticmethod
    def listar_disponibles_por_producto(
        db: Session,
        producto_id: int,
        sucursal_id: int | None = None,
        talla_id: int | None = None,
        color_id: int | None = None,
        temporada_id: int | None = None,
    ) -> list[Inventario]:
        return InventarioRepository.listar_disponibles_por_producto(
            db,
            producto_id,
            sucursal_id=sucursal_id,
            talla_id=talla_id,
            color_id=color_id,
            temporada_id=temporada_id,
        )

    # ------------------------------------------------------------------
    # CU13 - Consultar inventario por sucursal
    # ------------------------------------------------------------------

    @staticmethod
    def resolver_alcance(usuario: Usuario) -> int | None:
        """Sucursal efectiva permitida (None = todas, solo Administrador)."""
        return _sucursal_scope(usuario)

    @staticmethod
    def validar_alcance_sucursal(usuario: Usuario, sucursal_id: int) -> None:
        scope = _sucursal_scope(usuario)
        if scope is not None and scope != sucursal_id:
            raise SucursalScopeError()

    @staticmethod
    def resolver_sucursal_consulta(
        db: Session, usuario: Usuario, sucursal_id: int
    ) -> int:
        """Valida alcance y existencia de la sucursal para endpoints por sucursal."""
        scope = _sucursal_scope(usuario)
        if scope is not None:
            if scope != sucursal_id:
                raise SucursalScopeError()
            return scope
        if InventarioRepository.obtener_sucursal_activa(db, sucursal_id) is None:
            raise SucursalNoEncontradaError()
        return sucursal_id

    @staticmethod
    def _normalizar_disponibilidad(disponibilidad) -> str | None:
        if disponibilidad is None:
            return None
        valor = str(getattr(disponibilidad, "value", disponibilidad)).strip().upper()
        if valor not in DISPONIBILIDADES_VALIDAS:
            raise DisponibilidadInvalidaError()
        return None if valor == DisponibilidadFiltro.TODOS.value else valor

    @staticmethod
    def consultar_inventario(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        producto: str | None = None,
        producto_id: int | None = None,
        categoria_id: int | None = None,
        talla_id: int | None = None,
        color_id: int | None = None,
        temporada_id: int | None = None,
        disponibilidad: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> InventarioConsultaResponse:
        """CU13: consulta paginada y filtrada de inventario por sucursal.

        El alcance se determina en el backend: Administrador consulta todas las
        sucursales; los demas roles autorizados solo su propia sucursal.
        """
        disponibilidad = InventarioService._normalizar_disponibilidad(
            disponibilidad
        )

        scope = _sucursal_scope(usuario)
        if scope is not None:
            if sucursal_id is not None and sucursal_id != scope:
                raise SucursalScopeError()
            sucursal_id = scope
        elif sucursal_id is not None:
            if InventarioRepository.obtener_sucursal_activa(db, sucursal_id) is None:
                raise SucursalNoEncontradaError()

        inventarios, total = InventarioRepository.consultar(
            db,
            sucursal_id=sucursal_id,
            producto=producto,
            producto_id=producto_id,
            categoria_id=categoria_id,
            talla_id=talla_id,
            color_id=color_id,
            temporada_id=temporada_id,
            disponibilidad=disponibilidad,
            limit=limit,
            offset=offset,
        )

        return InventarioConsultaResponse(
            items=[_construir_item(inventario) for inventario in inventarios],
            total=total,
            limit=limit,
            offset=offset,
        )

    # ------------------------------------------------------------------
    # CU14 - Consultar movimientos de inventario
    # ------------------------------------------------------------------

    @staticmethod
    def _normalizar_tipo_movimiento(tipo) -> str | None:
        if tipo is None:
            return None
        valor = str(getattr(tipo, "value", tipo)).strip().upper()
        if valor not in TIPOS_MOVIMIENTO_VALIDOS:
            raise TipoMovimientoInvalidoError()
        return valor

    @staticmethod
    def consultar_movimientos_inventario(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        tipo: str | None = None,
        producto: str | None = None,
        producto_id: int | None = None,
        variante_producto_id: int | None = None,
        temporada_id: int | None = None,
        usuario_id: int | None = None,
        referencia_tipo: str | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> MovimientoInventarioConsultaResponse:
        """CU14: Kardex de inventario paginado y filtrado (solo lectura).

        Alcance en backend: Administrador consulta todas las sucursales; los
        demas roles autorizados unicamente su propia sucursal (empleado activo).
        """
        tipo = InventarioService._normalizar_tipo_movimiento(tipo)
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)

        scope = _sucursal_scope(usuario)
        if scope is not None:
            if sucursal_id is not None and sucursal_id != scope:
                raise SucursalScopeError()
            sucursal_id = scope
        elif sucursal_id is not None:
            if InventarioRepository.obtener_sucursal_activa(db, sucursal_id) is None:
                raise SucursalNoEncontradaError()

        filas, total = InventarioRepository.consultar_movimientos(
            db,
            sucursal_id=sucursal_id,
            tipo=tipo,
            producto=producto,
            producto_id=producto_id,
            variante_producto_id=variante_producto_id,
            temporada_id=temporada_id,
            usuario_id=usuario_id,
            referencia_tipo=referencia_tipo,
            fecha_desde=desde,
            fecha_hasta=hasta,
            limit=limit,
            offset=offset,
        )

        return MovimientoInventarioConsultaResponse(
            items=[_construir_movimiento_item(fila) for fila in filas],
            total=total,
            limit=limit,
            offset=offset,
        )

    # ------------------------------------------------------------------
    # CU08 SaaS - adaptacion segura de CU13/CU14
    # ------------------------------------------------------------------

    @staticmethod
    def consultar_inventario_tenant(
        db: Session,
        tenant: TenantContext,
        **filtros,
    ) -> InventarioConsultaResponse:
        disponibilidad = InventarioService._normalizar_disponibilidad(
            filtros.pop("disponibilidad", None)
        )
        sucursal_id = _resolver_sucursal_tenant(
            db, tenant, filtros.pop("sucursal_id", None)
        )
        limit = filtros.pop("limit", 50)
        offset = filtros.pop("offset", 0)
        inventarios, total = InventarioRepository.consultar(
            db,
            empresa_id=tenant.empresa_id,
            sucursal_id=sucursal_id,
            disponibilidad=disponibilidad,
            limit=limit,
            offset=offset,
            **filtros,
        )
        return InventarioConsultaResponse(
            items=[_construir_item(item) for item in inventarios],
            total=total,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def consultar_movimientos_tenant(
        db: Session,
        tenant: TenantContext,
        **filtros,
    ) -> MovimientoInventarioConsultaResponse:
        tipo = InventarioService._normalizar_tipo_movimiento(
            filtros.pop("tipo", None)
        )
        desde, hasta = _rango_fechas(
            filtros.pop("fecha_desde", None), filtros.pop("fecha_hasta", None)
        )
        sucursal_id = _resolver_sucursal_tenant(
            db, tenant, filtros.pop("sucursal_id", None)
        )
        limit = filtros.pop("limit", 10)
        offset = filtros.pop("offset", 0)
        filas, total = InventarioRepository.consultar_movimientos(
            db,
            empresa_id=tenant.empresa_id,
            sucursal_id=sucursal_id,
            tipo=tipo,
            fecha_desde=desde,
            fecha_hasta=hasta,
            limit=limit,
            offset=offset,
            **filtros,
        )
        return MovimientoInventarioConsultaResponse(
            items=[_construir_movimiento_item(fila) for fila in filas],
            total=total,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def filtros_tenant(
        db: Session, tenant: TenantContext
    ) -> InventarioFiltrosResponse:
        sucursal_id = _resolver_sucursal_tenant(db, tenant, None)
        return InventarioFiltrosResponse.model_validate(
            InventarioRepository.listar_filtros_tenant(
                db, empresa_id=tenant.empresa_id, sucursal_id=sucursal_id
            )
        )

