from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.modules.autenticacion_seguridad.models.models import (
    Cliente,
    Empleado,
    Rol,
    Usuario,
)


class AuthRepository:
    @staticmethod
    def obtener_usuario_activo_por_correo(
        db: Session, correo: str
    ) -> Usuario | None:
        statement = (
            select(Usuario)
            .options(joinedload(Usuario.rol))
            .where(Usuario.correo == correo, Usuario.estado.is_(True))
        )
        return db.scalar(statement)

    @staticmethod
    def obtener_usuario_activo_por_id(db: Session, usuario_id: int) -> Usuario | None:
        statement = (
            select(Usuario)
            .options(joinedload(Usuario.rol))
            .where(Usuario.id == usuario_id, Usuario.estado.is_(True))
        )
        return db.scalar(statement)

    @staticmethod
    def obtener_cliente_activo_por_usuario_id(
        db: Session, usuario_id: int
    ) -> Cliente | None:
        statement = select(Cliente).where(
            Cliente.usuario_id == usuario_id,
            Cliente.estado.is_(True),
        )
        return db.scalar(statement)

    @staticmethod
    def obtener_empleado_activo_por_usuario_id(
        db: Session, usuario_id: int
    ) -> Empleado | None:
        statement = select(Empleado).where(
            Empleado.usuario_id == usuario_id,
            Empleado.estado.is_(True),
        )
        return db.scalar(statement)

    # ------------------------------------------------------------------
    # Registro publico de cuenta de CLIENTE
    # ------------------------------------------------------------------

    @staticmethod
    def obtener_usuario_por_correo(db: Session, correo: str) -> Usuario | None:
        """Usuario con ese correo (cualquier estado), sin distinguir mayusculas."""
        statement = select(Usuario).where(
            func.lower(Usuario.correo) == correo.strip().lower()
        )
        return db.scalar(statement)

    @staticmethod
    def obtener_rol_activo_por_nombre(db: Session, nombre: str) -> Rol | None:
        """Rol por NOMBRE (nunca por ID fijo) y que este activo."""
        statement = select(Rol).where(
            func.upper(Rol.nombre) == nombre.strip().upper(),
            Rol.estado.is_(True),
        )
        return db.scalar(statement)

    @staticmethod
    def obtener_cliente_por_ci(db: Session, ci: str) -> Cliente | None:
        """Cliente con ese documento (case-insensitive) para detectar duplicados."""
        statement = select(Cliente).where(
            func.lower(Cliente.ci) == ci.strip().lower()
        )
        return db.scalar(statement)

    @staticmethod
    def crear_usuario(
        db: Session,
        *,
        correo: str,
        password_hash: str,
        rol_id: int,
        fecha_creacion: datetime,
        estado: bool = True,
    ) -> Usuario:
        """Inserta el Usuario y hace flush para obtener su id (sin commit)."""
        usuario = Usuario(
            correo=correo,
            password_hash=password_hash,
            rol_id=rol_id,
            fecha_creacion=fecha_creacion,
            estado=estado,
        )
        db.add(usuario)
        db.flush()
        return usuario

    @staticmethod
    def crear_cliente(
        db: Session,
        *,
        usuario_id: int,
        nombre: str,
        apellido: str,
        telefono: str,
        sexo: str,
        ci: str,
        fecha_nacimiento: date,
        estado: bool = True,
    ) -> Cliente:
        """Inserta el Cliente ligado al Usuario y hace flush (sin commit)."""
        cliente = Cliente(
            usuario_id=usuario_id,
            nombre=nombre,
            apellido=apellido,
            telefono=telefono,
            sexo=sexo,
            ci=ci,
            fecha_nacimiento=fecha_nacimiento,
            estado=estado,
        )
        db.add(cliente)
        db.flush()
        return cliente
