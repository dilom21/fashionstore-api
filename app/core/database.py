from contextvars import ContextVar

from fastapi import Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.request_ip import obtener_ip_cliente
from app.core.saas_rls_assertion import construir_asercion, es_postgres
from app.core.surface_guard import SURFACE_LEGACY, SURFACE_TENANT


def _crear_engine(url: str):
    """Engine con la misma politica de pool para ambas superficies."""
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
    # Etapa C (5BK): asercion FIRMADA por transaccion. Es lo unico que las
    # politicas RLS usan para resolver la empresa; los GUC anteriores quedan
    # como datos de auditoria y pueden ser falsificados sin efecto alguno.
    # Se reemite con expiracion nueva cada vez que la Session abre transaccion,
    # de modo que una conexion reutilizada del pool nunca herede una asercion
    # vencida ni la de otro request.
    if (
        empresa_id is not None
        and usuario_id is not None
        and session.info.get("rls_assertion", True)
        and es_postgres(connection)
    ):
        asercion = construir_asercion(int(empresa_id), int(usuario_id))
        if asercion is not None:
            connection.execute(
                text("SELECT set_config('app.tenant_assertion', :asercion, true)"),
                {"asercion": asercion},
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