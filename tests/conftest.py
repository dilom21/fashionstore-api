import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.database import engine, get_db
from app.core.security import create_access_token
from app.main import app
from app.modules.autenticacion_seguridad.models.models import Rol, Usuario


@pytest.fixture()
def db_session():
    """Sesion de prueba ligada a una transaccion externa que se revierte.

    Todo lo escrito por los endpoints (incluidos los triggers de bitacora)
    se deshace al finalizar, por lo que no queda informacion temporal en la
    base de datos real.
    """
    connection = engine.connect()
    transaction = connection.begin()
    testing_session = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        join_transaction_mode="create_savepoint",
    )
    session = testing_session()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _usuario_activo_por_rol(db_session, rol_nombre: str) -> Usuario:
    statement = (
        select(Usuario)
        .join(Rol, Rol.id == Usuario.rol_id)
        .where(Rol.nombre == rol_nombre, Usuario.estado.is_(True))
        .order_by(Usuario.id)
    )
    usuario = db_session.scalar(statement)
    assert usuario is not None, f"No existe usuario activo con rol {rol_nombre}"
    return usuario


def _token(usuario: Usuario) -> str:
    return create_access_token(
        {
            "sub": str(usuario.id),
            "correo": usuario.correo,
            "rol": usuario.rol.nombre,
            "contexto": "personal",
        }
    )


@pytest.fixture()
def admin_usuario(db_session) -> Usuario:
    return _usuario_activo_por_rol(db_session, "ADMINISTRADOR")


@pytest.fixture()
def cajero_usuario(db_session) -> Usuario:
    return _usuario_activo_por_rol(db_session, "CAJERO")


@pytest.fixture()
def admin_headers(admin_usuario) -> dict:
    return {"Authorization": f"Bearer {_token(admin_usuario)}"}


@pytest.fixture()
def cajero_headers(cajero_usuario) -> dict:
    return {"Authorization": f"Bearer {_token(cajero_usuario)}"}
