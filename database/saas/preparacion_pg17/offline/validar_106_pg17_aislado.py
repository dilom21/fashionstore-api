"""Valida 106 CU19/CU20 en PostgreSQL 17 aislado; nunca usa Supabase."""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RES = ROOT / "database/saas/preparacion_pg17/reservas"
CU13 = ROOT / "database/saas/preparacion_pg17/cu12_cu13"
LAB = ROOT / "database/saas/laboratorio_pg17_5cb"
sys.path[:0] = [str(ROOT), str(RES), str(LAB)]
from validar_080_pg17_aislado import (  # noqa: E402
    archivo_como, instalar_base, root_scalar, root_sql,
)
from lab_comun import BASE, Cluster, claims_tenant, jwt_hs256  # noqa: E402
from lab_pruebas import tokenizar  # noqa: E402
from lab_run_5cb import tokens_de  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.tenant_context import TenantContext  # noqa: E402
from app.modules.offline.schemas import OperacionOfflineRequest  # noqa: E402
from app.modules.offline.service import OfflineService  # noqa: E402


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _call(dsn: str, token: str, consulta: str, args):
    with psycopg.connect(dsn) as c:
        with c.cursor() as cur:
            tokenizar(cur, token)
            cur.execute(consulta, args)
            fila = cur.fetchone()
        c.commit()
        return fila


def _error(dsn: str, token: str, consulta: str, args, esperado: str) -> None:
    try:
        _call(dsn, token, consulta, args)
    except psycopg.Error as exc:
        if exc.sqlstate != esperado:
            raise AssertionError(
                f"SQLSTATE {exc.sqlstate} != {esperado}: {exc}"
            ) from exc
    else:
        raise AssertionError("operacion insegura aceptada")


