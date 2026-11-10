"""Valida CU14 en clústeres PostgreSQL 17 efímeros; nunca lee .env ni Stripe."""
from __future__ import annotations

import json
import secrets
import sys
import threading
from pathlib import Path

import psycopg
from psycopg import sql

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RES = ROOT / "database/saas/preparacion_pg17/reservas"
CU13 = ROOT / "database/saas/preparacion_pg17/cu12_cu13"
LAB = ROOT / "database/saas/laboratorio_pg17_5cb"
sys.path[:0] = [str(RES), str(LAB)]
from validar_080_pg17_aislado import (  # noqa: E402
    archivo_como, instalar_base, root_scalar, root_sql,
)
from lab_comun import BASE, Cluster, LabError  # noqa: E402
from lab_pruebas import tokenizar  # noqa: E402
from lab_run_5cb import tokens_de  # noqa: E402


def scalar(dsn: str, query: str, params=None):
    with psycopg.connect(dsn, connect_timeout=10) as c:
        row = c.execute(query, params or ()).fetchone()
        return row[0] if row else None


def execute(dsn: str, query: str, params=None) -> None:
    with psycopg.connect(dsn, connect_timeout=10) as c:
        c.execute(query, params or ())
        c.commit()


def call_api(dsn: str, token: str, query: str, args):
    with psycopg.connect(dsn) as c:
        with c.cursor() as cur:
            tokenizar(cur, token)
            cur.execute(query, args)
            row = cur.fetchone()
        c.commit()
        return row


def call_webhook(dsn: str, args) -> int:
    with psycopg.connect(dsn) as c:
        row = c.execute(
            "CALL fashionstore_private.sp_evento_stripe_tenant"
            "(%s,%s,%s,%s,%s)", args
        ).fetchone()
        c.commit()
        return row[0]


def refund_rechazado(dsn: str, args) -> None:
    try:
        call_webhook(dsn, args)
    except psycopg.Error as exc:
        assert exc.sqlstate == "23514"
    else:
        raise AssertionError(f"reembolso no total aceptado: {args}")


def membresias(dsn: str) -> list[dict]:
    raw = scalar(dsn, """
      SELECT coalesce(json_agg(json_build_object(
       'rol',granted.rolname,'grantor',grantor.rolname,
       'admin',m.admin_option,'inherit',m.inherit_option,
       'set',m.set_option) ORDER BY granted.rolname,grantor.rolname,
       m.admin_option,m.inherit_option,m.set_option)::text,'[]')
      FROM pg_auth_members m
      JOIN pg_roles granted ON granted.oid=m.roleid
      JOIN pg_roles member ON member.oid=m.member
      JOIN pg_roles grantor ON grantor.oid=m.grantor
      WHERE member.rolname='fashionstore_installer'
        AND granted.rolname IN
          ('fashionstore_sp_owner','fashionstore_webhook_owner')
    """)
    return json.loads(raw)


def sp_owner_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["rol"] == "fashionstore_sp_owner"]


