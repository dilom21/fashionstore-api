"""Extracción centralizada de la IP del cliente.

La IP visible para el backend se resuelve una sola vez por petición HTTP y
luego se propaga a PostgreSQL mediante el ajuste de sesión ``app.ip``, que el
trigger ``trg_bitacora_completar_ip`` consume al insertar en ``public.bitacora``.

Orden de resolución:
    1. ``X-Forwarded-For``: primera dirección válida.
    2. ``X-Real-IP``.
    3. ``request.client.host``.

Cada candidato se valida con :func:`ipaddress.ip_address`; un valor que no sea
una IP real se descarta (nunca se inventa ni se acepta texto arbitrario).
"""

from ipaddress import ip_address

from fastapi import Request

_HEADER_X_FORWARDED_FOR = "x-forwarded-for"
_HEADER_X_REAL_IP = "x-real-ip"


def _normalizar_ip(candidato: str | None) -> str | None:
    """Devuelve la IP canónica si ``candidato`` es válido; si no, ``None``."""
    if candidato is None:
        return None
    valor = candidato.strip()
    if not valor:
        return None
    try:
        return str(ip_address(valor))
    except ValueError:
        return None


def obtener_ip_cliente(request: Request) -> str | None:
    """Resuelve la IP del cliente visible para el backend."""
    reenviadas = request.headers.get(_HEADER_X_FORWARDED_FOR)
    if reenviadas:
        for parte in reenviadas.split(","):
            ip = _normalizar_ip(parte)
            if ip is not None:
                return ip

    ip_real = _normalizar_ip(request.headers.get(_HEADER_X_REAL_IP))
    if ip_real is not None:
        return ip_real

    cliente = request.client
    if cliente is not None:
        return _normalizar_ip(cliente.host)

    return None