def main() -> int:
    cluster = Cluster.crear(BASE + "_cu19_cu20")
    try:
        import secrets
        secreto = secrets.token_urlsafe(48)
        dsns, root, installer, clave, _ = instalar_base(
            cluster, secreto, negativos=False
        )
        archivo_como(
            cluster,
            CU13 / "090_instalar_cu12_cu13_saas_PROPUESTA_NO_EJECUTAR.sql",
            "fashionstore_installer", clave,
        )
        # El owner del sandbox tiene CREATE pero no USAGE por defecto; el
        # instalador real de Supabase (postgres) ya posee ambos.
        root_sql(root, "GRANT USAGE ON SCHEMA fashionstore_private "
                       "TO fashionstore_installer")
        salida = archivo_como(
            cluster, HERE / "106_offline_cu19_cu20_PROPUESTA_NO_EJECUTAR.sql",
            "fashionstore_installer", clave,
        )
        assert "CU19_CU20_OFFLINE_INSTALL=PASS" in salida, salida
        tokens = tokens_de(secreto)
        api = dsns["api"]
        token_a2 = tokens["a2"]

        # Migracion de carrito: existentes ONLINE, dos ONLINE no, OFFLINE si.
        assert root_scalar(
            root, "SELECT count(*) FROM public.carrito WHERE origen IS NULL"
        ) == "0"
        assert root_scalar(
            root, "SELECT origen FROM public.carrito WHERE id=1"
        ) == "ONLINE"
        with psycopg.connect(root) as c:
            c.execute("UPDATE public.carrito SET estado='EXPIRADO' "
                      "WHERE empresa_id=1 AND cliente_id=102")
            c.execute("""
              INSERT INTO public.carrito(
                empresa_id,cliente_id,sucursal_id,estado,origen)
              VALUES(1,102,11,'ACTIVO','ONLINE')
            """)
            c.execute("""
              INSERT INTO public.carrito(
                empresa_id,cliente_id,sucursal_id,estado,origen)
              VALUES(1,102,11,'ACTIVO','OFFLINE')
            """)
            try:
                c.execute("""
                  INSERT INTO public.carrito(
                    empresa_id,cliente_id,sucursal_id,estado,origen)
                  VALUES(1,102,11,'ACTIVO','ONLINE')
                """)
            except psycopg.errors.UniqueViolation:
                pass
            else:
                raise AssertionError("segundo carrito ONLINE activo aceptado")
            c.rollback()

        # Funciones: begin nuevo, finish ACEPTADA, get, replay y conflicto.
        op = str(uuid.uuid4())
        h = _hash("payload-1")
        fila = _call(api, token_a2,
            "SELECT * FROM fashionstore_private.fn_offline_begin("
            "%s,%s,%s::uuid,%s,%s,%s,%s)",
            (1, 102, op, "VENTA_CONTADO", h, 11, "100.00"))
        assert fila[0] is True and fila[1] == "ENVIANDO", fila
        _call(api, token_a2,
            "SELECT fashionstore_private.fn_offline_finish("
            "%s,%s,%s::uuid,'ACEPTADA',%s,%s::jsonb,NULL,NULL)",
            (1, 102, op, 999, json.dumps({"venta_id": 999})))
        fila = _call(api, token_a2,
            "SELECT * FROM fashionstore_private.fn_offline_get(%s,%s,%s::uuid)",
            (1, 102, op))
        assert fila[0] == "VENTA_CONTADO" and fila[1] == "ACEPTADA", fila
        assert fila[2] == 999 and fila[3]["venta_id"] == 999, fila

        fila = _call(api, token_a2,
            "SELECT * FROM fashionstore_private.fn_offline_begin("
            "%s,%s,%s::uuid,%s,%s,%s,%s)",
            (1, 102, op, "VENTA_CONTADO", h, 11, "100.00"))
        assert fila[0] is False and fila[1] == "ACEPTADA", fila
        fila = _call(api, token_a2,
            "SELECT * FROM fashionstore_private.fn_offline_begin("
            "%s,%s,%s::uuid,%s,%s,%s,%s)",
            (1, 102, op, "VENTA_CONTADO", _hash("otro"), 11, "100.00"))
        assert fila[0] is False and fila[1] == "CONFLICTO", fila
        assert fila[4] == "UUID_PAYLOAD_MISMATCH", fila

        # Rechazo con efectivo conservado para conciliacion.
        op2 = str(uuid.uuid4())
        fila = _call(api, token_a2,
            "SELECT * FROM fashionstore_private.fn_offline_rechazar("
            "%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s)",
            (1, 102, op2, "VENTA_CONTADO", _hash("payload-2"), 11,
             "100.00", "RECHAZADA", "STOCK", "sin stock"))
        assert fila[0] == "RECHAZADA", fila
        fila = _call(api, token_a2,
            "SELECT efectivo_recibido FROM fashionstore_private.fn_offline_get("
            "%s,%s,%s::uuid)", (1, 102, op2))
        assert str(fila[0]) == "100.00", fila

        # Actor ajeno y acceso directo al ledger deben fallar.
        _error(api, token_a2,
            "SELECT * FROM fashionstore_private.fn_offline_get(%s,%s,%s::uuid)",
            (1, 103, op2), "42501")
        _error(api, token_a2,
            "SELECT * FROM fashionstore_private.offline_operacion", (), "42501")

        # Rollback: el begin no deja fila ni bloquea el reintento.
        op3 = str(uuid.uuid4())
        h3 = _hash("payload-3")
        with psycopg.connect(api) as c:
            c.autocommit = False
            with c.cursor() as cur:
                tokenizar(cur, token_a2)
                cur.execute(
                    "SELECT * FROM fashionstore_private.fn_offline_begin("
                    "%s,%s,%s::uuid,%s,%s,%s,%s)",
                    (1, 102, op3, "RESERVA", h3, 11, None))
                assert cur.fetchone()[0] is True
            c.rollback()
        with psycopg.connect(api) as c:
            with c.cursor() as cur:
                tokenizar(cur, token_a2)
                cur.execute(
                    "SELECT * FROM fashionstore_private.fn_offline_get("
                    "%s,%s,%s::uuid)", (1, 102, op3))
                assert cur.fetchone() is None
        fila = _call(api, token_a2,
            "SELECT * FROM fashionstore_private.fn_offline_begin("
            "%s,%s,%s::uuid,%s,%s,%s,%s)",
            (1, 102, op3, "RESERVA", h3, 11, None))
        assert fila[0] is True

        # Concurrencia: el segundo begin espera y recupera el ACEPTADA.
        op4 = str(uuid.uuid4())
        h4 = _hash("payload-4")
        c1 = psycopg.connect(api)
        c1.autocommit = False
        cur1 = c1.cursor()
        tokenizar(cur1, token_a2)
        cur1.execute(
            "SELECT * FROM fashionstore_private.fn_offline_begin("
            "%s,%s,%s::uuid,%s,%s,%s,%s)",
            (1, 102, op4, "VENTA_CONTADO", h4, 11, "50.00"))
        assert cur1.fetchone()[0] is True
        resultado: dict = {}

        def segundo_begin():
            with psycopg.connect(api) as c2:
                with c2.cursor() as cur2:
                    tokenizar(cur2, token_a2)
                    cur2.execute(
                        "SELECT * FROM fashionstore_private.fn_offline_begin("
                        "%s,%s,%s::uuid,%s,%s,%s,%s)",
                        (1, 102, op4, "VENTA_CONTADO", h4, 11, "50.00"))
                    resultado["fila"] = cur2.fetchone()
                c2.commit()

        hilo = threading.Thread(target=segundo_begin)
        hilo.start()
        time.sleep(0.8)
        assert hilo.is_alive(), "el segundo begin no espero el lock"
        cur1.execute(
            "SELECT fashionstore_private.fn_offline_finish("
            "%s,%s,%s::uuid,'ACEPTADA',%s,%s::jsonb,NULL,NULL)",
            (1, 102, op4, 1000, json.dumps({"venta_id": 1000})))
        c1.commit()
        cur1.close()
        c1.close()
        hilo.join(15)
        assert not hilo.is_alive(), "el segundo begin no desperto"
        assert resultado["fila"][0] is False, resultado
        assert resultado["fila"][1] == "ACEPTADA", resultado
        assert resultado["fila"][2] == 1000, resultado

        # Propietarios, SECURITY DEFINER, ACL y RLS.
        firmas = (
            "fashionstore_private.fn_offline_begin(bigint,bigint,uuid,text,text,bigint,numeric)",
            "fashionstore_private.fn_offline_finish(bigint,bigint,uuid,text,bigint,jsonb,text,text)",
            "fashionstore_private.fn_offline_rechazar(bigint,bigint,uuid,text,text,bigint,numeric,text,text,text)",
            "fashionstore_private.fn_offline_get(bigint,bigint,uuid)",
        )
        for firma in firmas:
            assert root_scalar(root, f"""
              SELECT count(*) FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
              WHERE p.oid=to_regprocedure('{firma}')
                AND r.rolname='fashionstore_sp_owner'
                AND p.prosecdef
                AND p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[]
                AND has_function_privilege('fashionstore_api',p.oid,'EXECUTE')
                AND NOT EXISTS(SELECT 1 FROM aclexplode(p.proacl) a
                  WHERE a.grantee=0 AND a.privilege_type='EXECUTE')
            """) == "1", firma
        assert root_scalar(root, """
          SELECT count(*) FROM pg_class c
          WHERE c.oid='fashionstore_private.offline_operacion'::regclass
            AND c.relrowsecurity AND c.relforcerowsecurity
        """) == "1"
        assert root_scalar(root, """
          SELECT count(*) FROM information_schema.role_table_grants
          WHERE grantee='fashionstore_api'
            AND table_schema='fashionstore_private'
            AND table_name='offline_operacion'
        """) == "0"

        # Actor CAJERO ficticio exclusivo del laboratorio.
        root_sql(root, """
          INSERT INTO public.rol(id,nombre) VALUES(4,'CAJERO');
          INSERT INTO public.usuario(id,rol_id,correo,password_hash)
            VALUES(104,4,'a4.cajero@lab.test','$lab$pendiente');
          INSERT INTO public.usuario_empresa(usuario_id,empresa_id,rol_id)
            VALUES(104,1,4);
          INSERT INTO public.empleado(
            id,empresa_id,usuario_id,sucursal_id,nombres,apellidos,ci,
            fecha_contratacion)
          VALUES(104,1,104,11,'Caja','Uno','CI-A4',CURRENT_DATE-100);
        """)

        # Integracion real del servicio Python contra el mismo PostgreSQL 17.
        settings.saas_sp_sql_owner = "fashionstore_sp_owner"
        settings.saas_actor_sql_owner = "fashionstore_context_owner"
        settings.saas_reservas_enabled = True
        settings.saas_ventas_enabled = True
        settings.saas_offline_enabled = True

        def sesion(token: str, empresa: int, usuario: int):
            motor = create_engine(
                api.replace("postgresql://", "postgresql+psycopg://", 1)
            )
            fabrica = sessionmaker(bind=motor, autoflush=False, autocommit=False)
            db = fabrica()
            db.execute(text(
                "SELECT set_config('app.actor_token', :t, true)"
            ), {"t": token})
            db.execute(text(
                "SELECT set_config('app.empresa_id', :e, true)"
            ), {"e": str(empresa)})
            db.execute(text(
                "SELECT set_config('app.usuario_id', :u, true)"
            ), {"u": str(usuario)})
            return db, motor

        tenant_cliente = TenantContext(
            usuario_id=102, empresa_id=1, empresa_slug="vanter-men",
            empresa_nombre="Vanter Men", rol_id=2, rol_nombre="CLIENTE",
            contexto="cliente", plan_codigo="BASICO",
            permite_reportes_ia=False,
        )
        db, motor = sesion(token_a2, 1, 102)
        try:
            req = OperacionOfflineRequest(
                operacion_uuid=uuid.uuid4(), tipo="RESERVA", sucursal_id=11,
                lineas=[{"inventario_id": 2, "cantidad": 1}],
                fecha_atencion=datetime.now(timezone.utc) + timedelta(days=2),
                observacion="integracion offline",
            )
            resp, codigo = OfflineService.ejecutar(db, tenant_cliente, req)
            assert codigo == 200 and resp.estado == "ACEPTADA", (codigo, resp)
            reserva_id = int(resp.resultado["reserva_id"])
            assert root_scalar(root, f"SELECT count(*) FROM public.reserva "
                                     f"WHERE id={reserva_id} AND empresa_id=1") == "1"
            assert root_scalar(root, f"SELECT estado FROM public.reserva "
                                     f"WHERE id={reserva_id}") == "PENDIENTE"
            assert root_scalar(root, "SELECT stock_reservado FROM public.inventario "
                                     "WHERE id=2") == "2"
        finally:
            db.close()
            motor.dispose()

        tenant_admin = TenantContext(
            usuario_id=101, empresa_id=1, empresa_slug="vanter-men",
            empresa_nombre="Vanter Men", rol_id=1, rol_nombre="ADMINISTRADOR",
            contexto="personal", plan_codigo="BASICO",
            permite_reportes_ia=False,
        )
        stock_antes = int(root_scalar(
            root, "SELECT stock_actual FROM public.inventario WHERE id=2"
        ))
        db, motor = sesion(tokens["a1"], 1, 101)
        try:
            req_venta = OperacionOfflineRequest(
                operacion_uuid=uuid.uuid4(), tipo="VENTA_CONTADO",
                sucursal_id=11,
                lineas=[{"inventario_id": 2, "cantidad": 1}],
                efectivo_recibido=Decimal("150.00"),
            )
            resp_venta, codigo = OfflineService.ejecutar(
                db, tenant_admin, req_venta
            )
            assert codigo == 200 and resp_venta.estado == "ACEPTADA", (
                codigo, resp_venta)
            venta_id = int(resp_venta.resultado["venta_id"])
            pago_id = int(resp_venta.resultado["pago_id"])
            assert resp_venta.resultado["metodo"] == "EFECTIVO"
            assert resp_venta.resultado["estado"] == "COMPLETADA"
            assert root_scalar(root, f"SELECT estado FROM public.venta "
                                     f"WHERE id={venta_id}") == "COMPLETADA"
            assert root_scalar(root, f"SELECT estado FROM public.pago "
                                     f"WHERE id={pago_id}") == "APROBADO"
            assert root_scalar(
                root,
                f"SELECT count(*) FROM public.movimiento_inventario "
                f"WHERE referencia_tipo='VENTA' AND referencia_id={venta_id}"
            ) == "1"
            assert int(root_scalar(
                root, "SELECT stock_actual FROM public.inventario WHERE id=2"
            )) == stock_antes - 1
            fila = _call(api, token_a2,
                "SELECT * FROM fashionstore_private.fn_offline_get("
                "%s,%s,%s::uuid)", (1, 102, str(req.operacion_uuid)))
            assert fila[1] == "ACEPTADA" and fila[2] == reserva_id, fila
        finally:
            db.close()
            motor.dispose()

        # CU20 con CAJERO: empleado activo y sucursal asignada.
        token_cajero = jwt_hs256(
            secreto, claims_tenant(104, 1, "personal")
        )
        tenant_cajero = TenantContext(
            usuario_id=104, empresa_id=1, empresa_slug="vanter-men",
            empresa_nombre="Vanter Men", rol_id=4, rol_nombre="CAJERO",
            contexto="personal", plan_codigo="BASICO",
            permite_reportes_ia=False,
        )
        stock_antes_cajero = int(root_scalar(
            root, "SELECT stock_actual FROM public.inventario WHERE id=2"
        ))
        db, motor = sesion(token_cajero, 1, 104)
        try:
            req_cajero = OperacionOfflineRequest(
                operacion_uuid=uuid.uuid4(), tipo="VENTA_CONTADO",
                sucursal_id=11,
                lineas=[{"inventario_id": 2, "cantidad": 1}],
                efectivo_recibido=Decimal("100.00"),
            )
            resp_cajero, codigo = OfflineService.ejecutar(
                db, tenant_cajero, req_cajero
            )
            assert codigo == 200 and resp_cajero.estado == "ACEPTADA", (
                codigo, resp_cajero)
            venta_cajero = int(resp_cajero.resultado["venta_id"])
            assert root_scalar(root, f"SELECT estado FROM public.venta "
                                     f"WHERE id={venta_cajero}") == "COMPLETADA"
            assert int(root_scalar(
                root, "SELECT stock_actual FROM public.inventario WHERE id=2"
            )) == stock_antes_cajero - 1
        finally:
            db.close()
            motor.dispose()

        # CU11/CU13 siguen presentes.
        for firma in (
            "public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint)",
            "public.sp_crear_venta_presencial_tenant(bigint,bigint,bigint,bigint,jsonb,bigint)",
            "public.sp_registrar_pago_presencial_tenant(bigint,bigint,bigint,text,bigint)",
        ):
            existe = root_scalar(root, f"SELECT to_regprocedure('{firma}') IS NOT NULL")
            assert existe in {"t", "True"}, f"falta {firma}: {existe}"

        print("CU19_CU20_LAB=PASS ledger,idempotencia,conflicto,rechazo,"
              "concurrencia,rollback,servicio_reserva,venta_admin,venta_cajero,"
              "actor,acl,rls,carrito_origen,cu11_cu13_intactos")
    finally:
        cluster.destruir()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
