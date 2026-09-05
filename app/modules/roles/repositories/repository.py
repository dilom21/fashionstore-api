from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import (
    Accion,
    Bitacora,
    Funcion,
    Modulo,
    Rol,
    RolFuncion,
    Usuario,
)


class RolRepository:
    @staticmethod
    def listar(db: Session, estado: bool | None = None) -> list[Rol]:
        statement = select(Rol)
        if estado is not None:
            statement = statement.where(Rol.estado.is_(estado))
        statement = statement.order_by(Rol.id)
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, rol_id: int) -> Rol | None:
        return db.get(Rol, rol_id)

    @staticmethod
    def obtener_por_nombre(db: Session, nombre: str) -> Rol | None:
        statement = select(Rol).where(Rol.nombre == nombre.strip().upper())
        return db.scalar(statement)

    @staticmethod
    def crear(db: Session, nombre: str, descripcion: str | None) -> Rol:
        rol = Rol(nombre=nombre, descripcion=descripcion, estado=True)
        db.add(rol)
        db.flush()
        return rol

    @staticmethod
    def contar_usuarios_activos(db: Session, rol_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(Usuario)
            .where(Usuario.rol_id == rol_id, Usuario.estado.is_(True))
        )
        return int(db.scalar(statement) or 0)

    @staticmethod
    def obtener_catalogo_activo(
        db: Session,
    ) -> tuple[list[Modulo], list[Funcion], list[Accion]]:
        modulos = list(
            db.scalars(
                select(Modulo).where(Modulo.estado.is_(True)).order_by(Modulo.id)
            ).all()
        )
        funciones = list(
            db.scalars(
                select(Funcion).where(Funcion.estado.is_(True)).order_by(Funcion.id)
            ).all()
        )
        acciones = list(
            db.scalars(
                select(Accion).where(Accion.estado.is_(True)).order_by(Accion.id)
            ).all()
        )
        return modulos, funciones, acciones

    @staticmethod
    def listar_permisos(db: Session, rol_id: int) -> list[tuple[int, int]]:
        statement = select(RolFuncion.funcion_id, RolFuncion.accion_id).where(
            RolFuncion.rol_id == rol_id
        )
        filas = db.execute(statement).all()
        return [(funcion_id, accion_id) for funcion_id, accion_id in filas]

    @staticmethod
    def reemplazar_permisos(
        db: Session, rol_id: int, pares: list[tuple[int, int]]
    ) -> None:
        db.execute(delete(RolFuncion).where(RolFuncion.rol_id == rol_id))
        for funcion_id, accion_id in pares:
            db.add(
                RolFuncion(
                    rol_id=rol_id,
                    funcion_id=funcion_id,
                    accion_id=accion_id,
                )
            )
        db.flush()

    @staticmethod
    def tiene_permiso(
        db: Session,
        rol_id: int,
        nombre_funcion: str,
        nombre_accion: str,
    ) -> bool:
        statement = (
            select(func.count())
            .select_from(RolFuncion)
            .join(Rol, Rol.id == RolFuncion.rol_id)
            .join(Funcion, Funcion.id == RolFuncion.funcion_id)
            .join(Modulo, Modulo.id == Funcion.modulo_id)
            .join(Accion, Accion.id == RolFuncion.accion_id)
            .where(
                Rol.id == rol_id,
                Rol.estado.is_(True),
                Funcion.estado.is_(True),
                Modulo.estado.is_(True),
                Accion.estado.is_(True),
                func.upper(Funcion.nombre) == nombre_funcion.strip().upper(),
                func.upper(Accion.nombre) == nombre_accion.strip().upper(),
            )
        )
        return int(db.scalar(statement) or 0) > 0

    @staticmethod
    def registrar_evento_bitacora(
        db: Session,
        *,
        usuario_id: int,
        accion: str,
        entidad_afectada: str,
        descripcion: str,
    ) -> None:
        db.add(
            Bitacora(
                usuario_id=usuario_id,
                fecha_hora=datetime.now(timezone.utc),
                accion=accion,
                entidad_afectada=entidad_afectada,
                descripcion=descripcion,
            )
        )
        db.flush()
