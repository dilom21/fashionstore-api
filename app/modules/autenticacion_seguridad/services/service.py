from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.password_policy import validar_password_segura
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.modules.autenticacion_seguridad.models.models import (
    Cliente,
    Empleado,
    Usuario,
)
from app.modules.autenticacion_seguridad.repositories.repository import (
    AuthRepository,
)
from app.modules.autenticacion_seguridad.schemas.schemas import (
    ClientePerfilResponse,
    ClientePerfilUpdateRequest,
    ClienteRegistroRequest,
)

NOMBRE_ROL_CLIENTE = "CLIENTE"
MENSAJE_CUENTA_CREADA = "Cuenta creada correctamente."


class RegistroClienteError(Exception):
    """Base de errores del registro publico de cuenta de CLIENTE."""


class CorreoClienteDuplicadoError(RegistroClienteError):
    """Ya existe una cuenta con ese correo."""


class CiClienteDuplicadoError(RegistroClienteError):
    """Ya existe un cliente con ese documento."""


class PasswordInseguraError(RegistroClienteError):
    """La contrasena no cumple la politica (mensaje controlado)."""


class PasswordsNoCoincidenError(RegistroClienteError):
    """password y password_confirmacion son distintas."""


class RolClienteNoDisponibleError(RegistroClienteError):
    """El rol CLIENTE no existe o esta inactivo: configuracion interna."""


class RegistroInesperadoError(RegistroClienteError):
    """Fallo no atribuible a duplicados (no se expone el detalle SQL)."""


class PerfilClienteVacioError(Exception):
    """El PATCH no contiene campos editables."""


def _constraint_violada(exc: IntegrityError) -> str | None:
    """Nombre de la constraint violada (psycopg3), de forma segura.

    Usa ``exc.orig.diag.constraint_name`` y, como respaldo, busca tokens
    conocidos en el texto del error. Ese texto nunca se expone al cliente:
    solo se usa para decidir el codigo HTTP.
    """
    origen = getattr(exc, "orig", None)
    diag = getattr(origen, "diag", None)
    nombre = getattr(diag, "constraint_name", None)
    if nombre:
        return str(nombre)

    texto = str(origen or exc)
    for esperada in ("usuario_correo_key", "cliente_ci_key", "cliente_ci"):
        if esperada in texto:
            return esperada
    return None


def _traducir_integrity_error(exc: IntegrityError) -> RegistroClienteError:
    """Carrera de concurrencia: constraint real -> error de dominio."""
    nombre = (_constraint_violada(exc) or "").lower()
    texto = str(getattr(exc, "orig", exc) or "").lower()

    if "correo" in nombre or "correo" in texto:
        return CorreoClienteDuplicadoError()
    if "cliente_ci" in nombre or "_ci_" in nombre or "ci_key" in nombre:
        return CiClienteDuplicadoError()
    if "cliente_ci" in texto or "ci_key" in texto:
        return CiClienteDuplicadoError()
    return RegistroInesperadoError()


def _momento_actual() -> datetime:
    """Patron temporal del proyecto: UTC en el propio backend."""
    return datetime.now(timezone.utc)


