"""5BK / Etapa C: asercion firmada de tenant para las politicas RLS.

El valor de un GUC (``app.empresa_id``) NO es una identidad: cualquier consulta
SQL puede reescribirlo con ``set_config``. Este modulo produce una asercion
``empresa|usuario|expira|nonce|hmac`` firmada con un secreto que solo conoce el
proceso FastAPI y la tabla ``saas.rls_secret`` (sin privilegios para el rol de
ejecucion). Las politicas RLS llaman a ``saas.tenant_actual()``, que verifica la
firma y vuelve a comprobar membresia/plan/suscripcion en base.

Limites explicitos:
  * La firma ata la empresa al usuario y a una ventana temporal corta.
  * Un SQL inyectado en la sesion puede leer y copiar su PROPIA asercion, pero no
    firmar una ajena: el secreto no esta en la base ni en el GUC.
  * No protege contra ejecucion arbitraria dentro del proceso Python (ahi el
    secreto vive en memoria); esa frontera queda documentada como riesgo.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.config import settings

# Segundos de validez de una asercion. La funcion SQL rechaza expiraciones a mas
# de 3600 s; se usa una ventana mucho menor y se reemite por transaccion.
TTL_ASERCION_SEGUNDOS = 120
LONGITUD_MINIMA_CLAVE = 32


def clave_asercion() -> bytes | None:
    """Clave HMAC configurada. ``None`` => RLS deny-all (fail closed)."""
    valor = settings.saas_tenant_assertion_key
    if not valor:
        return None
    dato = valor.encode("utf-8")
    if len(dato) < LONGITUD_MINIMA_CLAVE:
        return None
    return dato


def clave_disponible() -> bool:
    return clave_asercion() is not None


def construir_asercion(
    empresa_id: int, usuario_id: int, *, ttl: int = TTL_ASERCION_SEGUNDOS
) -> str | None:
    """Devuelve la asercion firmada o ``None`` si no hay clave valida."""
    clave = clave_asercion()
    if clave is None:
        return None
    if isinstance(empresa_id, bool) or isinstance(usuario_id, bool):
        return None
    if not isinstance(empresa_id, int) or not isinstance(usuario_id, int):
        return None
    if empresa_id <= 0 or usuario_id <= 0:
        return None
    expira = int(time.time()) + int(ttl)
    nonce = secrets.token_hex(12)
    mensaje = f"{empresa_id}|{usuario_id}|{expira}|{nonce}"
    firma = hmac.new(clave, mensaje.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{mensaje}|{firma}"


def es_postgres(connection: Connection | Session) -> bool:
    """Solo PostgreSQL aplica RLS; SQLite se usa en pruebas offline unitarias."""
    if isinstance(connection, Connection):
        bind = connection
    else:
        bind = getattr(connection, "get_bind", lambda: None)()
    dialecto = getattr(getattr(bind, "dialect", None), "name", "")
    return dialecto == "postgresql"


def aplicar_asercion(session: Session, empresa_id: int, usuario_id: int) -> bool:
    """Fija la asercion en la transaccion ABIERTA de la sesion.

    Se ejecuta despues de validar JWT + membresia. El listener ``after_begin``
    reemite una asercion nueva cuando la misma Session abre otra transaccion.
    Devuelve ``False`` si no hay clave o el motor no es PostgreSQL.
    """
    if not es_postgres(session):
        return False
    asercion = construir_asercion(int(empresa_id), int(usuario_id))
    if asercion is None:
        return False
    session.execute(
        text("SELECT set_config('app.tenant_assertion', :asercion, true)"),
        {"asercion": asercion},
    )
    return True
