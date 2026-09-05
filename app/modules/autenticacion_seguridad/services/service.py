from sqlalchemy.orm import Session

from app.core.security import create_access_token, verify_password
from app.modules.autenticacion_seguridad.models.models import (
    Cliente,
    Empleado,
    Usuario,
)
from app.modules.autenticacion_seguridad.repositories.repository import AuthRepository


class AuthService:
    """Autenticacion compartida para los contextos cliente y personal."""

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
