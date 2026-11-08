from contextvars import ContextVar

from fastapi import Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.request_ip import obtener_ip_cliente
from app.core.surface_guard import SURFACE_LEGACY, SURFACE_TENANT


def _crear_engine(url: str):
    """Engine con la misma politica de pool para ambas superficies."""
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


# Superficie de la peticion en curso. La fija el middleware
# `ResolucionSuperficie` antes de llegar al router; las rutas tenant siguen
# exigiendo JWT + membresia, de modo que esta variable solo decide CONTRA QUE
# BASE se ejecuta una sesion, nunca quien es el usuario.
superficie_actual: ContextVar[str] = ContextVar(
    "superficie_actual", default=SURFACE_LEGACY
)

engine = _crear_engine(settings.database_url)


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

# 5C1: segundo destino SOLO para el contrato legacy cuando convive con SaaS.
# Si no se declara, la superficie legacy usa el engine tenant (comportamiento
# historico, seguro porque en ese caso el gate bloquea el legacy o el SaaS
# esta apagado).
_legacy_url = (settings.legacy_database_url or "").strip()
engine_legacy = _crear_engine(_legacy_url) if _legacy_url else None
SessionLegacyLocal = (
    sessionmaker(bind=engine_legacy, autoflush=False, autocommit=False)
    if engine_legacy is not None
    else None
)


def _motor_auxiliar(url: str | None, tamano: int = 2):
    """Motor para un pool con función propia (bootstrap / platform_admin).

    Sin DSN dedicado se reutiliza el pool principal, que es el comportamiento
    previo a la migración 050a-c. Cada pool usa su login PostgreSQL restringido
    (NOLOGIN + GRANT), nunca una credencial con BYPASSRLS.
    """
    if not url:
        return engine
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=tamano,
        max_overflow=tamano,
    )


# 5CA: tres pools por función. El login sólo ejecuta la proyección estrecha de
# bootstrap; /saas/superadmin es el único consumidor del pool platform_admin.
bootstrap_engine = _motor_auxiliar(settings.database_bootstrap_url)
platform_engine = _motor_auxiliar(settings.database_platform_url)

BootstrapSession = sessionmaker(
    bind=bootstrap_engine,
    autoflush=False,
    autocommit=False,
)

PlatformSession = sessionmaker(
    bind=platform_engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


@event.listens_for(Session, "after_begin")
def _aplicar_contexto_request(
    session: Session,
    transaction,
    connection,
) -> None:
    """Propaga la IP del request a PostgreSQL en cada transacción.

    Se usa ``set_config('app.ip', :ip, true)``: el valor es local a la
    transacción, de modo que una conexión devuelta al pool nunca filtra la IP
    de un request hacia otro. El listener reaplica el contexto si la misma
    Session abre una nueva transacción.

    En SaaS, tras validar JWT+membresia, Session.info contiene tambien
    usuario_id. Se reaplica solo como dato de AUDITORIA, nunca como prueba
    de la identidad/autorizacion de una conexion SQL arbitraria.
    """
    request_ip = session.info.get("request_ip")
    if request_ip:
        connection.execute(
            text("SELECT set_config('app.ip', :ip, true)"),
            {"ip": request_ip},
        )
    # 5CA: el JWT crudo se publica como GUC LOCAL antes de cualquier consulta,
    # para que actor_claims() pueda verificar firma, vigencia y membresía en
    # PostgreSQL. is_local=true es obligatorio: con false el token sobrevive al
    # commit y una conexión reutilizada del pool heredaría la identidad del
    # request anterior (demostrado en POOL-03 del laboratorio 5CA).
    actor_token = session.info.get("actor_token")
    if actor_token:
        connection.execute(
            text("SELECT set_config('app.actor_token', :token, true)"),
            {"token": actor_token},
        )
    # El tenant solo se añade a Session.info DESPUES de una membresia
    # comprobada en app/core/tenant_dependencies.py. Cada nueva transaccion
    # reestablece el valor de manera LOCAL (sin fugas por connection pool).
    empresa_id = session.info.get("empresa_id")
    if empresa_id is not None:
        connection.execute(
            text("SELECT set_config('app.empresa_id', :empresa_id, true)"),
            {"empresa_id": str(empresa_id)},
        )
    # Solo la dependencia SaaS validada inserta esta clave en Session.info.
    # Esta reaplicacion ocurre tras un commit/rollback cuando la misma Session
    # vuelve a abrir una transaccion. No afecta servicios legacy sin esa clave.
    usuario_id = session.info.get("usuario_id")
    if usuario_id is not None:
        connection.execute(
            text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
            {"usuario_id": str(usuario_id)},
        )
def _fabrica_de_sesiones(superficie: str):
    """Sesion adecuada a la superficie de la peticion.

    La superficie legacy solo usa un engine propio cuando existe
    `LEGACY_DATABASE_URL`; en cualquier otro caso comparte el engine tenant,
    que es el comportamiento historico (sin SaaS activo no hay fuga posible).
    """
    if superficie == SURFACE_LEGACY and SessionLegacyLocal is not None:
        return SessionLegacyLocal
    return SessionLocal


def get_db(request: Request):
    superficie = superficie_actual.get()
    db = _fabrica_de_sesiones(superficie)()
    db.info["request_ip"] = obtener_ip_cliente(request)
    # Marca de diagnostico: nunca se usa como autorizacion.
    db.info["superficie"] = superficie

    try:
        yield db
    finally:
        db.close()


def get_bootstrap_db():
    """Pool de bootstrap: sólo la proyección estrecha de credenciales/empresa.

    No publica IP ni token porque no escribe bitácora ni evalúa policies de
    tenant; su login PostgreSQL no tiene acceso a tablas comerciales.
    """
    db = BootstrapSession()
    try:
        yield db
    finally:
        db.close()


def get_platform_db():
    """Pool exclusivo de /saas/superadmin (administración global deliberada)."""
    db = PlatformSession()
    try:
        yield db
    finally:
        db.close()
