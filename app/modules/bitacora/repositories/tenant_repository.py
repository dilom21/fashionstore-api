"""Tenant-safe, read-only queries for audit events."""
from datetime import datetime

from sqlalchemy import BigInteger, and_, column, func, or_, select, table
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Bitacora, Rol, Usuario


usuario_empresa = table(
    "usuario_empresa",
    column("usuario_id", BigInteger),
    column("empresa_id", BigInteger),
    column("rol_id", BigInteger),
)


class TenantBitacoraRepository:
    @staticmethod
    def _tenant_id(empresa_id: int) -> int:
        if isinstance(empresa_id, bool) or not isinstance(empresa_id, int) or empresa_id <= 0:
            raise ValueError("El contexto empresarial validado es obligatorio")
        return empresa_id

    @classmethod
    def _base(cls, empresa_id: int):
        tenant_id = cls._tenant_id(empresa_id)
        b = Bitacora.__table__
        u = Usuario.__table__
        r = Rol.__table__
        origen = (
            b.outerjoin(
                usuario_empresa,
                and_(
                    usuario_empresa.c.usuario_id == b.c.usuario_id,
                    usuario_empresa.c.empresa_id == tenant_id,
                ),
            )
            .outerjoin(u, u.c.id == usuario_empresa.c.usuario_id)
            .outerjoin(r, r.c.id == usuario_empresa.c.rol_id)
        )
        return tenant_id, b, u, r, origen

    @staticmethod
    def _condiciones(
        b,
        u,
        *,
        buscar: str | None,
        usuario_id: int | None,
        accion: str | None,
        entidad_afectada: str | None,
        fecha_desde: datetime | None,
        fecha_hasta: datetime | None,
    ) -> list:
        condiciones = []
        if usuario_id is not None:
            condiciones.append(b.c.usuario_id == usuario_id)
        if accion:
            condiciones.append(b.c.accion == accion.strip())
        if entidad_afectada:
            condiciones.append(b.c.entidad_afectada == entidad_afectada.strip())
        if fecha_desde is not None:
            condiciones.append(b.c.fecha_hora >= fecha_desde)
        if fecha_hasta is not None:
            condiciones.append(b.c.fecha_hora <= fecha_hasta)
        if buscar and buscar.strip():
            patron = f"%{buscar.strip()}%"
            condiciones.append(
                or_(
                    b.c.descripcion.ilike(patron),
                    b.c.accion.ilike(patron),
                    b.c.entidad_afectada.ilike(patron),
                    u.c.correo.ilike(patron),
                )
            )
        return condiciones

    @classmethod
    def listar(cls, db: Session, *, empresa_id: int, limit: int, offset: int, **filtros):
        tenant_id, b, u, r, origen = cls._base(empresa_id)
        condiciones = cls._condiciones(b, u, **filtros)
        statement = (
            select(
                b.c.id,
                b.c.fecha_hora,
                b.c.ip,
                b.c.accion,
                b.c.entidad_afectada,
                b.c.descripcion,
                b.c.usuario_id,
                u.c.correo.label("usuario_correo"),
                r.c.nombre.label("rol_nombre"),
            )
            .select_from(origen)
            .where(b.c.empresa_id == tenant_id, *condiciones)
            .order_by(b.c.fecha_hora.desc(), b.c.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [dict(fila) for fila in db.execute(statement).mappings().all()]

    @classmethod
    def contar(cls, db: Session, *, empresa_id: int, **filtros) -> int:
        tenant_id, b, u, _r, origen = cls._base(empresa_id)
        statement = (
            select(func.count())
            .select_from(origen)
            .where(b.c.empresa_id == tenant_id, *cls._condiciones(b, u, **filtros))
        )
        return int(db.scalar(statement) or 0)

    @classmethod
    def obtener(cls, db: Session, *, empresa_id: int, bitacora_id: int):
        tenant_id, b, u, r, origen = cls._base(empresa_id)
        statement = (
            select(
                b.c.id,
                b.c.fecha_hora,
                b.c.ip,
                b.c.accion,
                b.c.entidad_afectada,
                b.c.descripcion,
                b.c.usuario_id,
                u.c.correo.label("usuario_correo"),
                r.c.nombre.label("rol_nombre"),
            )
            .select_from(origen)
            .where(b.c.empresa_id == tenant_id, b.c.id == bitacora_id)
        )
        fila = db.execute(statement).mappings().first()
        return dict(fila) if fila is not None else None

    @classmethod
    def catalogos(cls, db: Session, *, empresa_id: int) -> dict:
        tenant_id, b, u, _r, origen = cls._base(empresa_id)
        acciones = db.scalars(
            select(b.c.accion)
            .where(b.c.empresa_id == tenant_id, b.c.accion.is_not(None))
            .distinct()
            .order_by(b.c.accion)
        ).all()
        entidades = db.scalars(
            select(b.c.entidad_afectada)
            .where(b.c.empresa_id == tenant_id, b.c.entidad_afectada.is_not(None))
            .distinct()
            .order_by(b.c.entidad_afectada)
        ).all()
        usuarios = db.execute(
            select(b.c.usuario_id.label("id"), u.c.correo)
            .select_from(origen)
            .where(
                b.c.empresa_id == tenant_id,
                b.c.usuario_id.is_not(None),
                u.c.correo.is_not(None),
            )
            .distinct()
            .order_by(u.c.correo)
        ).mappings().all()
        return {
            "acciones": [valor for valor in acciones if valor],
            "entidades": [valor for valor in entidades if valor and valor.strip()],
            "usuarios": [dict(fila) for fila in usuarios],
        }
