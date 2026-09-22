"""Reglas de negocio de CU26 - Vestidor Virtual (AR).

Arquitectura: Router -> Service -> Repository -> SQLAlchemy.

Responsabilidades:
- Exponer configuraciones AR compatibles de productos reales.
- Crear/reutilizar sesiones de vestidor del CLIENTE autenticado.
- Registrar y finalizar pruebas de prendas.

El motor AR (CameraX + MediaPipe) es 100% local en el telefono: este servicio
nunca recibe landmarks, frames ni imagenes de camara.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.vestidor_virtual.models.models import (
    ConfiguracionVestidorAR,
    PruebaVestidorAR,
    SesionVestidorAR,
)
from app.modules.vestidor_virtual.repositories.repository import (
    ESTADO_SESION_ACTIVA,
    TIPO_ASSET_PNG_2D,
    VestidorRepository,
)
from app.modules.vestidor_virtual.schemas.schemas import (
    ConfiguracionARResponse,
    ConfiguracionesProductoResponse,
    IniciarPruebaRequest,
    PruebaARResponse,
    SesionARResponse,
)

ESTADOS_FINALES_PRUEBA = frozenset({"COMPLETADA", "CANCELADA", "ERROR"})
ESTADOS_FINALES_SESION = frozenset({"FINALIZADA", "CANCELADA"})


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Errores tipados (mapeados a HTTP en el router)
# ---------------------------------------------------------------------------


class VestidorError(Exception):
    """Base de errores de negocio de CU26."""


class ProductoNoEncontradoError(VestidorError):
    pass


class ConfiguracionNoEncontradaError(VestidorError):
    pass


class ConfiguracionNoActivaError(VestidorError):
    """Configuracion inactiva, de otro tipo de asset o con recurso inactivo."""


class SesionNoEncontradaError(VestidorError):
    pass


class SesionAjenaError(VestidorError):
    """La sesion pertenece a otro cliente."""


class SesionNoActivaError(VestidorError):
    pass


class PruebaNoEncontradaError(VestidorError):
    pass


class PruebaAjenaError(VestidorError):
    """La prueba pertenece a la sesion de otro cliente."""


class VarianteIncompatibleError(VestidorError):
    """La variante no pertenece al producto del recurso configurado."""


class EstadoInvalidoError(VestidorError):
    """Transicion final incompatible con el estado actual."""


class RegistroInvalidoError(VestidorError):
    """IntegrityError no recuperable."""


# ---------------------------------------------------------------------------
# Serializacion
# ---------------------------------------------------------------------------


def _serializar_configuracion(
    producto_id: int, configuracion: ConfiguracionVestidorAR
) -> ConfiguracionARResponse:
    recurso = configuracion.recurso
    return ConfiguracionARResponse(
        configuracion_id=configuracion.id,
        recurso_producto_id=configuracion.recurso_producto_id,
        asset_url=recurso.url,
        color_id=recurso.color_id,
        color=recurso.color.nombre if recurso.color is not None else None,
        zona_cuerpo=configuracion.zona_cuerpo,
        tipo_asset=configuracion.tipo_asset,
        factor_ancho=Decimal(configuracion.factor_ancho),
        factor_alto=Decimal(configuracion.factor_alto),
        offset_x=Decimal(configuracion.offset_x),
        offset_y=Decimal(configuracion.offset_y),
        rotacion_offset=Decimal(configuracion.rotacion_offset),
        orden_capa=configuracion.orden_capa,
        opacidad=Decimal(configuracion.opacidad),
    )


def _serializar_sesion(sesion: SesionVestidorAR) -> SesionARResponse:
    return SesionARResponse(
        sesion_id=sesion.id,
        cliente_id=sesion.cliente_id,
        estado=sesion.estado,
        fecha_inicio=sesion.fecha_inicio,
        fecha_fin=sesion.fecha_fin,
    )


def _serializar_prueba(prueba: PruebaVestidorAR) -> PruebaARResponse:
    return PruebaARResponse(
        prueba_id=prueba.id,
        sesion_vestidor_ar_id=prueba.sesion_vestidor_ar_id,
        configuracion_id=prueba.configuracion_vestidor_ar_id,
        variante_producto_id=prueba.variante_producto_id,
        estado=prueba.estado,
        fecha_inicio=prueba.fecha_inicio,
        fecha_fin=prueba.fecha_fin,
    )


class VestidorVirtualService:
    """CU26 - Vestidor virtual (CLIENTE)."""

    # ------------------------------------------------------------------
    # A. Configuraciones compatibles
    # ------------------------------------------------------------------

    @staticmethod
    def obtener_configuraciones(
        db: Session,
        producto_id: int,
        *,
        variante_id: int | None = None,
        color_id: int | None = None,
    ) -> ConfiguracionesProductoResponse:
        """Configuraciones AR activas de un producto.

        - Producto inexistente -> ProductoNoEncontradoError (404).
        - Producto existente sin configuraciones -> compatible=False, [].
        - Filtro opcional por color o por variante (su color).
        """
        producto = VestidorRepository.obtener_producto(db, producto_id)
        if producto is None:
            raise ProductoNoEncontradoError()

        color_filtro = color_id
        if variante_id is not None:
            variante = VestidorRepository.obtener_variante(db, variante_id)
            if variante is None or variante.producto_id != producto_id:
                return ConfiguracionesProductoResponse(
                    producto_id=producto_id,
                    compatible=False,
                    configuraciones=[],
                )
            color_filtro = variante.color_id

        if not producto.estado:
            return ConfiguracionesProductoResponse(
                producto_id=producto_id,
                compatible=False,
                configuraciones=[],
            )

        configuraciones = VestidorRepository.listar_configuraciones_activas(
            db, producto_id=producto_id, color_id=color_filtro
        )
        items = [
            _serializar_configuracion(producto_id, configuracion)
            for configuracion in configuraciones
        ]
        return ConfiguracionesProductoResponse(
            producto_id=producto_id,
            compatible=bool(items),
            configuraciones=items,
        )

    # ------------------------------------------------------------------
    # B. Crear sesion
    # ------------------------------------------------------------------

    @staticmethod
    def crear_sesion(db: Session, cliente) -> SesionARResponse:
        """Crea (o reutiliza) la sesion ACTIVA del cliente autenticado.

        Politica idempotente: si el cliente ya tiene una sesion ACTIVA se
        devuelve esa misma, evitando acumular sesiones activas innecesarias.
        ``cliente_id`` y ``fecha_inicio`` NUNCA vienen del body.
        """
        existente = VestidorRepository.obtener_sesion_activa(db, cliente.id)
        if existente is not None:
            return _serializar_sesion(existente)

        try:
            sesion = VestidorRepository.crear_sesion(db, cliente.id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise RegistroInvalidoError() from exc

        sesion = VestidorRepository.obtener_sesion(db, sesion.id)
        if sesion is None:
            raise SesionNoEncontradaError()
        return _serializar_sesion(sesion)

    # ------------------------------------------------------------------
    # C. Registrar inicio de prueba
    # ------------------------------------------------------------------

    @staticmethod
    def iniciar_prueba(
        db: Session,
        cliente,
        sesion_id: int,
        datos: IniciarPruebaRequest,
    ) -> PruebaARResponse:
        sesion = VestidorRepository.obtener_sesion(db, sesion_id)
        if sesion is None:
            raise SesionNoEncontradaError()
        if sesion.cliente_id != cliente.id:
            raise SesionAjenaError()
        if sesion.estado != ESTADO_SESION_ACTIVA:
            raise SesionNoActivaError()

        configuracion = VestidorRepository.obtener_configuracion(
            db, datos.configuracion_id
        )
        if configuracion is None:
            raise ConfiguracionNoEncontradaError()
        if not configuracion.estado or configuracion.tipo_asset != TIPO_ASSET_PNG_2D:
            raise ConfiguracionNoActivaError()

        recurso = configuracion.recurso
        if recurso is None or not recurso.estado:
            raise ConfiguracionNoActivaError()

        if datos.variante_producto_id is not None:
            variante = VestidorRepository.obtener_variante(
                db, datos.variante_producto_id
            )
            if variante is None or variante.producto_id != recurso.producto_id:
                raise VarianteIncompatibleError()

        try:
            prueba = VestidorRepository.crear_prueba(
                db,
                sesion_id=sesion.id,
                configuracion_id=configuracion.id,
                variante_producto_id=datos.variante_producto_id,
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise RegistroInvalidoError() from exc

        prueba = VestidorRepository.obtener_prueba(db, prueba.id)
        if prueba is None:
            raise PruebaNoEncontradaError()
        return _serializar_prueba(prueba)

    # ------------------------------------------------------------------
    # D. Finalizar prueba
    # ------------------------------------------------------------------

    @staticmethod
    def finalizar_prueba(
        db: Session, cliente, prueba_id: int, estado: str
    ) -> PruebaARResponse:
        if estado not in ESTADOS_FINALES_PRUEBA:
            raise EstadoInvalidoError()

        prueba = VestidorRepository.obtener_prueba(db, prueba_id)
        if prueba is None:
            raise PruebaNoEncontradaError()

        sesion = VestidorRepository.obtener_sesion(
            db, prueba.sesion_vestidor_ar_id
        )
        if sesion is None:
            raise PruebaNoEncontradaError()
        if sesion.cliente_id != cliente.id:
            raise PruebaAjenaError()

        if prueba.estado in ESTADOS_FINALES_PRUEBA:
            if prueba.estado == estado:
                return _serializar_prueba(prueba)
            raise EstadoInvalidoError()

        prueba.estado = estado
        prueba.fecha_fin = _ahora()
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise RegistroInvalidoError() from exc

        prueba = VestidorRepository.obtener_prueba(db, prueba.id)
        if prueba is None:
            raise PruebaNoEncontradaError()
        return _serializar_prueba(prueba)

    # ------------------------------------------------------------------
    # E. Finalizar sesion
    # ------------------------------------------------------------------

    @staticmethod
    def finalizar_sesion(
        db: Session, cliente, sesion_id: int, estado: str
    ) -> SesionARResponse:
        if estado not in ESTADOS_FINALES_SESION:
            raise EstadoInvalidoError()

        sesion = VestidorRepository.obtener_sesion(db, sesion_id)
        if sesion is None:
            raise SesionNoEncontradaError()
        if sesion.cliente_id != cliente.id:
            raise SesionAjenaError()

        if sesion.estado in ESTADOS_FINALES_SESION:
            if sesion.estado == estado:
                return _serializar_sesion(sesion)
            raise EstadoInvalidoError()

        sesion.estado = estado
        sesion.fecha_fin = _ahora()
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise RegistroInvalidoError() from exc

        sesion = VestidorRepository.obtener_sesion(db, sesion.id)
        if sesion is None:
            raise SesionNoEncontradaError()
        return _serializar_sesion(sesion)
