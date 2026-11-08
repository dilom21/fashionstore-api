"""Prueba futura de identidad firmada y reutilización de conexión.

NO se ejecuta automáticamente. Requiere una base desechable con 050a-c ya
instalados y variables de entorno; nunca imprime DSN ni tokens.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError


DSN = os.environ.get("FASHIONSTORE_SECURITY_TEST_DSN")
TOKENS = {
    "a1": os.environ.get("FASHIONSTORE_TEST_TOKEN_A1"),
    "a2": os.environ.get("FASHIONSTORE_TEST_TOKEN_A2"),
    "b1": os.environ.get("FASHIONSTORE_TEST_TOKEN_B1"),
}
if not DSN or not all(TOKENS.values()):
    raise SystemExit("Faltan DSN/tokens de laboratorio en variables de entorno")


def actor(conn: Connection, token: str) -> tuple[int, int, int]:
    with conn.begin():
        conn.execute(
            text("SELECT set_config('app.actor_token',:token,true)"),
            {"token": token},
        )
        # GUCs de IDs manipulados no deben modificar el actor firmado.
        conn.execute(text("SELECT set_config('app.empresa_id','999999999',true)"))
        conn.execute(text("SELECT set_config('app.usuario_id','999999999',true)"))
        return tuple(conn.execute(text(
            "SELECT fashionstore_private.actor_empresa_id(),"
            "fashionstore_private.actor_usuario_id(),count(*) "
            "FROM public.empresa"
        )).one())


engine = create_engine(DSN, pool_pre_ping=True, pool_size=1, max_overflow=1)
try:
    resultados: dict[str, tuple[int, int, int]] = {}
    # max_size=1 efectivo durante esta secuencia: misma conexión física puede
    # servir A1 -> B1 -> A2 sin conservar contexto al terminar transacción.
    for nombre in ("a1", "b1", "a2"):
        with engine.connect() as conn:
            residual = conn.scalar(text(
                "SELECT nullif(current_setting('app.actor_token',true),'')"
            ))
            assert residual is None, "token residual entre transacciones"
            resultados[nombre] = actor(conn, TOKENS[nombre])
            assert resultados[nombre][2] == 1, "RLS no limita empresa visible"

    assert resultados["a1"][0] == resultados["a2"][0]
    assert resultados["a1"][1] != resultados["a2"][1]
    assert resultados["a1"][0] != resultados["b1"][0]

    # Dos conexiones simultáneas no deben intercambiar identidad.
    def concurrente(nombre: str):
        with engine.connect() as conn:
            return nombre, actor(conn, TOKENS[nombre])

    with ThreadPoolExecutor(max_workers=2) as executor:
        pares = dict(executor.map(concurrente, ("a1", "b1")))
    assert pares["a1"][0] != pares["b1"][0]

    # Firma alterada: la consulta debe fallar cerrada con insufficient_privilege.
    with engine.connect() as conn:
        try:
            actor(conn, TOKENS["a1"][:-1] + ("A" if TOKENS["a1"][-1] != "A" else "B"))
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != "42501":
                raise
        else:
            raise AssertionError("JWT alterado fue aceptado")
finally:
    engine.dispose()

print("PASS: identidad JWT, GUC local, reutilización y concurrencia")
