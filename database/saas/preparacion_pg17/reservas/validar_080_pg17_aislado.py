"""Instala y prueba CU11 en un clúster PostgreSQL 17 efímero.

No lee .env, no usa el puerto 5432 y destruye el clúster aunque falle.
"""
from __future__ import annotations

import json
import secrets
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg import sql

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
LAB = ROOT / "database/saas/laboratorio_pg17_5cb"
sys.path.insert(0, str(LAB))

import lab_instalar  # noqa: E402
from lab_comun import BASE, HOST, Cluster, LabError, correr, exe  # noqa: E402
from lab_pruebas import tokenizar  # noqa: E402
from lab_run_5cb import tokens_de  # noqa: E402

MIGR = ROOT / "database/saas/preparacion_pg17"
SQL = LAB / "sql"


def archivo_como(cluster: Cluster, ruta: Path, usuario: str, clave: str) -> str:
    base = str(cluster.detalles["base"])
    cmd = [exe("psql"), "-X", "-q", "-A", "-t", "-w",
           "-v", "ON_ERROR_STOP=1", "-h", HOST, "-p", str(cluster.port),
           "-U", usuario, "-d", base, "-f", str(ruta)]
    return correr(cmd, passwd=clave, timeout=600)


def archivo_debe_fallar(cluster: Cluster, ruta: Path,
                        usuario: str, clave: str, contiene: str) -> None:
    try:
        archivo_como(cluster, ruta, usuario, clave)
    except LabError as exc:
        if contiene not in str(exc):
            raise AssertionError(f"fallo inesperado: {exc}") from exc
        return
    raise AssertionError(f"{ruta.name} debía fallar")


POLICY_MOVIMIENTO = """
CREATE POLICY p_sp_tenant_movimiento_inventario
 ON public.movimiento_inventario FOR ALL TO fashionstore_sp_owner
 USING (empresa_id=(SELECT fashionstore_private.actor_empresa_id()))
 WITH CHECK (empresa_id=(SELECT fashionstore_private.actor_empresa_id()));
"""


def membresias_externas(root_dsn: str) -> str:
    return root_scalar(root_dsn, """
      SELECT coalesce(json_agg(json_build_object(
       'rol',granted.rolname,'grantor',grantor.rolname,
       'admin',m.admin_option,'inherit',m.inherit_option,'set',m.set_option)
       ORDER BY granted.rolname,grantor.rolname)::text,'[]')
      FROM pg_auth_members m
      JOIN pg_roles granted ON granted.oid=m.roleid
      JOIN pg_roles member ON member.oid=m.member
      JOIN pg_roles grantor ON grantor.oid=m.grantor
      WHERE member.rolname='fashionstore_installer'
       AND granted.rolname IN ('fashionstore_sp_owner','fashionstore_context_owner')
       AND grantor.rolname<>'fashionstore_installer'
    """)


