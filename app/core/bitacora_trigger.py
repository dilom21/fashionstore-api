"""Detección de auditoría por trigger para evitar eventos duplicados.

La migración 040 instala ``trg_bitacora_categoria`` porque ``public.categoria``
era la única tabla empresarial con escritura del administrador (050c) y sin
auditoría por trigger. Con el trigger presente, el evento lo escribe PostgreSQL
en la MISMA transacción del cambio; si el repositorio insertara además su fila
manual, la bitácora registraría dos veces la misma operación.

Este módulo responde una única pregunta --¿la base ya audita ``categoria`` con
trigger?-- sin añadir lógica de negocio:

* ``SAAS_BITACORA_POR_TRIGGER`` (configuración explícita) tiene prioridad: es la
  vía prevista para el entorno donde 040/050c ya están aplicados.
* En PostgreSQL se consulta ``pg_catalog.pg_trigger`` una vez por engine y se
  memoriza el resultado: el esquema sólo cambia con migraciones y el proceso se
  reinicia al aplicarlas. ``pg_trigger`` es legible por cualquier rol, así que la
  sonda no requiere privilegios nuevos.
* En SQLite (pruebas unitarias) no existe ``pg_trigger``: se conserva el insert
  manual previo, que es el comportamiento histórico verificado por la suite.

Si la sonda falla (base sin catálogo accesible), se devuelve ``False``: se
mantiene el comportamiento anterior en vez de silenciar auditoría.
"""
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings

_SQL_TRIGGER_CATEGORIA = text(
    """
    SELECT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_trigger t
        JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'categoria'
          AND t.tgname = 'trg_bitacora_categoria'
          AND NOT t.tgisinternal
    )
    """
)

# Resultado por engine: el esquema no cambia dentro de la vida del proceso.
_CACHE: dict[int, bool] = {}


def bitacora_categoria_por_trigger(db: Session) -> bool:
    """True si la escritura de categoria ya se audita con trigger en la base."""
    try:
        bind = db.get_bind()
        motor = getattr(bind, "engine", bind)
        # SQLite no puede tener el trigger PostgreSQL de auditoría.  Esto debe
        # evaluarse antes del flag de despliegue para que las pruebas aisladas
        # y cualquier laboratorio local con SQLite conserven la auditoría
        # manual histórica.
        if motor.dialect.name != "postgresql":
            return False
        if settings.saas_bitacora_por_trigger:
            return True
        clave = id(motor)
        if clave not in _CACHE:
            _CACHE[clave] = bool(db.scalar(_SQL_TRIGGER_CATEGORIA))
        return _CACHE[clave]
    except SQLAlchemyError:
        return False


def limpiar_cache_bitacora_trigger() -> None:
    """Olvida la sonda (sólo para pruebas que aplican migraciones en caliente)."""
    _CACHE.clear()
