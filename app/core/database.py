from fastapi import Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.request_ip import obtener_ip_cliente


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)


SessionLocal = sessionmaker(
    bind=engine,
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


def get_db(request: Request):
    db = SessionLocal()
    db.info["request_ip"] = obtener_ip_cliente(request)

    try:
        yield db
    finally:
        db.close()