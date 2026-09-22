"""Acceso a datos de CU26 - Vestidor Virtual.

Solo lectura/escritura de las tablas AR y de las entidades de catalogo que
necesita validar (producto, variante, recurso). No define reglas de negocio:
esas viven en ``services/service.py``.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.modules.catalogo.models.models import (
    Producto,
    RecursoProducto,
    VarianteProducto,
)
from app.modules.vestidor_virtual.models.models import (
    ConfiguracionVestidorAR,
    PruebaVestidorAR,
    SesionVestidorAR,
)

ESTADO_SESION_ACTIVA = "ACTIVA"
TIPO_ASSET_PNG_2D = "PNG_2D"
ZONA_CUERPO_TORSO = "TORSO"


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class VestidorRepository:
    # ------------------------------------------------------------------
    # Catalogo (validaciones)
    # ------------------------------------------------------------------

    @staticmethod
    def obtener_producto(db: Session, producto_id: int) -> Producto | None:
        """Producto por id sin filtrar por estado (el 404 se decide en service)."""
        return db.get(Producto, producto_id)

    @staticmethod
    def obtener_variante(
        db: Session, variante_id: int
    ) -> VarianteProducto | None:
        return db.get(VarianteProducto, variante_id)

    # ------------------------------------------------------------------
    # Configuraciones AR
    # ------------------------------------------------------------------

    @staticmethod
    def listar_configuraciones_activas(
        db: Session,
        *,
        producto_id: int,
        color_id: int | None = None,
    ) -> list[ConfiguracionVestidorAR]:
        """Configuraciones activas PNG_2D de torso de un producto.

        Exige recurso activo y perteneciente al producto solicitado. Si
        ``color_id`` viene informado, solo devuelve configuraciones de recursos
        de ese color.
        """
        statement = (
            select(ConfiguracionVestidorAR)
            .join(
                RecursoProducto,
                ConfiguracionVestidorAR.recurso_producto_id
                == RecursoProducto.id,
            )
            .options(
                joinedload(ConfiguracionVestidorAR.recurso).joinedload(
                    RecursoProducto.color
                )
            )
            .where(
                RecursoProducto.producto_id == producto_id,
                RecursoProducto.estado.is_(True),
                ConfiguracionVestidorAR.estado.is_(True),
                ConfiguracionVestidorAR.tipo_asset == TIPO_ASSET_PNG_2D,
                ConfiguracionVestidorAR.zona_cuerpo == ZONA_CUERPO_TORSO,
            )
            .order_by(
                ConfiguracionVestidorAR.orden_capa,
                ConfiguracionVestidorAR.id,
            )
        )
        if color_id is not None:
            statement = statement.where(RecursoProducto.color_id == color_id)
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_configuracion(
        db: Session, configuracion_id: int
    ) -> ConfiguracionVestidorAR | None:
        statement = (
            select(ConfiguracionVestidorAR)
            .options(joinedload(ConfiguracionVestidorAR.recurso))
            .where(ConfiguracionVestidorAR.id == configuracion_id)
        )
        return db.scalar(statement)

    # ------------------------------------------------------------------
    # Sesiones
    # ------------------------------------------------------------------

    @staticmethod
    def obtener_sesion(
        db: Session, sesion_id: int
    ) -> SesionVestidorAR | None:
        return db.get(SesionVestidorAR, sesion_id)

    @staticmethod
    def obtener_sesion_activa(
        db: Session, cliente_id: int
    ) -> SesionVestidorAR | None:
        statement = (
            select(SesionVestidorAR)
            .where(
                SesionVestidorAR.cliente_id == cliente_id,
                SesionVestidorAR.estado == ESTADO_SESION_ACTIVA,
            )
            .order_by(SesionVestidorAR.id.desc())
            .limit(1)
        )
        return db.scalar(statement)

    @staticmethod
    def crear_sesion(db: Session, cliente_id: int) -> SesionVestidorAR:
        sesion = SesionVestidorAR(
            cliente_id=cliente_id,
            fecha_inicio=_ahora(),
            estado=ESTADO_SESION_ACTIVA,
        )
        db.add(sesion)
        db.flush()
        return sesion

    # ------------------------------------------------------------------
    # Pruebas
    # ------------------------------------------------------------------

    @staticmethod
    def obtener_prueba(
        db: Session, prueba_id: int
    ) -> PruebaVestidorAR | None:
        return db.get(PruebaVestidorAR, prueba_id)

    @staticmethod
    def crear_prueba(
        db: Session,
        *,
        sesion_id: int,
        configuracion_id: int,
        variante_producto_id: int | None,
    ) -> PruebaVestidorAR:
        prueba = PruebaVestidorAR(
            sesion_vestidor_ar_id=sesion_id,
            configuracion_vestidor_ar_id=configuracion_id,
            variante_producto_id=variante_producto_id,
            fecha_inicio=_ahora(),
            estado="INICIADA",
        )
        db.add(prueba)
        db.flush()
        return prueba