def instalar_base(cluster: Cluster, secreto: str, *, negativos: bool = True
                  ) -> tuple[dict[str, str], str, str, str, str]:
    cluster.archivo(lab_instalar.BASELINE)
    for nombre in lab_instalar.MIGRACIONES:
        cluster.archivo(MIGR / nombre)
    cluster.archivo(lab_instalar.FIXTURES)
    cluster.archivo(lab_instalar.VAULT_SUSTITUTO)
    # 050a concede EXECUTE sobre esta función interna real de Supabase Vault.
    # El sustituto local no la usa, pero debe exponer la misma firma para que
    # la instalación de ACL se valide sin alterar 050a.
    cluster.sql("""
      CREATE FUNCTION vault._crypto_aead_det_decrypt(
        bytea,bytea,bigint,bytea,bytea
      ) RETURNS bytea LANGUAGE sql IMMUTABLE AS 'SELECT NULL::bytea';
      REVOKE ALL ON FUNCTION vault._crypto_aead_det_decrypt(
        bytea,bytea,bigint,bytea,bytea) FROM PUBLIC;
    """)
    lab_instalar.sembrar_vault(cluster, secreto)
    # El ejecutor real es CREATEROLE y propietario, pero no superusuario.
    # El bootstrap superuser local transfiere el baseline antes de 050.
    installer_pass = secrets.token_urlsafe(30)
    with psycopg.connect(cluster.dsn("postgres", cluster.passwd),
                         autocommit=True) as con:
        con.execute(sql.SQL(
            "CREATE ROLE fashionstore_installer LOGIN CREATEROLE NOSUPERUSER "
            "NOCREATEDB NOBYPASSRLS NOINHERIT PASSWORD {}"
        ).format(sql.Literal(installer_pass)))
        con.execute(sql.SQL("ALTER DATABASE {} OWNER TO fashionstore_installer")
                    .format(sql.Identifier(str(cluster.detalles["base"]))))
        con.execute("GRANT USAGE ON SCHEMA extensions,vault TO fashionstore_installer WITH GRANT OPTION")
        con.execute("GRANT SELECT ON vault.decrypted_secrets TO fashionstore_installer")
        con.execute("GRANT EXECUTE ON FUNCTION extensions.hmac(bytea,bytea,text) TO fashionstore_installer WITH GRANT OPTION")
        con.execute("GRANT EXECUTE ON FUNCTION vault._crypto_aead_det_decrypt(bytea,bytea,bigint,bytea,bytea) TO fashionstore_installer WITH GRANT OPTION")
        con.execute("""
          DO $own$
          DECLARE x record; kind text;
          BEGIN
            FOR x IN SELECT n.nspname,c.relname,c.relkind
              FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m')
            LOOP
              kind:=CASE x.relkind WHEN 'v' THEN 'VIEW'
                    WHEN 'm' THEN 'MATERIALIZED VIEW'
                    ELSE 'TABLE' END;
              EXECUTE format('ALTER %s %I.%I OWNER TO fashionstore_installer',
                             kind,x.nspname,x.relname);
            END LOOP;
            FOR x IN SELECT n.nspname,p.proname,p.prokind,
                            pg_get_function_identity_arguments(p.oid) args
              FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
              WHERE n.nspname IN ('public','fashionstore_private')
            LOOP
              kind:=CASE WHEN x.prokind='p' THEN 'PROCEDURE' ELSE 'FUNCTION' END;
              EXECUTE format('ALTER %s %I.%I(%s) OWNER TO fashionstore_installer',
                             kind,x.nspname,x.proname,x.args);
            END LOOP;
          END $own$;
        """)
    lab_instalar.generar_copias_lab()
    for clave in ("050a", "050b", "050c"):
        archivo_como(cluster, SQL / f"{clave}_lab.sql",
                     "fashionstore_installer", installer_pass)
    root_dsn = cluster.dsn("postgres", cluster.passwd)
    # PostgreSQL 17: membresías administrativas preexistentes, concedidas por
    # otro grantor, deben sobrevivir a los GRANT/REVOKE temporales de 080/082.
    root_sql(root_dsn, """
      GRANT fashionstore_sp_owner TO fashionstore_installer
       WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
      GRANT fashionstore_context_owner TO fashionstore_installer
       WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
    """)
    membresias_antes = membresias_externas(root_dsn)
    if '"set" : false' not in membresias_antes and '"set":false' not in membresias_antes:
        raise AssertionError(f"membresías externas inesperadas: {membresias_antes}")

    if negativos:
        # Policy empresarial ausente, incluido movimiento_inventario.
        root_sql(root_dsn, "DROP POLICY p_sp_tenant_movimiento_inventario ON public.movimiento_inventario")
        archivo_debe_fallar(
            cluster, HERE / "080_instalar_reservas_saas_PROPUESTA_NO_EJECUTAR.sql",
            "fashionstore_installer", installer_pass,
            "Policies RLS CU11 ausentes o incompatibles")
        root_sql(root_dsn, POLICY_MOVIMIENTO)
        # ACL mínima ausente.
        root_sql(root_dsn, "REVOKE INSERT ON public.movimiento_inventario FROM fashionstore_sp_owner")
        archivo_debe_fallar(
            cluster, HERE / "080_instalar_reservas_saas_PROPUESTA_NO_EJECUTAR.sql",
            "fashionstore_installer", installer_pass,
            "Owner SP sin ACL minima ya aprobada")
        root_sql(root_dsn, "GRANT INSERT ON public.movimiento_inventario TO fashionstore_sp_owner")
        # Owner inseguro.
        root_sql(root_dsn, "ALTER ROLE fashionstore_sp_owner LOGIN")
        archivo_debe_fallar(
            cluster, HERE / "080_instalar_reservas_saas_PROPUESTA_NO_EJECUTAR.sql",
            "fashionstore_installer", installer_pass,
            "Roles base ausentes o inseguros")
        root_sql(root_dsn, "ALTER ROLE fashionstore_sp_owner NOLOGIN")
        if root_scalar(root_dsn, "SELECT count(*) FROM pg_proc WHERE proname IN ('fs_verificar_actor_reserva','sp_crear_reserva_tenant','sp_cancelar_reserva_tenant','sp_finalizar_reserva_sin_compra_tenant')") != "0":
            raise AssertionError("un preflight negativo dejó rutinas parciales")
    # Deliberadamente NO instala el antiguo 070 del laboratorio.
    archivo_como(cluster, HERE / "080_instalar_reservas_saas_PROPUESTA_NO_EJECUTAR.sql",
                 "fashionstore_installer", installer_pass)
    logins = lab_instalar.provisionar_logins(cluster)
    if membresias_externas(root_dsn) != membresias_antes:
        raise AssertionError("080 alteró membresías externas SET FALSE")
    return ({k: v["dsn"] for k, v in logins.items()},
            root_dsn,
            cluster.dsn("fashionstore_installer", installer_pass),
            installer_pass, membresias_antes)


