from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.modules.autenticacion_seguridad.models.models import (
    Empleado,
    Rol,
    Usuario,
)

_OPCIONES_USUARIO = (
    joinedload(Usuario.rol),
    joinedload(Usuario.empleado).joinedload(Empleado.sucursal),
)


def _existe_empleado_con(*clausulas):
    """Subconsulta correlacionada para filtrar por datos del empleado.

    Evita que los filtros sobre Empleado conviertan el listado de usuarios
    en un JOIN interno (los usuarios sin empleado no son internos).
    """
    subconsulta = select(Empleado.id).where(
        Empleado.usuario_id == Usuario.id,
        *clausulas,
    )
    return subconsulta.exists()


class UsuarioRepository:
    @staticmethod
    def listar(
        db: Session,
        buscar: str | None = None,
        rol_id: int | None = None,
        estado: bool | None = None,
        sucursal_id: int | None = None,
    ) -> list[Usuario]:
        statement = select(Usuario).options(*_OPCIONES_USUARIO)
        condiciones = []

        if estado is not None:
            condiciones.append(Usuario.estado.is_(estado))

        if rol_id is not None:
            condiciones.append(Usuario.rol_id == rol_id)

        if sucursal_id is not None:
            condiciones.append(
                _existe_empleado_con(Empleado.sucursal_id == sucursal_id)
            )

        if buscar:
            patron = f"%{buscar.strip().lower()}%"
            condiciones.append(
                or_(
                    func.lower(Usuario.correo).ilike(patron),
                    _existe_empleado_con(
                        or_(
                            func.lower(Empleado.nombres).ilike(patron),
                            func.lower(Empleado.apellidos).ilike(patron),
                            func.lower(Empleado.ci).ilike(patron),
                        ),
                    ),
                )
            )

        if condiciones:
            statement = statement.where(*condiciones)

        statement = statement.order_by(Usuario.id)
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, usuario_id: int) -> Usuario | None:
        statement = (
            select(Usuario)
            .options(*_OPCIONES_USUARIO)
            .where(Usuario.id == usuario_id)
        )
        return db.scalar(statement)

    @staticmethod
    def obtener_por_correo(db: Session, correo: str) -> Usuario | None:
        statement = (
            select(Usuario)
            .options(*_OPCIONES_USUARIO)
            .where(func.lower(Usuario.correo) == correo.strip().lower())
        )
        return db.scalar(statement)

    @staticmethod
    def obtener_rol_por_id(db: Session, rol_id: int) -> Rol | None:
        return db.get(Rol, rol_id)

    @staticmethod
    def obtener_empleado_por_ci(db: Session, ci: str) -> Empleado | None:
        statement = select(Empleado).where(
            func.lower(Empleado.ci) == ci.strip().lower()
        )
        return db.scalar(statement)
