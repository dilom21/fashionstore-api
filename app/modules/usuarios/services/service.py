from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.modules.autenticacion_seguridad.models.models import Empleado, Usuario
from app.modules.roles.services.service import RolService
from app.modules.sucursales.repositories.repository import SucursalRepository
from app.modules.usuarios.repositories.repository import UsuarioRepository
from app.modules.usuarios.schemas.schemas import (
    EmpleadoCreate,
    EmpleadoUpdate,
    UsuarioCreate,
    UsuarioUpdate,
)


class UsuarioNoEncontradoError(Exception):
    pass


class CorreoDuplicadoError(Exception):
    pass


class CiDuplicadoError(Exception):
    pass


class RolInexistenteError(Exception):
    pass


class RolInactivoError(Exception):
    pass


class SucursalInvalidaError(Exception):
    pass


class EmpleadoNoEncontradoError(Exception):
    pass


class SinCambiosError(Exception):
    pass


class UsuarioService:
    """Reglas de negocio de CU03 - Gestionar usuarios internos."""

    @staticmethod
    def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
        db.execute(
            text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
            {"usuario_id": str(usuario_id)},
        )

    @staticmethod
    def _momento_actual() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def listar(
        db: Session,
        buscar: str | None = None,
        rol_id: int | None = None,
        estado: bool | None = None,
        sucursal_id: int | None = None,
    ) -> list[Usuario]:
        return UsuarioRepository.listar(
            db,
            buscar=buscar,
            rol_id=rol_id,
            estado=estado,
            sucursal_id=sucursal_id,
        )

    @staticmethod
    def obtener(db: Session, usuario_id: int) -> Usuario | None:
        return UsuarioRepository.obtener_por_id(db, usuario_id)

    @staticmethod
    def crear(db: Session, datos: UsuarioCreate, admin: Usuario) -> Usuario:
        if UsuarioRepository.obtener_por_correo(db, datos.correo) is not None:
            raise CorreoDuplicadoError()

        rol = UsuarioRepository.obtener_rol_por_id(db, datos.rol_id)
        if rol is None:
            raise RolInexistenteError()
        if not rol.estado:
            raise RolInactivoError()
        RolService.validar_rol_para_usuario_interno(rol)

        if datos.empleado.ci:
            if UsuarioRepository.obtener_empleado_por_ci(db, datos.empleado.ci):
                raise CiDuplicadoError()

        sucursal = SucursalRepository.obtener_activa_por_id(
            db, datos.empleado.sucursal_id
        )
        if sucursal is None:
            raise SucursalInvalidaError()

        try:
            UsuarioService._establecer_contexto_bitacora(db, admin.id)

            usuario = Usuario(
                correo=datos.correo,
                password_hash=hash_password(datos.password),
                rol_id=rol.id,
                fecha_creacion=UsuarioService._momento_actual(),
                estado=True,
            )
            db.add(usuario)
            db.flush()

            empleado = UsuarioService._construir_empleado(
                datos.empleado, usuario.id
            )
            db.add(empleado)
            db.flush()

            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise CorreoDuplicadoError() from exc

        usuario = UsuarioRepository.obtener_por_id(db, usuario.id)
        if usuario is None:
            raise UsuarioNoEncontradoError()
        return usuario

    @staticmethod
    def _construir_empleado(
        datos: EmpleadoCreate, usuario_id: int
    ) -> Empleado:
        return Empleado(
            usuario_id=usuario_id,
            sucursal_id=datos.sucursal_id,
            nombres=datos.nombres.strip(),
            apellidos=datos.apellidos.strip(),
            ci=datos.ci.strip(),
            telefono=(
                datos.telefono.strip() if datos.telefono is not None else None
            ),
            fecha_contratacion=datos.fecha_contratacion,
            estado=True,
        )

    @staticmethod
    def actualizar(
        db: Session, usuario_id: int, datos: UsuarioUpdate, admin: Usuario
    ) -> Usuario:
        usuario = UsuarioRepository.obtener_por_id(db, usuario_id)
        if usuario is None:
            raise UsuarioNoEncontradoError()

        if not datos.tiene_datos:
            raise SinCambiosError()

        if datos.correo is not None:
            existente = UsuarioRepository.obtener_por_correo(db, datos.correo)
            if existente is not None and existente.id != usuario.id:
                raise CorreoDuplicadoError()
            usuario.correo = datos.correo

        if datos.password is not None:
            usuario.password_hash = hash_password(datos.password)

        if datos.rol_id is not None and datos.rol_id != usuario.rol_id:
            rol = UsuarioRepository.obtener_rol_por_id(db, datos.rol_id)
            if rol is None:
                raise RolInexistenteError()
            if not rol.estado:
                raise RolInactivoError()
            RolService.validar_rol_para_usuario_interno(rol)
            usuario.rol_id = rol.id

        if datos.empleado is not None and datos.empleado.tiene_datos:
            UsuarioService._aplicar_actualizacion_empleado(
                db, usuario, datos.empleado
            )

        try:
            UsuarioService._establecer_contexto_bitacora(db, admin.id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise CorreoDuplicadoError() from exc

        usuario = UsuarioRepository.obtener_por_id(db, usuario.id)
        if usuario is None:
            raise UsuarioNoEncontradoError()
        return usuario

    @staticmethod
    def _aplicar_actualizacion_empleado(
        db: Session, usuario: Usuario, datos: EmpleadoUpdate
    ) -> None:
        if usuario.empleado is None:
            raise EmpleadoNoEncontradoError()

        empleado = usuario.empleado

        if datos.sucursal_id is not None:
            sucursal = SucursalRepository.obtener_activa_por_id(
                db, datos.sucursal_id
            )
            if sucursal is None:
                raise SucursalInvalidaError()
            empleado.sucursal_id = sucursal.id

        if datos.ci is not None and datos.ci.strip().lower() != empleado.ci.lower():
            existente = UsuarioRepository.obtener_empleado_por_ci(db, datos.ci)
            if existente is not None and existente.usuario_id != usuario.id:
                raise CiDuplicadoError()
            empleado.ci = datos.ci.strip()

        if datos.nombres is not None:
            empleado.nombres = datos.nombres.strip()
        if datos.apellidos is not None:
            empleado.apellidos = datos.apellidos.strip()
        if datos.telefono is not None:
            empleado.telefono = datos.telefono.strip()
        if datos.fecha_contratacion is not None:
            empleado.fecha_contratacion = datos.fecha_contratacion

    @staticmethod
    def cambiar_estado(
        db: Session, usuario_id: int, estado: bool, admin: Usuario
    ) -> Usuario:
        usuario = UsuarioRepository.obtener_por_id(db, usuario_id)
        if usuario is None:
            raise UsuarioNoEncontradoError()

        if usuario.estado == estado:
            return usuario

        usuario.estado = estado

        try:
            UsuarioService._establecer_contexto_bitacora(db, admin.id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise CorreoDuplicadoError() from exc

        usuario = UsuarioRepository.obtener_por_id(db, usuario.id)
        if usuario is None:
            raise UsuarioNoEncontradoError()
        return usuario
