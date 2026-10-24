"""Lecturas administrativas comerciales aisladas por tenant (B3/P5).

No ejecuta procedimientos almacenados ni operaciones que cambien stock,
reservas, ventas o pagos. Una nueva operacion comercial debe reutilizar
estas comprobaciones de pertenencia, NO invocar handlers legacy.
"""
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.carrito.models.models import Carrito
from app.modules.compras.models.models import OrdenCompra
from app.modules.devoluciones.models.models import Devolucion
from app.modules.pagos.models.models import Pago
from app.modules.proveedores.models.models import Proveedor
from app.modules.reservas.models.models import Reserva
from app.modules.sucursales.models.models import Sucursal
from app.modules.ventas.models.models import Venta


def _igual_tenant(tabla, empresa_id: int):
    return tabla.c.empresa_id == empresa_id


# Los padres se expresan como (tabla, columna FK de la hija): nunca
# se usa una FK suministrada por el request para inferir empresa.
_MAPA_COMERCIAL = {
    "carritos": (
        Carrito.__table__,
        ((Cliente.__table__, "cliente_id"), (Sucursal.__table__, "sucursal_id")),
    ),
    "reservas": (
        Reserva.__table__,
        ((Cliente.__table__, "cliente_id"), (Sucursal.__table__, "sucursal_id")),
    ),
    "ventas": (
        Venta.__table__,
        ((Sucursal.__table__, "sucursal_id"),),
    ),
    "pagos": (
        Pago.__table__,
        ((Venta.__table__, "venta_id"),),
    ),
    "compras": (
        OrdenCompra.__table__,
        ((Proveedor.__table__, "proveedor_id"), (Sucursal.__table__, "sucursal_id")),
    ),
    "devoluciones": (
        Devolucion.__table__,
        ((Venta.__table__, "venta_id"),),
    ),
}


class TenantComercialRepository:
    """Consultas sin objetos ORM cargados, sin permisos de modificación."""

    @staticmethod
    def _validar(empresa_id: int, tipo: str) -> tuple:
        if isinstance(empresa_id, bool) or not isinstance(empresa_id, int) or empresa_id <= 0:
            raise ValueError("Es obligatorio un contexto empresarial positivo")
        if tipo not in _MAPA_COMERCIAL:
            raise ValueError("Tipo comercial no autorizado")
        return _MAPA_COMERCIAL[tipo]

    @classmethod
    def _consulta(cls, *, empresa_id: int, tipo: str):
        tabla, padres = cls._validar(empresa_id, tipo)
        stmt = select(
            tabla.c.id,
            tabla.c.estado,
        )
        for padre, fk in padres:
            stmt = stmt.join(
                padre,
                and_(
                    padre.c.id == tabla.c[fk],
                    _igual_tenant(padre, empresa_id),
                ),
            )
        return stmt.where(_igual_tenant(tabla, empresa_id))

    @classmethod
    def listar(
        cls, db: Session, *, empresa_id: int, tipo: str,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        if isinstance(limit, bool) or isinstance(offset, bool) or not (1 <= limit <= 100) or offset < 0:
            raise ValueError("Paginacion comercial invalida")
        stmt = cls._consulta(empresa_id=empresa_id, tipo=tipo)
        stmt = stmt.order_by(_MAPA_COMERCIAL[tipo][0].c.id).limit(limit).offset(offset)
        return [dict(x) for x in db.execute(stmt).mappings().all()]

    @classmethod
    def obtener(
        cls, db: Session, *, empresa_id: int, tipo: str, recurso_id: int,
    ) -> dict | None:
        if isinstance(recurso_id, bool) or not isinstance(recurso_id, int) or recurso_id <= 0:
            raise ValueError("Identificador de recurso invalido")
        tabla, _ = cls._validar(empresa_id, tipo)
        stmt = cls._consulta(empresa_id=empresa_id, tipo=tipo).where(
            tabla.c.id == recurso_id
        )
        fila = db.execute(stmt).mappings().one_or_none()
        return dict(fila) if fila is not None else None
