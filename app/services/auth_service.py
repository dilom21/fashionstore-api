from sqlalchemy.orm import Session

from app.core.security import create_access_token, verify_password
from app.models.seguridad import Usuario
from app.repositories.auth_repository import AuthRepository


class AuthService:
    @staticmethod
    def autenticar(db: Session, correo: str, password: str) -> tuple[Usuario, str] | None:
        usuario = AuthRepository.obtener_usuario_activo_por_correo(db, correo.lower())
        if usuario is None or not verify_password(password, usuario.password_hash):
            return None

        token = create_access_token(
            {
                "sub": str(usuario.id),
                "correo": usuario.correo,
                "rol": usuario.rol.nombre,
            }
        )
        return usuario, token

    @staticmethod
    def obtener_usuario_activo(db: Session, usuario_id: int) -> Usuario | None:
        return AuthRepository.obtener_usuario_activo_por_id(db, usuario_id)
