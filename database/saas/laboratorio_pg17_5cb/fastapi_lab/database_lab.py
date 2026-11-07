"""Adaptación de laboratorio 5CB del acceso a datos de FastAPI.

No modifica el código original: son archivos nuevos que se importan junto a él.
Los cambios son exactamente los mínimos que exige SEGURIDAD_ARQUITECTURA.md:
  1. conservar el JWT crudo y publicarlo como app.actor_token en cada transacción;
  2. pool bootstrap dedicado;
  3. pool platform_admin dedicado;
  4. ningún GUC de IDs se usa como autorización.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _url(dsn: str) -> str:
    """psycopg3 necesita el dialecto explícito; el DSN del laboratorio es local."""
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://"):]
    return dsn


def _motor(dsn: str, tamano: int = 5):
    return create_engine(
        _url(dsn),
        pool_pre_ping=True,
        pool_size=tamano,
        max_overflow=tamano,
    )


engine = _motor(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

BOOTSTRAP_DSN = os.environ["FASHIONSTORE_BOOTSTRAP_DSN"]
PLATFORM_DSN = os.environ["FASHIONSTORE_PLATFORM_DSN"]
bootstrap_engine = _motor(BOOTSTRAP_DSN, 2)
platform_engine = _motor(PLATFORM_DSN, 2)
BootstrapSession = sessionmaker(bind=bootstrap_engine, autoflush=False,
                                autocommit=False)
PlatformSession = sessionmaker(bind=platform_engine, autoflush=False,
                               autocommit=False)


@event.listens_for(Session, "after_begin")
def _aplicar_contexto(session: Session, transaction, connection) -> None:
    """Publica el token firmado como GUC LOCAL antes de cualquier consulta.

    is_local=true es obligatorio: con false el token sobrevive al commit y una
    conexión reutilizada podría heredar la identidad del request anterior
    (demostrado en la prueba POOL-03 del laboratorio).
    """
    request_ip = session.info.get("request_ip")
    if request_ip:
        connection.execute(text("SELECT set_config('app.ip', :ip, true)"),
                           {"ip": request_ip})
    actor_token = session.info.get("actor_token")
    if actor_token:
        connection.execute(
            text("SELECT set_config('app.actor_token', :token, true)"),
            {"token": actor_token},
        )
    # Sólo auditoría: actor_claims() NO lee estos GUC para autorizar.
    empresa_id = session.info.get("empresa_id")
    if empresa_id is not None:
        connection.execute(
            text("SELECT set_config('app.empresa_id', :v, true)"),
            {"v": str(empresa_id)},
        )
    usuario_id = session.info.get("usuario_id")
    if usuario_id is not None:
        connection.execute(
            text("SELECT set_config('app.usuario_id', :v, true)"),
            {"v": str(usuario_id)},
        )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_bootstrap_db():
    db = BootstrapSession()
    try:
        yield db
    finally:
        db.close()


def get_platform_db():
    db = PlatformSession()
    try:
        yield db
    finally:
        db.close()
