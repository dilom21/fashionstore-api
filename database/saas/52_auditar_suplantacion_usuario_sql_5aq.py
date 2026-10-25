"""5AQ: prueba NEGATIVA del actor SQL compartido dentro del mismo tenant.

Seguridad bajo prueba: dos usuarios finales A1/A2 comparten principal
PostgreSQL fashionstore_5ab_a. Sin contexto firmado verificado POR EL SP,
la comprobacion de membresia+RLS de empresa puede aceptar p_usuario_id=A2
aunque el solicitante original sea A1.

El resultado ESPERADO demuestra una VULNERABILIDAD PENDIENTE; no es una
certificacion de aislamiento. Este archivo no da soluciones de despliegue.
SOLO LAB 127.0.0.1:55432 / fashionstore_rls_lab_5u, 0 COMMIT.
"""
from __future__ import annotations

import getpass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import runpy
import sys

import psycopg

ROOT = Path(__file__).resolve().parent
mod = runpy.run_path(str(ROOT / "49_ensayo_sp_tenant_reversible_5an.py"))
preflight = mod["preflight"]
load_sources = mod["sources"]
fixtures = mod["fixtures"]
grants = mod["grant_minimal_lab"]
role_a = mod["as_role"]
one = mod["one"]
ensure = mod["ensure"]
CALL_CREATE = mod["CALL_CREATE"]
A = mod["A"]
HOST, PORT, DB = mod["HOST"], mod["PORT"], mod["DB"]


def segundo_cliente_con_carrito(conn, actores):
    """Preparar identidad A2 legítima con carrito propio de 1 unidad."""
    rol_id = one(conn, """SELECT id FROM public.rol
                          WHERE nombre='CLIENTE' AND estado IS TRUE""")
    segundo = one(conn, """INSERT INTO public.usuario(
        rol_id,correo,password_hash,estado)
        VALUES(%s,'cliente-a2-5aq@demo.invalid',
               '!SIN-LOGIN-PRUEBA-5AQ',TRUE) RETURNING id""", (rol_id,))
    conn.execute("""INSERT INTO public.usuario_empresa(
        empresa_id,usuario_id,rol_id,estado)
        VALUES(%s,%s,%s,TRUE)""",
        (actores["a_empresa"],segundo,rol_id))
    cliente_id = one(conn, """INSERT INTO public.cliente(
        empresa_id,usuario_id,nombre,apellido,estado)
        VALUES(%s,%s,'Cliente A2','5AQ',TRUE) RETURNING id""",
        (actores["a_empresa"],segundo))
    sucursal = one(conn, """SELECT sucursal_id FROM public.carrito
        WHERE id=%s AND empresa_id=%s""",
        (actores["a_carrito"],actores["a_empresa"]))
    carrito = one(conn,"""INSERT INTO public.carrito(
        empresa_id,cliente_id,sucursal_id,estado)
        VALUES(%s,%s,%s,'ACTIVO') RETURNING id""",
        (actores["a_empresa"],cliente_id,sucursal))
    conn.execute("""INSERT INTO public.detalle_carrito(
        empresa_id,carrito_id,inventario_id,cantidad)
        VALUES(%s,%s,%s,1)""",
        (actores["a_empresa"],carrito,actores["a_inventario"]))
    return segundo,carrito