class AuthService:
    """Autenticacion compartida para los contextos cliente y personal.

    Incluye el registro publico de cuenta de CLIENTE (sin JWT previo): crea
    Usuario + Cliente en UNA sola transaccion, con el rol CLIENTE resuelto por
    nombre en la base (nunca por ID fijo).
    """

    @staticmethod
    def autenticar_credenciales(
        db: Session, correo: str, password: str
    ) -> Usuario | None:
        """Valida correo normalizado, usuario activo y contrasena Argon2."""
        correo_normalizado = str(correo).strip().lower()
        usuario = AuthRepository.obtener_usuario_activo_por_correo(
            db, correo_normalizado
        )
        if usuario is None or not verify_password(password, usuario.password_hash):
            return None
        return usuario

    @staticmethod
    def login_cliente(
        db: Session, correo: str, password: str
    ) -> tuple[Usuario, Cliente, str] | None:
        usuario = AuthService.autenticar_credenciales(db, correo, password)
        if usuario is None:
            return None

        cliente = AuthRepository.obtener_cliente_activo_por_usuario_id(db, usuario.id)
        if cliente is None:
            return None

        access_token = AuthService._generar_token(usuario, contexto="cliente")
        return usuario, cliente, access_token

    @staticmethod
    def login_personal(
        db: Session, correo: str, password: str
    ) -> tuple[Usuario, Empleado, str] | None:
        usuario = AuthService.autenticar_credenciales(db, correo, password)
        if usuario is None:
            return None

        empleado = AuthRepository.obtener_empleado_activo_por_usuario_id(
            db, usuario.id
        )
        if empleado is None:
            return None

        access_token = AuthService._generar_token(usuario, contexto="personal")
        return usuario, empleado, access_token

    @staticmethod
    def _generar_token(usuario: Usuario, contexto: str) -> str:
        return create_access_token(
            {
                "sub": str(usuario.id),
                "correo": usuario.correo,
                "rol": usuario.rol.nombre,
                "contexto": contexto,
            }
        )

    @staticmethod
    def obtener_usuario_activo(db: Session, usuario_id: int) -> Usuario | None:
        return AuthRepository.obtener_usuario_activo_por_id(db, usuario_id)

    @staticmethod
    def obtener_cliente_activo(db: Session, usuario_id: int) -> Cliente | None:
        """Perfil de cliente activo asociado al usuario autenticado."""
        return AuthRepository.obtener_cliente_activo_por_usuario_id(
            db, usuario_id
        )

    @staticmethod
    def obtener_perfil_cliente(cliente: Cliente, usuario: Usuario) -> ClientePerfilResponse:
        return ClientePerfilResponse(
            cliente_id=cliente.id,
            usuario_id=usuario.id,
            correo=usuario.correo,
            rol=usuario.rol.nombre,
            nombre=cliente.nombre,
            apellido=cliente.apellido,
            ci=cliente.ci,
            telefono=cliente.telefono,
            sexo=cliente.sexo,
            fecha_nacimiento=cliente.fecha_nacimiento,
            estado=cliente.estado,
            fecha_creacion=usuario.fecha_creacion,
        )

    @staticmethod
    def actualizar_perfil_cliente(
        db: Session, cliente: Cliente, usuario: Usuario, datos: ClientePerfilUpdateRequest
    ) -> ClientePerfilResponse:
        cambios = datos.model_dump(exclude_unset=True)
        if not cambios:
            raise PerfilClienteVacioError()
        try:
            for campo, valor in cambios.items():
                setattr(cliente, campo, valor.value if hasattr(valor, "value") else valor)
            db.flush()
            perfil = AuthService.obtener_perfil_cliente(cliente, usuario)
            db.commit()
            return perfil
        except Exception:
            db.rollback()
            raise

    # ------------------------------------------------------------------
    # Registro publico de cuenta de CLIENTE
    # ------------------------------------------------------------------

    @staticmethod
    def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
        """Deja al usuario recien creado como autor en la bitacora.

        Mismo mecanismo que CU03/CU25: ``app.usuario_id`` es local a la
        transaccion, de modo que no se filtra entre requests.
        """
        db.execute(
            text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
            {"usuario_id": str(usuario_id)},
        )

    @staticmethod
    def registrar_cliente(
        db: Session, datos: ClienteRegistroRequest
    ) -> tuple[Usuario, Cliente]:
        """Registro publico de CLIENTE: Usuario + Cliente en UNA transaccion.

        Orden: politica de contrasena -> confirmacion -> rol CLIENTE (por
        NOMBRE y activo) -> correo unico -> CI unico -> crear Usuario (flush)
        -> crear Cliente (flush) -> UNICO ``commit()``.

        Cualquier fallo revierte TODO (``rollback``), por lo que nunca queda un
        Usuario sin Cliente. No emite JWT: el cliente inicia sesion despues con
        ``POST /auth/clientes/login``.
        """
        try:
            validar_password_segura(datos.password)
        except ValueError as exc:
            raise PasswordInseguraError(str(exc)) from exc

        if datos.password != datos.password_confirmacion:
            raise PasswordsNoCoincidenError()

        rol = AuthRepository.obtener_rol_activo_por_nombre(
            db, NOMBRE_ROL_CLIENTE
        )
        if rol is None:
            raise RolClienteNoDisponibleError()

        if AuthRepository.obtener_usuario_por_correo(db, datos.correo):
            raise CorreoClienteDuplicadoError()

        if AuthRepository.obtener_cliente_por_ci(db, datos.ci):
            raise CiClienteDuplicadoError()

        try:
            usuario = AuthRepository.crear_usuario(
                db,
                correo=datos.correo,
                password_hash=hash_password(datos.password),
                rol_id=rol.id,
                fecha_creacion=_momento_actual(),
                estado=True,
            )
            AuthService._establecer_contexto_bitacora(db, usuario.id)

            cliente = AuthRepository.crear_cliente(
                db,
                usuario_id=usuario.id,
                nombre=datos.nombre,
                apellido=datos.apellido,
                telefono=datos.telefono,
                sexo=datos.sexo.value,
                ci=datos.ci,
                fecha_nacimiento=datos.fecha_nacimiento,
                estado=True,
            )

            # Unico commit: Usuario y Cliente se confirman juntos o ninguno.
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise _traducir_integrity_error(exc) from exc
        except Exception:
            db.rollback()
            raise

        return usuario, cliente
