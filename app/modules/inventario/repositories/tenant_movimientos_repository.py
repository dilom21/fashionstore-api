"""Tenant-safe, read-only inventory movement queries."""
from datetime import datetime

from sqlalchemy import BigInteger, and_, column, func, select, table
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Empleado, Usuario
from app.modules.catalogo.models.models import Color, Producto, Talla, Temporada, VarianteProducto
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal


usuario_empresa = table(
    "usuario_empresa",
    column("usuario_id", BigInteger),
    column("empresa_id", BigInteger),
)


class TenantMovimientosRepository:
    @staticmethod
    def _tenant_id(empresa_id: int) -> int:
        if isinstance(empresa_id, bool) or not isinstance(empresa_id, int) or empresa_id <= 0:
            raise ValueError("El contexto empresarial validado es obligatorio")
        return empresa_id

    @classmethod
    def sucursal_del_actor(cls, db: Session, *, empresa_id: int, usuario_id: int) -> int | None:
        tenant_id = cls._tenant_id(empresa_id)
        e = Empleado.__table__
        return db.scalar(
            select(e.c.sucursal_id).where(
                e.c.empresa_id == tenant_id,
                e.c.usuario_id == usuario_id,
                e.c.estado.is_(True),
            )
        )

    @classmethod
    def _base(cls, empresa_id: int):
        tenant_id = cls._tenant_id(empresa_id)
        m = MovimientoInventario.__table__
        i = Inventario.__table__
        s = Sucursal.__table__
        vp = VarianteProducto.__table__
        p = Producto.__table__
        talla = Talla.__table__
        color = Color.__table__
        t = Temporada.__table__
        u = Usuario.__table__
        origen = (
            m.join(i, and_(i.c.id == m.c.inventario_id, i.c.empresa_id == tenant_id))
            .join(s, and_(s.c.id == i.c.sucursal_id, s.c.empresa_id == tenant_id))
            .join(vp, and_(vp.c.id == i.c.variante_producto_id, vp.c.empresa_id == tenant_id))
            .join(p, and_(p.c.id == vp.c.producto_id, p.c.empresa_id == tenant_id))
            .join(talla, talla.c.id == vp.c.talla_id)
            .join(color, color.c.id == vp.c.color_id)
            .join(t, and_(t.c.id == i.c.temporada_id, t.c.empresa_id == tenant_id))
            .outerjoin(
                usuario_empresa,
                and_(
                    usuario_empresa.c.usuario_id == m.c.usuario_id,
                    usuario_empresa.c.empresa_id == tenant_id,
                ),
            )
            .outerjoin(u, u.c.id == usuario_empresa.c.usuario_id)
        )
        return tenant_id, m, i, s, vp, p, talla, color, t, u, origen

    @staticmethod
    def _condiciones(
        m, i, vp, p, *, sucursal_id=None, tipo=None, producto=None,
        producto_id=None, variante_producto_id=None, temporada_id=None,
        usuario_id=None, referencia_tipo=None, fecha_desde=None, fecha_hasta=None,
    ) -> list:
        condiciones = []
        if sucursal_id is not None:
            condiciones.append(i.c.sucursal_id == sucursal_id)
        if tipo is not None:
            condiciones.append(m.c.tipo == tipo)
        if producto and producto.strip():
            condiciones.append(p.c.nombre.ilike(f"%{producto.strip()}%"))
        if producto_id is not None:
            condiciones.append(p.c.id == producto_id)
        if variante_producto_id is not None:
            condiciones.append(vp.c.id == variante_producto_id)
        if temporada_id is not None:
            condiciones.append(i.c.temporada_id == temporada_id)
        if usuario_id is not None:
            condiciones.append(m.c.usuario_id == usuario_id)
        if referencia_tipo and referencia_tipo.strip():
            condiciones.append(m.c.referencia_tipo == referencia_tipo.strip())
        if fecha_desde is not None:
            condiciones.append(m.c.fecha_hora >= fecha_desde)
        if fecha_hasta is not None:
            condiciones.append(m.c.fecha_hora <= fecha_hasta)
        return condiciones

    @classmethod
    def _statement(cls, empresa_id: int, **filtros):
        tenant_id, m, i, s, vp, p, talla, color, t, u, origen = cls._base(empresa_id)
        condiciones = cls._condiciones(m, i, vp, p, **filtros)
        statement = select(
            m.c.id.label("movimiento_id"), m.c.inventario_id, m.c.usuario_id,
            u.c.correo.label("usuario_correo"), m.c.tipo, m.c.cantidad,
            m.c.fecha_hora, m.c.observaciones, m.c.referencia_tipo,
            m.c.referencia_id, i.c.sucursal_id,
            s.c.nombre.label("sucursal_nombre"), p.c.id.label("producto_id"),
            p.c.nombre.label("producto_nombre"), vp.c.id.label("variante_producto_id"),
            vp.c.sku, talla.c.id.label("talla_id"), talla.c.nombre.label("talla_nombre"),
            color.c.id.label("color_id"), color.c.nombre.label("color_nombre"),
            t.c.id.label("temporada_id"), t.c.nombre.label("temporada_nombre"),
        ).select_from(origen).where(m.c.empresa_id == tenant_id, *condiciones)
        return statement

    @classmethod
    def listar(cls, db: Session, *, empresa_id: int, limit: int, offset: int, **filtros):
        base = cls._statement(empresa_id, **filtros)
        total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        statement = base.order_by(
            MovimientoInventario.fecha_hora.desc(), MovimientoInventario.id.desc()
        ).limit(limit).offset(offset)
        return [dict(fila) for fila in db.execute(statement).mappings().all()], total

    @classmethod
    def obtener(cls, db: Session, *, empresa_id: int, movimiento_id: int, sucursal_id: int | None):
        statement = cls._statement(empresa_id, sucursal_id=sucursal_id).where(
            MovimientoInventario.id == movimiento_id
        )
        fila = db.execute(statement).mappings().first()
        return dict(fila) if fila is not None else None
