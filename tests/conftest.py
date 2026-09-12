import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.database import engine, get_db
from app.core.security import create_access_token
from app.main import app
from app.modules.autenticacion_seguridad.models.models import (
    Empleado,
    Rol,
    Usuario,
)
from app.modules.sucursales.models.models import Sucursal


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
def encargado_usuario(db_session) -> Usuario:
    """Crea temporalmente un usuario ENCARGADO_SUCURSAL para pruebas RBAC.

    El usuario se crea dentro de la transaccion revertida del fixture
    db_session, por lo que no deja residuos en la base de datos. Se asocia a
    un empleado activo para poder validar el alcance por sucursal (CU12).
    """
    rol = db_session.scalar(
        select(Rol).where(Rol.nombre == "ENCARGADO_SUCURSAL")
    )
    assert rol is not None, "No existe el rol ENCARGADO_SUCURSAL"
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"encargado.cu11.{uuid.uuid4().hex[:8]}@fashionstore.test",
        password_hash="temporal",
        fecha_creacion=datetime.now(timezone.utc),
        estado=True,
    )
    db_session.add(usuario)
    db_session.flush()

    sucursal = db_session.scalar(
        select(Sucursal).where(Sucursal.estado.is_(True)).order_by(Sucursal.id)
    )
    assert sucursal is not None, "No existe una sucursal activa"
    empleado = Empleado(
        usuario_id=usuario.id,
        sucursal_id=sucursal.id,
        nombres="Encargado",
        apellidos="Prueba",
        ci=f"CI{uuid.uuid4().hex[:10]}",
        telefono=None,
        fecha_contratacion=date.today(),
        estado=True,
    )
    db_session.add(empleado)
    db_session.flush()
    return usuario


@pytest.fixture()
def admin_headers(admin_usuario) -> dict:
    return {"Authorization": f"Bearer {_token(admin_usuario)}"}


@pytest.fixture()
def cajero_headers(cajero_usuario) -> dict:
    return {"Authorization": f"Bearer {_token(cajero_usuario)}"}


@pytest.fixture()
def encargado_headers(encargado_usuario) -> dict:
    return {"Authorization": f"Bearer {_token(encargado_usuario)}"}
