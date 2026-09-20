"""Pruebas de la IP del cliente en bitácora (extracción + contexto SQLAlchemy).

Cubren la resolución centralizada de la IP (X-Forwarded-For, X-Real-IP,
request.client.host y validación) y la propagación a PostgreSQL mediante
``app.ip`` a través del listener ``after_begin``.

No dependen de backfill: el trigger ``trg_bitacora_completar_ip`` se aplica
con SQL_Correccion_Bitacora_IP.sql y solo afecta a INSERT nuevos.
"""

from sqlalchemy import text
from starlette.requests import Request

from app.core.database import SessionLocal, get_db
from app.core.request_ip import obtener_ip_cliente


def _request(
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("10.0.0.1", 12345),
) -> Request:
    raw_headers = [
        (clave.lower().encode("latin-1"), valor.encode("latin-1"))
        for clave, valor in (headers or {}).items()
    ]
    scope: dict = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": raw_headers,
    }
    if client is not None:
        scope["client"] = client
    return Request(scope)


# ---------------------------------------------------------------------------
# Extracción de la IP
# ---------------------------------------------------------------------------


def test_xff_simple():
    request = _request({"X-Forwarded-For": "203.0.113.9"})
    assert obtener_ip_cliente(request) == "203.0.113.9"


def test_xff_cadena_toma_primera_ip_valida():
    request = _request(
        {"X-Forwarded-For": "203.0.113.9, 198.51.100.4, 10.0.0.1"}
    )
    assert obtener_ip_cliente(request) == "203.0.113.9"


def test_xff_cadena_descarta_entradas_invalidas():
    request = _request(
        {"X-Forwarded-For": "desconocido, 198.51.100.4, 10.0.0.1"}
    )
    assert obtener_ip_cliente(request) == "198.51.100.4"


def test_x_real_ip():
    request = _request({"X-Real-IP": "198.51.100.23"})
    assert obtener_ip_cliente(request) == "198.51.100.23"


def test_xff_tiene_prioridad_sobre_x_real_ip():
    request = _request(
        {
            "X-Forwarded-For": "203.0.113.9",
            "X-Real-IP": "198.51.100.23",
        }
    )
    assert obtener_ip_cliente(request) == "203.0.113.9"


def test_fallback_request_client_host():
    request = _request({}, client=("192.0.2.77", 5000))
    assert obtener_ip_cliente(request) == "192.0.2.77"


def test_ipv6_se_normaliza():
    request = _request({"X-Forwarded-For": "::1"})
    assert obtener_ip_cliente(request) == "::1"


def test_valor_invalido_devuelve_none():
    request = _request({"X-Forwarded-For": "no-es-una-ip"}, client=None)
    assert obtener_ip_cliente(request) is None


def test_sin_fuentes_devuelve_none():
    request = _request({}, client=None)
    assert obtener_ip_cliente(request) is None


# ---------------------------------------------------------------------------
# Contexto SQLAlchemy
# ---------------------------------------------------------------------------


def test_get_db_almacena_request_ip():
    generador = get_db(_request({"X-Forwarded-For": "203.0.113.9"}))
    db = next(generador)
    try:
        assert db.info["request_ip"] == "203.0.113.9"
    finally:
        generador.close()


def test_get_db_sin_ip_valida_guarda_none():
    generador = get_db(_request({"X-Forwarded-For": "invalida"}, client=None))
    db = next(generador)
    try:
        assert db.info["request_ip"] is None
    finally:
        generador.close()


def test_after_begin_configura_app_ip():
    session = SessionLocal()
    try:
        session.info["request_ip"] = "203.0.113.9"
        valor = session.execute(
            text("SELECT current_setting('app.ip', TRUE)")
        ).scalar()
        assert valor == "203.0.113.9"
    finally:
        session.rollback()
        session.close()


def test_after_begin_sin_request_ip_no_configura_app_ip():
    session = SessionLocal()
    try:
        valor = session.execute(
            text("SELECT current_setting('app.ip', TRUE)")
        ).scalar()
        assert valor in (None, "")
    finally:
        session.rollback()
        session.close()


def test_ip_canonica_se_normaliza():
    request = _request(
        {"X-Forwarded-For": "2001:0db8:0000:0000:0000:0000:0000:0001"}
    )
    assert obtener_ip_cliente(request) == "2001:db8::1"


def test_ip_con_ceros_izquierdos_se_descarta():
    request = _request({"X-Forwarded-For": "203.000.113.009"}, client=None)
    assert obtener_ip_cliente(request) is None
