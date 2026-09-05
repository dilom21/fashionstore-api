from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import (
    Bitacora,
    Rol,
    Usuario,
)


@dataclass
class BitacoraRegistro:
    """Fila de bitacora junto con la proyeccion segura de su usuario.

    Se construye con una unica consulta (outerjoin Usuario y Rol), evitando
    N+1 sin duplicar modelos ni definir relaciones nuevas.
    """

    id: int
    fecha_hora: datetime
    ip: str | None
    accion: str
    entidad_afectada: str | None
    descripcion: str | None
    usuario_id: int | None
    usuario_correo: str | None
    rol_nombre: str | None


def _proyeccion_columnas() -> tuple:
    return (
        Bitacora.id,
        Bitacora.fecha_hora,
        Bitacora.ip,
        Bitacora.accion,
        Bitacora.entidad_afectada,
        Bitacora.descripcion,
        Usuario.id,
        Usuario.correo,
        Rol.nombre,
    )


def _con_joins_de_usuario(statement):
    return statement.join(
        Usuario, Usuario.id == Bitacora.usuario_id, isouter=True
    ).join(Rol, Rol.id == Usuario.rol_id, isouter=True)


def _condiciones(
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
        condiciones.append(Bitacora.usuario_id == usuario_id)

    if accion:
        condiciones.append(Bitacora.accion == accion.strip())

    if entidad_afectada:
        condiciones.append(Bitacora.entidad_afectada == entidad_afectada.strip())

    if fecha_desde is not None:
        condiciones.append(Bitacora.fecha_hora >= fecha_desde)

    if fecha_hasta is not None:
        condiciones.append(Bitacora.fecha_hora <= fecha_hasta)

    if buscar:
        patron = f"%{buscar.strip().lower()}%"
        condiciones.append(
            or_(
                func.lower(Bitacora.descripcion).ilike(patron),
                func.lower(Bitacora.accion).ilike(patron),
                func.lower(Bitacora.entidad_afectada).ilike(patron),
                func.lower(Usuario.correo).ilike(patron),
            )
        )

    return condiciones


class BitacoraRepository:
    """Acceso de solo lectura a la tabla bitacora."""

    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None,
        usuario_id: int | None,
        accion: str | None,
        entidad_afectada: str | None,
        fecha_desde: datetime | None,
        fecha_hasta: datetime | None,
        limit: int,
        offset: int,
    ) -> list[BitacoraRegistro]:
        statement = select(*_proyeccion_columnas()).select_from(Bitacora)
        statement = _con_joins_de_usuario(statement)
        condiciones = _condiciones(
            buscar=buscar,
            usuario_id=usuario_id,
            accion=accion,
            entidad_afectada=entidad_afectada,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
        if condiciones:
            statement = statement.where(*condiciones)
        statement = statement.order_by(
            Bitacora.fecha_hora.desc(), Bitacora.id.desc()
        )
        statement = statement.offset(offset).limit(limit)

        filas = db.execute(statement).all()
        return [BitacoraRegistro(*fila) for fila in filas]

    @staticmethod
    def contar(
        db: Session,
        *,
        buscar: str | None,
        usuario_id: int | None,
        accion: str | None,
        entidad_afectada: str | None,
        fecha_desde: datetime | None,
        fecha_hasta: datetime | None,
    ) -> int:
        statement = select(func.count()).select_from(Bitacora)
        statement = _con_joins_de_usuario(statement)
        condiciones = _condiciones(
            buscar=buscar,
            usuario_id=usuario_id,
            accion=accion,
            entidad_afectada=entidad_afectada,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
        if condiciones:
            statement = statement.where(*condiciones)
        return int(db.scalar(statement) or 0)

    @staticmethod
    def obtener_por_id(db: Session, bitacora_id: int) -> BitacoraRegistro | None:
        statement = select(*_proyeccion_columnas()).select_from(Bitacora)
        statement = _con_joins_de_usuario(statement)
        statement = statement.where(Bitacora.id == bitacora_id)
        fila = db.execute(statement).first()
        if fila is None:
            return None
        return BitacoraRegistro(*fila)

    @staticmethod
    def listar_acciones(db: Session) -> list[str]:
        statement = (
            select(Bitacora.accion)
            .where(Bitacora.accion.is_not(None))
            .distinct()
            .order_by(Bitacora.accion)
        )
        return [accion for accion in db.scalars(statement).all() if accion]

    @staticmethod
    def listar_entidades(db: Session) -> list[str]:
        statement = (
            select(Bitacora.entidad_afectada)
            .where(Bitacora.entidad_afectada.is_not(None))
            .distinct()
            .order_by(Bitacora.entidad_afectada)
        )
        return [
            entidad
            for entidad in db.scalars(statement).all()
            if entidad and entidad.strip()
        ]

    @staticmethod
    def listar_usuarios_para_filtro(
        db: Session,
    ) -> list[tuple[int, str]]:
        """Usuarios con eventos en bitacora, seguros para el filtro.

        Solo id y correo; no expone la tabla completa de usuarios.
        """
        statement = (
            select(Bitacora.usuario_id, Usuario.correo)
            .join(Usuario, Usuario.id == Bitacora.usuario_id)
            .where(Bitacora.usuario_id.is_not(None))
            .distinct()
            .order_by(Usuario.correo)
        )
        filas = db.execute(statement).all()
        return [
            (usuario_id, correo) for usuario_id, correo in filas
        ]
