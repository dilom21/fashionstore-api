"""5BS / Etapa J - Readiness explicita de los laboratorios 5BM / 5BN / 5BO.

Problema que resuelve
---------------------
Los modulos `test_operativo_saas_5bm.py`, `test_compras_devoluciones_5bn.py` y
`test_nucleo_saas_5bo.py` omiten la bateria cuando NO existe el manifiesto, pero
si el manifiesto existe y su cluster esta apagado (situacion tras reiniciar el
equipo) la suite intenta conectar y produce decenas de ERRORES de
`ConnectionTimeout`. Eso no es un fallo funcional: es una precondicion de
entorno no declarada.

Contrato
--------
* Si el manifiesto existe y el cluster responde y coincide con la base/puerto
  declarados -> la bateria se ejecuta COMPLETA (ninguna asercion se relaja).
* Si el manifiesto existe pero el cluster no responde o no coincide ->
  `pytest.skip` con un motivo explicito que nombra el puerto y el comando que
  aprovisiona el laboratorio. Un skip NO cuenta como PASS.
* Solo se admiten el puerto canonico historico del modulo o el rango 5BS
  (55449-55459) reservado para laboratorios nuevos. Cualquier otro puerto sigue
  rechazado.
"""
from __future__ import annotations

import socket

PUERTOS_5BS = frozenset(range(55449, 55460))
PUERTOS_PROTEGIDOS = frozenset({5432, 55432, 55433, 55434, 55435, 55436, 55437,
                                55438, 55439, 55440, 55441, 55442, 55443, 55444,
                                55445, 55446, 55447, 55448})


def _escucha(host: str, puerto: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, int(puerto)), timeout=timeout):
            return True
    except OSError:
        return False


def motivo_no_disponible(datos: dict, puerto_canonico: int, etiqueta: str,
                         comando: str) -> str | None:
    """Devuelve el motivo del skip, o None si el laboratorio esta operativo."""
    cluster = (datos or {}).get("cluster") or {}
    host = cluster.get("host")
    puerto = cluster.get("puerto")
    base = str(cluster.get("base") or "")

    if host not in {"127.0.0.1", "localhost"}:
        return f"El manifiesto {etiqueta} no apunta a loopback"
    if puerto != puerto_canonico and puerto not in PUERTOS_5BS:
        return (f"El manifiesto {etiqueta} usa el puerto {puerto}, fuera del "
                f"contrato (canonico {puerto_canonico} o rango 5BS 55449-55459)")
    if puerto in PUERTOS_PROTEGIDOS and puerto != puerto_canonico:
        return f"El puerto {puerto} esta protegido por las etapas previas"
    if "lab" not in base.lower():
        return f"El manifiesto {etiqueta} no apunta a una base descartable"

    if not _escucha(host, puerto):
        return (f"Laboratorio {etiqueta} declarado en el manifiesto pero el "
                f"cluster no escucha en {host}:{puerto} (manifiesto obsoleto). "
                f"Aprovisione uno nuevo con: {comando}")

    try:
        import psycopg
    except ImportError:  # pragma: no cover - psycopg es dependencia del proyecto
        return None

    dsn = datos.get("admin_dsn")
    if not dsn:
        return None
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            fila = conn.execute(
                "SELECT current_database(), current_setting('port')").fetchone()
    except Exception as exc:  # noqa: BLE001 - se informa la clase, no el DSN
        return (f"Laboratorio {etiqueta} no acepta la conexion declarada "
                f"({type(exc).__name__}); reaprovisione con: {comando}")
    base_efectiva, puerto_efectivo = fila
    if str(base_efectiva) != base or str(puerto_efectivo) != str(puerto):
        return (f"El cluster en {host}:{puerto} no corresponde al manifiesto "
                f"{etiqueta} (base/puerto distintos); reaprovisione con: {comando}")
    return None