def instalar(cluster, *, negativos=False):
    secreto = secrets.token_urlsafe(48)
    dsns, root_dsn, installer_dsn, clave, _ = instalar_base(
        cluster, secreto, negativos=False
    )
    # En Supabase el instalador real (postgres) tiene BYPASSRLS. El laboratorio
    # lo replica para que 102 pueda detectar actividad aunque FORCE RLS esté
    # activo; no se concede a ningún rol del paquete CU14.
    execute(root_dsn, "ALTER ROLE fashionstore_installer BYPASSRLS")
    archivo_como(
        cluster,
        CU13 / "090_instalar_cu12_cu13_saas_PROPUESTA_NO_EJECUTAR.sql",
        "fashionstore_installer", clave,
    )
    # Reproducir el estado real: el instalador ya tiene una concesión propia
    # (SET FALSE) sobre sp_owner, además de la de supabase_admin/postgres.
    execute(installer_dsn,
            "GRANT fashionstore_sp_owner TO CURRENT_USER "
            "WITH INHERIT FALSE, SET FALSE")
    antes = membresias(root_dsn)
    if len(sp_owner_rows(antes)) != 2:
        raise AssertionError(f"membresías sp_owner inesperadas: {antes}")

    if negativos:
        root_sql(root_dsn,
                 "DROP POLICY p_sp_tenant_movimiento_inventario "
                 "ON public.movimiento_inventario")
        try:
            archivo_como(
                cluster,
                HERE / "100_instalar_cu14_saas_PROPUESTA_NO_EJECUTAR.sql",
                "fashionstore_installer", clave,
            )
            raise AssertionError("instalación sin policy aceptada")
        except LabError as exc:
            assert "Policies SP empresariales ausentes" in str(exc)
        root_sql(root_dsn, """CREATE POLICY p_sp_tenant_movimiento_inventario
          ON public.movimiento_inventario FOR ALL TO fashionstore_sp_owner
          USING (empresa_id=(SELECT fashionstore_private.actor_empresa_id()))
          WITH CHECK (empresa_id=(SELECT fashionstore_private.actor_empresa_id()))""")

    archivo_como(
        cluster,
        HERE / "100_instalar_cu14_saas_PROPUESTA_NO_EJECUTAR.sql",
        "fashionstore_installer", clave,
    )
    post = archivo_como(
        cluster, HERE / "101_verificar_cu14_saas.sql",
        "fashionstore_installer", clave,
    )
    assert "CU14_POSTFLIGHT=PASS" in post, post
    despues = membresias(root_dsn)
    if sp_owner_rows(despues) != sp_owner_rows(antes):
        raise AssertionError(f"100 alteró membresías sp_owner: {antes} -> {despues}")
    if any(r["rol"] == "fashionstore_webhook_owner" and r["set"]
           for r in despues):
        raise AssertionError("webhook_owner quedó con SET temporal")
    if not any(r["rol"] == "fashionstore_webhook_owner" and r["admin"]
               for r in despues):
        raise AssertionError("webhook_owner sin ADMIN OPTION")

    webhook_pass = secrets.token_urlsafe(30)
    with psycopg.connect(root_dsn, autocommit=True) as c:
        c.execute(sql.SQL(
            "CREATE ROLE fashionstore_webhook_login LOGIN INHERIT "
            "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {}"
        ).format(sql.Literal(webhook_pass)))
        c.execute("GRANT fashionstore_webhook TO fashionstore_webhook_login "
                  "WITH INHERIT TRUE, SET FALSE")
    return (
        dsns, root_dsn, installer_dsn, clave,
        cluster.dsn("fashionstore_webhook_login", webhook_pass),
        tokens_de(secreto), antes, despues,
    )


def crear_fixtures_invalidos(root: str) -> None:
    root_sql(root, """
      INSERT INTO public.producto(id,empresa_id,categoria_id,nombre,precio,estado)
      VALUES (10,1,1,'Producto inactivo',50.00,TRUE);
      INSERT INTO public.variante_producto(
        id,empresa_id,producto_id,talla_id,color_id,sku,estado)
      VALUES (10,1,10,1,1,'A-INACTIVA',FALSE);
      INSERT INTO public.inventario(
        id,empresa_id,sucursal_id,variante_producto_id,temporada_id,
        stock_actual,stock_reservado)
      VALUES (10,1,11,10,1,5,0);
    """)


def crear_carrito(root: str, carrito: int, cliente: int,
                  lineas: list[tuple[int, int]]) -> None:
    execute(root, f"""INSERT INTO public.carrito(
      id,empresa_id,cliente_id,sucursal_id,estado)
      VALUES ({carrito},1,{cliente},11,'ACTIVO')""")
    for idx, (detalle, inventario) in enumerate(lineas, start=carrito * 10):
        execute(root, f"""INSERT INTO public.detalle_carrito(
          id,empresa_id,carrito_id,cantidad,inventario_id)
          VALUES ({idx},1,{carrito},1,{inventario})""")


def borrar_carrito(root: str, carrito: int) -> None:
    execute(root, f"DELETE FROM public.detalle_carrito WHERE carrito_id={carrito}")
    execute(root, f"DELETE FROM public.carrito WHERE id={carrito}")