def root_scalar(dsn: str, query: str) -> str:
    with psycopg.connect(dsn, connect_timeout=10) as con:
        with con.cursor() as cur:
            cur.execute(query)
            return str(cur.fetchone()[0])


def root_sql(dsn: str, query: str) -> None:
    with psycopg.connect(dsn, connect_timeout=10) as con:
        con.execute(query)
        con.commit()


def call_crear(dsn: str, token: str, carrito: int, usuario: int) -> int:
    with psycopg.connect(dsn, connect_timeout=10) as con:
        with con.cursor() as cur:
            tokenizar(cur, token)
            cur.execute(
                "CALL public.sp_crear_reserva_tenant(%s,%s,%s,%s,%s,%s)",
                (1, carrito, usuario,
                 datetime.now(timezone.utc) + timedelta(days=2), "lab 080", None),
            )
            reserva = cur.fetchone()[0]
        con.commit()
    return int(reserva)


def preparar_concurrencia(root_dsn: str) -> None:
    root_sql(root_dsn, """
      BEGIN;
      DELETE FROM public.detalle_reserva WHERE reserva_id NOT IN (1,2);
      DELETE FROM public.reserva WHERE id NOT IN (1,2);
      UPDATE public.carrito SET estado='ACTIVO',fecha_actualizacion=clock_timestamp()
       WHERE id IN (1,4);
      UPDATE public.inventario SET stock_actual=1,stock_reservado=0 WHERE id=1;
      COMMIT;
    """)


def validar_reversion_limpia(resultados: dict[str, object]) -> None:
    cluster = Cluster.crear(BASE + "_reservas_082")
    try:
        secreto = secrets.token_urlsafe(48)
        _, root_dsn, _, installer_pass, membresias_antes = instalar_base(
            cluster, secreto, negativos=False)
        post = archivo_como(cluster, HERE / "081_verificar_reservas_saas.sql",
                            "fashionstore_installer", installer_pass).strip()
        if "CU11_POSTFLIGHT=PASS" not in post:
            raise AssertionError(f"postflight limpio inesperado: {post}")
        root_sql(root_dsn,
                 "CREATE FUNCTION public.marcador_preexistente_080() "
                 "RETURNS integer LANGUAGE sql AS 'SELECT 1'")
        comentario = root_scalar(root_dsn, """
          SELECT obj_description(to_regprocedure(
           'public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text)'),'pg_proc')
        """)
        root_sql(root_dsn, """
          COMMENT ON PROCEDURE public.sp_cancelar_reserva_tenant(
           bigint,bigint,bigint,text) IS 'alterado fuera de 080'
        """)
        archivo_debe_fallar(
            cluster, HERE / "082_revertir_reservas_saas_CONTROLADO.sql",
            "fashionstore_installer", installer_pass,
            "Procedencia o integridad CU11 no verificable")
        comentario_sql = comentario.replace("'", "''")
        root_sql(root_dsn, "COMMENT ON PROCEDURE "
                 "public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text) "
                 f"IS '{comentario_sql}'")
        resultados["pruebas"].append(
            "reversión negativa: marcador de integridad alterado bloqueado")
        tablas_antes = root_scalar(root_dsn, """
          SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname='public' AND c.relkind IN ('r','p')
        """)
        policies_antes = root_scalar(root_dsn,
                                     "SELECT count(*) FROM pg_policies WHERE schemaname='public'")
        filas_antes = root_scalar(root_dsn, "SELECT count(*) FROM public.reserva")
        archivo_como(cluster, HERE / "082_revertir_reservas_saas_CONTROLADO.sql",
                     "fashionstore_installer", installer_pass)
        if root_scalar(root_dsn, """
          SELECT count(*) FROM pg_proc WHERE oid IN (
           to_regprocedure('public.fs_verificar_actor_reserva(bigint,bigint)'),
           to_regprocedure('public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint)'),
           to_regprocedure('public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text)'),
           to_regprocedure('public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text)'))
        """) != "0":
            raise AssertionError("082 no eliminó exactamente las cuatro rutinas")
        if root_scalar(root_dsn, "SELECT to_regprocedure('public.marcador_preexistente_080()') IS NOT NULL") != "True":
            raise AssertionError("082 eliminó un objeto preexistente")
        if root_scalar(root_dsn, "SELECT count(*) FROM public.reserva") != filas_antes:
            raise AssertionError("082 alteró datos")
        if root_scalar(root_dsn, """
          SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname='public' AND c.relkind IN ('r','p')
        """) != tablas_antes:
            raise AssertionError("082 alteró tablas")
        if root_scalar(root_dsn,
                       "SELECT count(*) FROM pg_policies WHERE schemaname='public'") != policies_antes:
            raise AssertionError("082 alteró policies")
        if membresias_externas(root_dsn) != membresias_antes:
            raise AssertionError("082 alteró membresías externas SET FALSE")
        resultados["pruebas"].append(
            "reversión limpia: 4 rutinas fuera; tablas, datos, policies, marcador y membresías intactos")
    finally:
        cluster.destruir()


