from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import (
    Bitacora,
    Rol,
    Usuario,
    UsuarioEmpresa,
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


def _con_joins_de_usuario(statement, *, empresa_id: int | None = None):
    statement = statement.join(
        Usuario, Usuario.id == Bitacora.usuario_id, isouter=True
    )
    if empresa_id is None:
        return statement.join(Rol, Rol.id == Usuario.rol_id, isouter=True)
    # En SaaS, el rol mostrado es el de la membresia empresarial, no el
    # rol_id global heredado del primer parcial.
    return statement.join(
        UsuarioEmpresa,
        (UsuarioEmpresa.usuario_id == Usuario.id)
        & (UsuarioEmpresa.empresa_id == empresa_id)
        & (UsuarioEmpresa.estado.is_(True)),
        isouter=True,
    ).join(Rol, Rol.id == UsuarioEmpresa.rol_id, isouter=True)


def _condiciones(
    *,
    empresa_id: int | None,
    buscar: str | None,
    usuario_id: int | None,
    accion: str | None,
    entidad_afectada: str | None,
    fecha_desde: datetime | None,
    fecha_hasta: datetime | None,
) -> list:
    condiciones = []

    if empresa_id is not None:
        condiciones.append(Bitacora.empresa_id == empresa_id)

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
        empresa_id: int | None = None,
    ) -> list[BitacoraRegistro]:
        statement = select(*_proyeccion_columnas()).select_from(Bitacora)
        statement = _con_joins_de_usuario(statement, empresa_id=empresa_id)
        condiciones = _condiciones(
            empresa_id=empresa_id,
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
        empresa_id: int | None = None,
    ) -> int:
        statement = select(func.count()).select_from(Bitacora)
        statement = _con_joins_de_usuario(statement, empresa_id=empresa_id)
        condiciones = _condiciones(
            empresa_id=empresa_id,
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
    def obtener_por_id(
        db: Session, bitacora_id: int, *, empresa_id: int | None = None
    ) -> BitacoraRegistro | None:
        statement = select(*_proyeccion_columnas()).select_from(Bitacora)
        statement = _con_joins_de_usuario(statement, empresa_id=empresa_id)
        statement = statement.where(Bitacora.id == bitacora_id)
        if empresa_id is not None:
            statement = statement.where(Bitacora.empresa_id == empresa_id)
        fila = db.execute(statement).first()
        if fila is None:
            return None
        return BitacoraRegistro(*fila)

    @staticmethod
    def listar_acciones(db: Session, *, empresa_id: int | None = None) -> list[str]:
        statement = (
            select(Bitacora.accion)
            .where(Bitacora.accion.is_not(None))
            .distinct()
            .order_by(Bitacora.accion)
        )
        if empresa_id is not None:
            statement = statement.where(Bitacora.empresa_id == empresa_id)
        return [accion for accion in db.scalars(statement).all() if accion]

    @staticmethod
    def listar_entidades(db: Session, *, empresa_id: int | None = None) -> list[str]:
        statement = (
            select(Bitacora.entidad_afectada)
            .where(Bitacora.entidad_afectada.is_not(None))
            .distinct()
            .order_by(Bitacora.entidad_afectada)
        )
        if empresa_id is not None:
            statement = statement.where(Bitacora.empresa_id == empresa_id)
        return [
            entidad
            for entidad in db.scalars(statement).all()
            if entidad and entidad.strip()
        ]

    @staticmethod
    def listar_usuarios_para_filtro(
        db: Session,
        *,
        empresa_id: int | None = None,
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
        if empresa_id is not None:
            statement = statement.where(Bitacora.empresa_id == empresa_id)
        filas = db.execute(statement).all()
        return [
            (usuario_id, correo) for usuario_id, correo in filas
        ]