def demostrar_limite(conn, actores, usuario_a2, carrito_a2):
    """Mismo session_user: llamada A1 denegada; A2 declarado aceptado."""
    empresa = actores["a_empresa"]
    usuario_a1 = actores["a_usuario"]
    ensure(usuario_a1 != usuario_a2, "identidades de prueba duplicadas")
    role_a(conn,A)
    ensure(one(conn,"SELECT session_user")==A,"principal SQL A incorrecto")
    ensure(one(conn,"SELECT count(*) FROM public.empresa")==1,
           "RLS empresarial no efectivo")
    ensure(one(conn,"SELECT count(*) FROM public.usuario WHERE id=%s",
               (usuario_a1,))==1,"usuario A1 oculto")
    ensure(one(conn,"SELECT count(*) FROM public.usuario WHERE id=%s",
               (usuario_a2,))==1,"usuario A2 oculto")
    fecha=datetime.now(timezone.utc)+timedelta(days=2)

    # SP funciona como se espera si A1 indica su identidad y quiere el
    # carrito de A2: el JOIN carrito.cliente.usuario_id la rechaza.
    conn.execute("SAVEPOINT sp_denegado")
    try:
        conn.execute(CALL_CREATE,
                     (empresa,carrito_a2,usuario_a1,fecha,"A1 no es A2",None))
    except psycopg.Error as error:
        code=error.sqlstate
        conn.execute("ROLLBACK TO SAVEPOINT sp_denegado")
        ensure(code=="P0002","error distinto a carrito ajeno: "+str(code))
        print("5AQ CONTROL PASS: A1 declarado no reserva carrito de A2")
    else:
        # Nunca consolidar el caso inesperado; revertir incluso sin error.
        conn.execute("ROLLBACK TO SAVEPOINT sp_denegado")
        raise RuntimeError("5AQ BLOQUEADO: SP aceptó un carrito ajeno con A1")
    conn.execute("RELEASE SAVEPOINT sp_denegado")

    # MISMO login SQL A. No se exige vincular este principal A a A1/A2:
    # un actor con acceso SQL directo puede elegir p_usuario_id=A2.
    # Esto NO significa que HTTP/JWT permita falsificar sub; el problema
    # es la ejecución directa del SP con credenciales SQL compartidas.
    ensure(one(conn,"SELECT session_user")==A,
           "cambió principal entre intentos")
    respuesta=conn.execute(CALL_CREATE,
        (empresa,carrito_a2,usuario_a2,fecha,"Actor A2 declarado",None)).fetchone()
    ensure(respuesta is not None and type(respuesta[0]) is int
           and respuesta[0]>0,"suplantación no quedó demostrada")
    rid=respuesta[0]
    ensure(one(conn,"""SELECT count(*) FROM public.reserva r
        JOIN public.cliente cl ON cl.id=r.cliente_id
         AND cl.empresa_id=r.empresa_id
        WHERE r.id=%s AND r.empresa_id=%s
         AND cl.usuario_id=%s""",(rid,empresa,usuario_a2))==1,
        "reserva directa no pertenece a A2")
    ensure(one(conn,"SELECT stock_reservado FROM public.inventario WHERE id=%s",
               (actores["a_inventario"],))==5,
        "stock no reservado por el CALL de A2")
    print("5AQ BRECHA DEMOSTRADA: el MISMO session_user A admitió "
          "p_usuario_id=A2 en CALL directo")
    print("5AQ HTTP/JWT no vulnerado: la ausencia es un vínculo "
          "criptográfico o principal individual verificado DENTRO del SP")


def main():
    print("5AQ: TEST NEGATIVO INTRAEMPRESA, SOLO LAB, ROLLBACK")
    sqls = load_sources()
    pw = getpass.getpass("Contrasena postgres SOLO LAB 55432: ")
    with psycopg.connect(
        host=HOST, port=PORT, dbname=DB, user="postgres",
        password=pw, sslmode="disable", connect_timeout=8, autocommit=True,
        options="-c statement_timeout=25000 -c lock_timeout=4000",
    ) as c:
        preflight(c)
        print("5AQ PREFLIGHT OK: 2 tenants ficticios en 55432")
        try:
            c.execute("BEGIN")
            c.execute("SET LOCAL statement_timeout='25s'")
            c.execute("SET LOCAL lock_timeout='4s'")
            actores=fixtures(c)
            a2,carrito2=segundo_cliente_con_carrito(c,actores)
            for sql in sqls.values():
                c.execute(sql)
            grants(c)
            ensure(one(c,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")==52,
                   "políticas RLS temporales inesperadas")
            demostrar_limite(c,actores,a2,carrito2)
        finally:
            c.execute("ROLLBACK")
            preflight(c)
            print("5AQ ROLLBACK VERIFICADO: 0 SP finales, "
                  "41 policies, 3 reservas originales, 4 carritos originales")
        print("5AQ CONCLUSION: prohibido habilitar acceso SQL directo a SP "
              "con un único principal compartido por empresa")


if __name__=="__main__":
    try:
        main()
    except Exception as error:
        print(f"5AQ ERROR: {type(error).__name__}: {error}",file=sys.stderr)
        sys.exit(2)