def main() -> int:
    cluster = None
    resultados: dict[str, object] = {"pruebas": []}
    try:
        cluster = Cluster.crear(BASE + "_reservas_080")
        secreto = secrets.token_urlsafe(48)
        dsn, root_dsn, installer_dsn, installer_pass, membresias_antes = instalar_base(
            cluster, secreto)
        resultados["clusters_efimeros"] = 2
        resultados["membresias_externas_set_false"] = json.loads(membresias_antes)
        resultados["negativas"] = {
            "080_sin_policy_movimiento": "BLOQUEADO",
            "080_sin_insert_movimiento": "BLOQUEADO",
            "080_owner_login": "BLOQUEADO",
            "081_policy_ausente": "FAIL_CON_EXIT_NO_CERO",
            "082_con_actividad": "BLOQUEADO",
            "082_integridad_alterada": "BLOQUEADO",
        }
        tokens = tokens_de(secreto)
        resultados["version"] = cluster.escalar("SHOW server_version")
        resultados["postflight"] = archivo_como(
            cluster, HERE / "081_verificar_reservas_saas.sql",
            "fashionstore_installer", installer_pass).strip()
        if "CU11_POSTFLIGHT=PASS" not in resultados["postflight"]:
            raise AssertionError(f"postflight inesperado: {resultados['postflight']}")
        resultados["pruebas"].extend([
            "preflight negativo: policy RLS ausente bloqueada",
            "preflight negativo: permiso INSERT ausente bloqueado",
            "preflight negativo: owner LOGIN inseguro bloqueado",
            "membresías externas SET FALSE preservadas por 080",
        ])

        # 081 debe imprimir FAIL y devolver código distinto de cero.
        root_sql(root_dsn,
                 "DROP POLICY p_sp_tenant_movimiento_inventario "
                 "ON public.movimiento_inventario")
        archivo_debe_fallar(
            cluster, HERE / "081_verificar_reservas_saas.sql",
            "fashionstore_installer", installer_pass, "CU11_POSTFLIGHT=FAIL")
        root_sql(root_dsn, POLICY_MOVIMIENTO)
        resultados["pruebas"].append(
            "postflight negativo: imprime FAIL y psql termina distinto de cero")

        metadata = cluster.escalar("""
          SELECT json_build_object(
           'procedimientos',(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='public' AND p.proname LIKE 'sp_%reserva%tenant'
               AND p.prokind='p' AND p.prosecdef
               AND pg_get_userbyid(p.proowner)='fashionstore_sp_owner'),
           'owner_bypass',(SELECT rolbypassrls FROM pg_roles WHERE rolname='fashionstore_sp_owner'),
           'public_execute',(SELECT count(*) FROM pg_proc p WHERE p.proname LIKE 'sp_%reserva%tenant'
             AND has_function_privilege('public',p.oid,'EXECUTE')),
           'set_temporal',(SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid
             JOIN pg_roles u ON u.oid=m.member WHERE u.rolname='fashionstore_installer'
             AND r.rolname IN ('fashionstore_sp_owner','fashionstore_context_owner') AND m.set_option))
        """)
        resultados["metadata"] = json.loads(metadata)

        preparar_concurrencia(root_dsn)
        barrera = threading.Barrier(2)
        salidas: list[tuple[str, object]] = []

        def competir(token: str, carrito: int, usuario: int) -> None:
            try:
                barrera.wait(timeout=10)
                salidas.append(("OK", call_crear(dsn["api"], token, carrito, usuario)))
            except psycopg.Error as exc:
                salidas.append(("ERROR", exc.sqlstate))

        h1 = threading.Thread(target=competir, args=(tokens["a2"], 1, 102))
        h2 = threading.Thread(target=competir, args=(tokens["a3"], 4, 103))
        h1.start(); h2.start(); h1.join(30); h2.join(30)
        if h1.is_alive() or h2.is_alive():
            raise AssertionError("concurrencia no terminó")
        if sorted(x[0] for x in salidas) != ["ERROR", "OK"]:
            raise AssertionError(f"resultado concurrente inesperado: {salidas}")
        if [x[1] for x in salidas if x[0] == "ERROR"] != ["23514"]:
            raise AssertionError(f"sqlstate concurrente inesperado: {salidas}")
        stock = root_scalar(root_dsn, "SELECT stock_reservado FROM public.inventario WHERE id=1")
        if stock != "1":
            raise AssertionError(f"sobreventa: stock_reservado={stock}")
        resultados["pruebas"].append("concurrencia: 1 PASS / 1 rechazo 23514")

        reserva = int(next(x[1] for x in salidas if x[0] == "OK"))
        carrito_ganador = int(root_scalar(root_dsn,
            f"SELECT carrito_id FROM public.reserva WHERE id={reserva}"))
        ganador = ((tokens["a2"], 102) if carrito_ganador == 1
                   else (tokens["a3"], 103))
        with psycopg.connect(dsn["api"], connect_timeout=10) as con:
            with con.cursor() as cur:
                tokenizar(cur, ganador[0])
                cur.execute("CALL public.sp_cancelar_reserva_tenant(%s,%s,%s,%s)",
                            (1, reserva, ganador[1], "cancelación lab"))
            con.commit()
        if root_scalar(root_dsn, f"SELECT estado FROM public.reserva WHERE id={reserva}") != "CANCELADA":
            raise AssertionError("cancelación no persistió")
        if root_scalar(root_dsn, "SELECT stock_reservado FROM public.inventario WHERE id=1") != "0":
            raise AssertionError("cancelación no liberó exactamente el stock")
        resultados["pruebas"].append("cancelación: libera exactamente una vez")

        # Una segunda cancelación no vuelve a liberar stock.
        try:
            with psycopg.connect(dsn["api"], connect_timeout=10) as con:
                with con.cursor() as cur:
                    tokenizar(cur, ganador[0])
                    cur.execute("CALL public.sp_cancelar_reserva_tenant(%s,%s,%s,%s)",
                                (1, reserva, ganador[1], "duplicada"))
            raise AssertionError("cancelación duplicada aceptada")
        except psycopg.Error as exc:
            if exc.sqlstate != "23514":
                raise
        if root_scalar(root_dsn, "SELECT stock_reservado FROM public.inventario WHERE id=1") != "0":
            raise AssertionError("cancelación duplicada alteró stock")
        resultados["pruebas"].append("idempotencia cancelación: rechazo 23514, stock intacto")

        # Rollback completo: el SP alcanza el INSERT de reserva, pero una línea
        # sin stock debe revertir reserva, detalle, movimiento y carrito.
        carrito_perdedor = 4 if carrito_ganador == 1 else 1
        usuario_perdedor = 103 if carrito_perdedor == 4 else 102
        token_perdedor = tokens["a3"] if carrito_perdedor == 4 else tokens["a2"]
        root_sql(root_dsn, f"""
          UPDATE public.detalle_carrito SET cantidad=2
           WHERE carrito_id={carrito_perdedor};
          UPDATE public.carrito SET estado='ACTIVO',fecha_actualizacion=clock_timestamp()
           WHERE id={carrito_perdedor};
        """)
        filas_previas = root_scalar(root_dsn, "SELECT count(*) FROM public.reserva")
        try:
            call_crear(dsn["api"], token_perdedor, carrito_perdedor, usuario_perdedor)
            raise AssertionError("reserva sin stock aceptada")
        except psycopg.Error as exc:
            if exc.sqlstate != "23514":
                raise
        if root_scalar(root_dsn, "SELECT count(*) FROM public.reserva") != filas_previas:
            raise AssertionError("rollback dejó reserva parcial")
        if root_scalar(root_dsn, f"SELECT estado FROM public.carrito WHERE id={carrito_perdedor}") != "ACTIVO":
            raise AssertionError("rollback convirtió el carrito")
        resultados["pruebas"].append("rollback: sin reserva parcial ni cambio de carrito/stock")

        # Finalización sin compra: personal autorizado libera exactamente el
        # stock de una reserva CONFIRMADA y la deja ATENDIDA.
        root_sql(root_dsn, """
          WITH m AS (
            INSERT INTO public.modulo(nombre,estado) VALUES('RESERVAS LAB 080',TRUE)
            RETURNING id
          ), f AS (
            INSERT INTO public.funcion(modulo_id,nombre,estado)
            SELECT id,'GESTIONAR_RESERVAS',TRUE FROM m RETURNING id
          ), a AS (
            INSERT INTO public.accion(nombre,estado) VALUES('EJECUTAR',TRUE)
            RETURNING id
          )
          INSERT INTO public.rol_funcion(rol_id,funcion_id,accion_id)
          SELECT 1,f.id,a.id FROM f CROSS JOIN a;
          UPDATE public.reserva SET estado='CONFIRMADA' WHERE id=1;
          UPDATE public.inventario SET stock_reservado=1 WHERE id=2;
        """)
        with psycopg.connect(dsn["api"], connect_timeout=10) as con:
            with con.cursor() as cur:
                tokenizar(cur, tokens["a1"])
                cur.execute(
                    "CALL public.sp_finalizar_reserva_sin_compra_tenant(%s,%s,%s,%s)",
                    (1, 1, 101, "finalizada lab"),
                )
            con.commit()
        if root_scalar(root_dsn, "SELECT estado FROM public.reserva WHERE id=1") != "ATENDIDA":
            raise AssertionError("finalización no cambió a ATENDIDA")
        if root_scalar(root_dsn, "SELECT stock_reservado FROM public.inventario WHERE id=2") != "0":
            raise AssertionError("finalización no liberó stock")
        resultados["pruebas"].append("finalización sin compra: ATENDIDA y stock liberado")

        # Actor cruzado: la empresa y el usuario declarados no pueden diferir del JWT.
        try:
            call_crear(dsn["api"], tokens["b1"], 1, 102)
            raise AssertionError("actor cruzado aceptado")
        except psycopg.Error as exc:
            if exc.sqlstate != "42501":
                raise
        resultados["pruebas"].append("actor cruzado: rechazo 42501")

        # Con actividad comercial posterior al marcador, 082 debe negarse sin
        # eliminar nada. La reversión positiva se prueba en otro clúster limpio.
        archivo_debe_fallar(
            cluster, HERE / "082_revertir_reservas_saas_CONTROLADO.sql",
            "fashionstore_installer", installer_pass,
            "secuencias CU11 avanzaron")
        if root_scalar(root_dsn, """
          SELECT count(*) FROM pg_proc WHERE proname IN (
           'fs_verificar_actor_reserva','sp_crear_reserva_tenant',
           'sp_cancelar_reserva_tenant','sp_finalizar_reserva_sin_compra_tenant')
        """) != "4":
            raise AssertionError("082 parcial tras bloquear actividad comercial")
        resultados["pruebas"].append(
            "reversión con actividad: bloqueada sin cambios parciales")
        if membresias_externas(root_dsn) != membresias_antes:
            raise AssertionError("actividad o 082 fallido alteró membresías externas")

        cluster.destruir()
        cluster = None
        validar_reversion_limpia(resultados)
        resultados["estado"] = "PASS"
        return 0
    except Exception as exc:
        resultados["estado"] = "FAIL"
        resultados["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (HERE / "resultado_validacion_pg17.json").write_text(
            json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
        if cluster is not None:
            cluster.destruir()
        print(json.dumps(resultados, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
