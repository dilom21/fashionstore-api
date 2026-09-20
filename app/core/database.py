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
def _aplicar_contexto_ip(
    session: Session,
    transaction,
    connection,
) -> None:
    """Propaga la IP del request a PostgreSQL en cada transacción.

    Se usa ``set_config('app.ip', :ip, true)``: el valor es local a la
    transacción, de modo que una conexión devuelta al pool nunca filtra la IP
    de un request hacia otro. El listener reaplica el contexto si la misma
    Session abre una nueva transacción.

    Coexiste con ``app.usuario_id``, que cada service establece por su cuenta.
    """
    request_ip = session.info.get("request_ip")
    if not request_ip:
        return
    connection.execute(
        text("SELECT set_config('app.ip', :ip, true)"),
        {"ip": request_ip},
    )


def get_db(request: Request):
    db = SessionLocal()
    db.info["request_ip"] = obtener_ip_cliente(request)

    try:
        yield db
    finally:
        db.close()