def main() -> int:
    cluster = Cluster.crear(BASE + "_cu14_100")
    try:
        (dsns, root, installer, clave, webhook,
         tokens, antes, despues) = instalar(cluster, negativos=True)
        crear_fixtures_invalidos(root)

        # Concurrencia sobre el mismo carrito.
        barrera = threading.Barrier(2)
        salidas = []
        def competir():
            try:
                barrera.wait(10)
                salidas.append(("OK", call_api(
                    dsns["api"], tokens["a2"],
                    "CALL public.sp_crear_venta_digital_tenant(%s,%s,%s,%s,%s,%s)",
                    (1, 102, 102, 1, "WEB", None))[0]))
            except psycopg.Error as exc:
                salidas.append(("ERROR", exc.sqlstate))
        h1 = threading.Thread(target=competir)
        h2 = threading.Thread(target=competir)
        h1.start(); h2.start(); h1.join(20); h2.join(20)
        assert sorted(x[0] for x in salidas) == ["ERROR", "OK"]
        assert next(x[1] for x in salidas if x[0] == "ERROR") in {"23505", "23514"}
        venta = next(x[1] for x in salidas if x[0] == "OK")
        assert scalar(root,
            f"SELECT estado||':'||total FROM venta WHERE id={venta}") == "PENDIENTE:100.00"
        assert scalar(root, "SELECT estado FROM carrito WHERE id=1") == "CONVERTIDO"

        # Carrito multilínea con una línea inactiva: nunca venta parcial.
        crear_carrito(root, 5, 102, [(50, 1), (51, 10)])
        try:
            call_api(dsns["api"], tokens["a2"],
                "CALL public.sp_crear_venta_digital_tenant(%s,%s,%s,%s,%s,%s)",
                (1, 102, 102, 5, "WEB", None))
            raise AssertionError("carrito multilínea inválido aceptado")
        except psycopg.Error as exc:
            assert exc.sqlstate == "23514"
        assert scalar(root, "SELECT estado FROM carrito WHERE id=5") == "ACTIVO"
        assert scalar(root, "SELECT count(*) FROM venta WHERE carrito_id=5") == 0
        borrar_carrito(root, 5)

        # Carrito completamente inválido: rollback completo.
        crear_carrito(root, 6, 102, [(60, 10)])
        try:
            call_api(dsns["api"], tokens["a2"],
                "CALL public.sp_crear_venta_digital_tenant(%s,%s,%s,%s,%s,%s)",
                (1, 102, 102, 6, "WEB", None))
            raise AssertionError("carrito inválido completo aceptado")
        except psycopg.Error as exc:
            assert exc.sqlstate == "23514"
        assert scalar(root, "SELECT estado FROM carrito WHERE id=6") == "ACTIVO"
        assert scalar(root, "SELECT count(*) FROM venta WHERE carrito_id=6") == 0
        borrar_carrito(root, 6)

        # Cliente distinto dentro de la empresa y cliente de otra empresa.
        for token, cliente, empresa, usuario in (
            (tokens["a2"], 103, 1, 102), (tokens["b1"], 102, 1, 201),
        ):
            try:
                call_api(dsns["api"], token,
                    "CALL public.sp_crear_venta_digital_tenant(%s,%s,%s,%s,%s,%s)",
                    (empresa, usuario, cliente, 1, "WEB", None))
                raise AssertionError("identidad de cliente cruzada aceptada")
            except psycopg.Error as exc:
                assert exc.sqlstate in {"42501", "23514", "P0002"}

        # Venta digital adicional para probar referencias cruzadas.
        crear_carrito(root, 7, 102, [(70, 2)])
        venta2 = call_api(dsns["api"], tokens["a2"],
            "CALL public.sp_crear_venta_digital_tenant(%s,%s,%s,%s,%s,%s)",
            (1, 102, 102, 7, "WEB", None))[0]
        assert scalar(root, f"SELECT estado FROM venta WHERE id={venta2}") == "PENDIENTE"

        # PaymentIntent propio, reutilización idempotente y cruces.
        pago1 = call_api(dsns["api"], tokens["a2"],
            "CALL public.sp_registrar_intencion_stripe_tenant(%s,%s,%s,%s,%s,%s)",
            (1, 102, venta, "pi_lab_cu14_1", "bob", None))[0]
        pago1b = call_api(dsns["api"], tokens["a2"],
            "CALL public.sp_registrar_intencion_stripe_tenant(%s,%s,%s,%s,%s,%s)",
            (1, 102, venta, "pi_lab_cu14_1", "bob", None))[0]
        assert pago1 == pago1b
        assert scalar(root,
            "SELECT count(*) FROM pago WHERE referencia_transaccion='pi_lab_cu14_1'") == 1
        root_sql(root, f"""
          INSERT INTO public.pago(empresa_id,venta_id,fecha_hora,monto,metodo,estado,
            referencia_transaccion,pasarela)
          VALUES (1,{venta},now(),1.00,'TARJETA','PENDIENTE','pi_bad_amount','STRIPE'),
                 (2,2,now(),50.00,'TARJETA','PENDIENTE','pi_cross_company','STRIPE'),
                 (1,{venta2},now(),100.00,'TARJETA','PENDIENTE','pi_cross_sale','STRIPE')
        """)
        for pi, args in (
            ("pi_bad_amount", (1, 102, venta, "pi_bad_amount", "bob", None)),
            ("pi_cross_company", (1, 102, venta, "pi_cross_company", "bob", None)),
            ("pi_cross_sale", (1, 102, venta, "pi_cross_sale", "bob", None)),
        ):
            try:
                call_api(dsns["api"], tokens["a2"],
                    "CALL public.sp_registrar_intencion_stripe_tenant(%s,%s,%s,%s,%s,%s)",
                    args)
                raise AssertionError(f"referencia cruzada aceptada: {pi}")
            except psycopg.Error as exc:
                assert exc.sqlstate == "23514"
        for moneda, pi in (("usd", "pi_bad_currency"), (None, "pi_null_currency")):
            try:
                call_api(dsns["api"], tokens["a2"],
                    "CALL public.sp_registrar_intencion_stripe_tenant(%s,%s,%s,%s,%s,%s)",
                    (1, 102, venta, pi, moneda, None))
                raise AssertionError("moneda inválida aceptada")
            except psycopg.Error as exc:
                assert exc.sqlstate == "22023"

        # Webhook: éxito, duplicado, fuera de orden y stock.
        with psycopg.connect(webhook) as c:
            row = c.execute(
                "CALL fashionstore_private.sp_evento_stripe_tenant(%s,%s,%s,%s,%s)",
                ("pi_lab_cu14_1", "payment_intent.succeeded", 10000, "bob", None)
            ).fetchone(); c.commit()
        assert row[0] == 1
        assert scalar(root, f"SELECT estado FROM venta WHERE id={venta}") == "COMPLETADA"
        assert scalar(root, "SELECT stock_actual FROM inventario WHERE id=1") == 0
        mov1 = scalar(root, f"""SELECT count(*) FROM movimiento_inventario
          WHERE referencia_tipo='VENTA' AND referencia_id={venta}""")
        assert mov1 == 1

        for evento in ("payment_intent.succeeded",
                       "payment_intent.payment_failed",
                       "payment_intent.canceled",
                       "payment_intent.refunded"):
            with psycopg.connect(webhook) as c:
                c.execute(
                    "CALL fashionstore_private.sp_evento_stripe_tenant(%s,%s,%s,%s,%s)",
                    ("pi_lab_cu14_1", evento, 10000, "bob", None)
                ); c.commit()
        assert scalar(root, f"SELECT estado FROM venta WHERE id={venta}") == "COMPLETADA"
        assert scalar(root, f"SELECT estado FROM pago WHERE id={pago1}") == "APROBADO"
        assert scalar(root, "SELECT stock_actual FROM inventario WHERE id=1") == 0
        assert scalar(root, f"""SELECT count(*) FROM movimiento_inventario
          WHERE referencia_tipo='VENTA' AND referencia_id={venta}""") == 1

        # Venta con stock insuficiente tras la primera compra.
        try:
            call_api(dsns["api"], tokens["a3"],
                "CALL public.sp_crear_venta_digital_tenant(%s,%s,%s,%s,%s,%s)",
                (1, 103, 103, 4, "WEB", None))
            raise AssertionError("stock insuficiente aceptado")
        except psycopg.Error as exc:
            assert exc.sqlstate == "23514"
        assert scalar(root, "SELECT estado FROM carrito WHERE id=4") == "ACTIVO"

        # Refund antes de succeeded: no retrocede una venta finalizada y es
        # idempotente; luego succeeded no descuenta stock.
        pago2 = call_api(dsns["api"], tokens["a2"],
            "CALL public.sp_registrar_intencion_stripe_tenant(%s,%s,%s,%s,%s,%s)",
            (1, 102, venta2, "pi_lab_cu14_2", "bob", None))[0]
        for evento in ("payment_intent.refunded", "payment_intent.refunded",
                       "payment_intent.succeeded"):
            with psycopg.connect(webhook) as c:
                c.execute(
                    "CALL fashionstore_private.sp_evento_stripe_tenant(%s,%s,%s,%s,%s)",
                    ("pi_lab_cu14_2", evento, 10000, "bob", None)
                ); c.commit()
        assert scalar(root, f"SELECT estado FROM venta WHERE id={venta2}") == "CANCELADA"
        assert scalar(root, f"SELECT estado FROM pago WHERE id={pago2}") == "REEMBOLSADO"
        assert scalar(root, "SELECT stock_actual FROM inventario WHERE id=2") == 5

        # Compensación por stock insuficiente en el webhook.
        crear_carrito(root, 8, 102, [(80, 2)])
        venta3 = call_api(dsns["api"], tokens["a2"],
            "CALL public.sp_crear_venta_digital_tenant(%s,%s,%s,%s,%s,%s)",
            (1, 102, 102, 8, "WEB", None))[0]
        pago3 = call_api(dsns["api"], tokens["a2"],
            "CALL public.sp_registrar_intencion_stripe_tenant(%s,%s,%s,%s,%s,%s)",
            (1, 102, venta3, "pi_lab_cu14_3", "bob", None))[0]
        root_sql(root, "UPDATE public.inventario SET stock_actual=0, stock_reservado=0 WHERE id=2")
        try:
            with psycopg.connect(webhook) as c:
                c.execute(
                    "CALL fashionstore_private.sp_evento_stripe_tenant(%s,%s,%s,%s,%s)",
                    ("pi_lab_cu14_3", "payment_intent.succeeded", 10000, "bob", None)
                ); c.commit()
            raise AssertionError("stock insuficiente en webhook aceptado")
        except psycopg.Error as exc:
            assert exc.sqlstate == "23514"
        with psycopg.connect(webhook) as c:
            c.execute(
                "CALL fashionstore_private.sp_evento_stripe_tenant(%s,%s,%s,%s,%s)",
                ("pi_lab_cu14_3", "payment_intent.refunded", 10000, "bob", None)
            ); c.commit()
        assert scalar(root, f"SELECT estado FROM pago WHERE id={pago3}") == "REEMBOLSADO"
        assert scalar(root, f"SELECT estado FROM venta WHERE id={venta3}") == "CANCELADA"

        # Reembolso parcial, moneda incorrecta y duplicado: solo el total
        # exacto marca la venta como CANCELADA/REEMBOLSADO.
        root_sql(root, "UPDATE public.inventario SET stock_actual=5, "
                       "stock_reservado=0 WHERE id=2")
        crear_carrito(root, 9, 102, [(90, 2)])
        venta4 = call_api(dsns["api"], tokens["a2"],
            "CALL public.sp_crear_venta_digital_tenant(%s,%s,%s,%s,%s,%s)",
            (1, 102, 102, 9, "WEB", None))[0]
        pago4 = call_api(dsns["api"], tokens["a2"],
            "CALL public.sp_registrar_intencion_stripe_tenant(%s,%s,%s,%s,%s,%s)",
            (1, 102, venta4, "pi_lab_cu14_4", "bob", None))[0]
        for monto, moneda in ((5000, "bob"), (10000, "usd"), (10000, None)):
            refund_rechazado(webhook, (
                "pi_lab_cu14_4", "payment_intent.refunded", monto, moneda, None
            ))
        assert scalar(root, f"SELECT estado FROM venta WHERE id={venta4}") == "PENDIENTE"
        assert scalar(root, f"SELECT estado FROM pago WHERE id={pago4}") == "PENDIENTE"
        assert call_webhook(webhook, (
            "pi_lab_cu14_4", "payment_intent.refunded", 10000, "bob", None
        )) == 1
        assert scalar(root, f"SELECT estado FROM venta WHERE id={venta4}") == "CANCELADA"
        assert scalar(root, f"SELECT estado FROM pago WHERE id={pago4}") == "REEMBOLSADO"
        assert scalar(root, "SELECT stock_actual FROM inventario WHERE id=2") == 5
        # Duplicado total: idempotente. Duplicado parcial: rechazado.
        assert call_webhook(webhook, (
            "pi_lab_cu14_4", "payment_intent.refunded", 10000, "bob", None
        )) == 1
        refund_rechazado(webhook, (
            "pi_lab_cu14_4", "payment_intent.refunded", 5000, "bob", None
        ))
        assert scalar(root, f"SELECT estado FROM venta WHERE id={venta4}") == "CANCELADA"
        assert scalar(root, f"SELECT estado FROM pago WHERE id={pago4}") == "REEMBOLSADO"
        assert scalar(root, "SELECT stock_actual FROM inventario WHERE id=2") == 5

        # En una venta ya COMPLETADA, un refund inválido tampoco se oculta por
        # el no-op de evento fuera de orden.
        refund_rechazado(webhook, (
            "pi_lab_cu14_1", "payment_intent.refunded", 5000, "bob", None
        ))
        assert call_webhook(webhook, (
            "pi_lab_cu14_1", "payment_intent.refunded", 10000, "bob", None
        )) == 1
        assert scalar(root, f"SELECT estado FROM venta WHERE id={venta}") == "COMPLETADA"
        assert scalar(root, f"SELECT estado FROM pago WHERE id={pago1}") == "APROBADO"

        # Visibilidad cliente: propia, misma empresa distinta y otra empresa.
        assert call_api(dsns["api"], tokens["a2"],
            "SELECT count(*) FROM venta WHERE id=%s", (venta,))[0] == 1
        assert call_api(dsns["api"], tokens["a3"],
            "SELECT count(*) FROM venta WHERE id=%s", (venta,))[0] == 0
        assert call_api(dsns["api"], tokens["b1"],
            "SELECT count(*) FROM venta WHERE id=%s", (venta,))[0] == 0

        # El principal webhook no tiene DML directo.
        with psycopg.connect(webhook) as c:
            try:
                c.execute("UPDATE public.pago SET estado='APROBADO' WHERE id=%s",
                          (pago1,))
            except psycopg.Error as exc:
                assert exc.sqlstate == "42501"; c.rollback()
            else:
                raise AssertionError("webhook obtuvo DML directo")

        # CU11/CU12/CU13 preservados.
        for firma in (
            "public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint)",
            "public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text)",
            "public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text)",
            "public.sp_confirmar_reserva_tenant(bigint,bigint,bigint)",
            "public.sp_crear_venta_presencial_tenant(bigint,bigint,bigint,bigint,jsonb,bigint)",
            "public.sp_registrar_pago_presencial_tenant(bigint,bigint,bigint,text,bigint)",
        ):
            assert scalar(root, "SELECT to_regprocedure(%s) IS NOT NULL",
                          (firma,)) is True
        print("CU14_LAB=PASS 100/101,multigestor,digital,invalido,concurrencia,"
              "stripe,webhook,out-of-order,compensacion,refund-total,"
              "refund-parcial,refund-duplicado,acl,cross-tenant")
    finally:
        cluster.destruir()

    # Reversión positiva sin actividad CU14.
    cluster = Cluster.crear(BASE + "_cu14_102_ok")
    try:
        _, root, _, clave, _, _, antes, _ = instalar(cluster)
        archivo_como(cluster, HERE / "102_revertir_cu14_CONTROLADO.sql",
                     "fashionstore_installer", clave)
        assert scalar(root, """SELECT to_regprocedure(
          'public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)')
          IS NULL""") is True
        assert scalar(root, "SELECT count(*) FROM pg_roles WHERE rolname IN "
                           "('fashionstore_webhook','fashionstore_webhook_owner')") == 0
        assert membresias(root) == antes
        print("CU14_REVERSION=PASS membresias_y_cu11_cu12_cu13_intactas")
    finally:
        cluster.destruir()

    # Reversión negativa: actividad pendiente antigua debe bloquear.
    cluster = Cluster.crear(BASE + "_cu14_102_block")
    try:
        _, root, _, clave, _, _, _, _ = instalar(cluster)
        root_sql(root, """
          INSERT INTO public.venta(id,empresa_id,cliente_id,sucursal_id,
            fecha_hora,canal,estado,total,carrito_id)
          VALUES (900,1,102,11,now()-interval '20 days','WEB','PENDIENTE',
                  100.00,NULL)
        """)
        try:
            archivo_como(cluster, HERE / "102_revertir_cu14_CONTROLADO.sql",
                         "fashionstore_installer", clave)
            raise AssertionError("102 aceptó venta digital pendiente antigua")
        except LabError as exc:
            assert "venta digital PENDIENTE" in str(exc)
        assert scalar(root, """SELECT to_regprocedure(
          'public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)')
          IS NOT NULL""") is True
        print("CU14_REVERSION_NEGATIVA=PASS actividad_antigua_bloqueada")
    finally:
        cluster.destruir()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
