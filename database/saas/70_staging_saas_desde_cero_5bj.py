"""5BJ - Staging SaaS desechable desde cero (sin produccion).

Objetivo
--------
Comprobar las migraciones portables del Bloque 2 contra un PostgreSQL real
pero DESCARTABLE:

  1. Crea el esquema heredado a partir del mapeo ORM del primer parcial.
  2. Lo reduce al estado historico (sin empresa_id ni UNIQUE por empresa)
     y le devuelve los UNIQUE globales que existian antes del SaaS.
  3. Carga datos 100 % ficticios en las 31 tablas empresariales.
  4. Aplica supabase/migrations/20261030000100..000300 con el tenant
     bootstrap declarado por parametro de sesion.
  5. Verifica integridad, conservacion de filas, idempotencia y rechazo
     de referencias cruzadas entre empresas.

Este script NUNCA debe apuntar a Supabase ni a una base con datos reales.
Exige que el nombre de la base contenga "staging" o "lab" y rechaza hosts
que no sean de bucle local, salvo confirmacion explicita con --allow-remote.

Uso:
    python database/saas/70_staging_saas_desde_cero_5bj.py \
        --dsn postgresql://postgres@127.0.0.1:55440/fashionstore_staging

    # opcional: --reset crea la base si no existe (requiere otra base de
    # mantenimiento en el mismo servidor)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Evita que la importacion del ORM lea credenciales reales del .env.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://staging@127.0.0.1/staging")
os.environ.setdefault("SECRET_KEY", "staging-only-placeholder")

BLOQUE = [
    "20261030000100_saas_core_portable.sql",
    "20261030000200_saas_empresa_id_portable.sql",
    "20261030000300_saas_integridad_tenant_portable.sql",
]

EMPRESAS = [
    "bitacora", "sucursal", "empleado", "cliente", "categoria", "producto",
    "variante_producto", "recurso_producto", "temporada", "coleccion",
    "producto_coleccion", "promocion", "promocion_producto", "proveedor",
    "proveedor_producto", "orden_compra", "detalle_orden_compra",
    "inventario", "movimiento_inventario", "carrito", "detalle_carrito",
    "reserva", "detalle_reserva", "venta", "detalle_venta", "pago",
    "devolucion", "detalle_devolucion", "configuracion_vestidor_ar",
    "sesion_vestidor_ar", "prueba_vestidor_ar",
]

BASE_SAAS = ["empresa", "plan_saas", "suscripcion", "usuario_empresa",
             "empresa_configuracion"]

# UNIQUE globales del esquema del primer parcial (relajados por la migracion).
UNIQUES_HISTORICOS = {
    "categoria": [("uq_categoria_nombre", ("nombre",))],
    "temporada": [("uq_temporada_nombre", ("nombre",))],
    "variante_producto": [("uq_variante_producto_sku", ("sku",))],
    "sucursal": [("uq_sucursal_ciudad_nombre", ("ciudad_id", "nombre"))],
    "proveedor": [("uq_proveedor_nit", ("nit",))],
    "cliente": [
        ("uq_cliente_ci", ("ci",)),
        ("uq_cliente_usuario", ("usuario_id",)),
    ],
    "empleado": [
        ("uq_empleado_ci", ("ci",)),
        ("uq_empleado_usuario", ("usuario_id",)),
    ],
}

AHORA = datetime.now(timezone.utc).replace(microsecond=0)

# Datos ficticios, en orden seguro de FK.
SEMILLA = [
    ("rol", "INSERT INTO public.rol (id,nombre,estado) VALUES (1,'ADMINISTRADOR',TRUE),(2,'CLIENTE',TRUE)"),
    ("ciudad", "INSERT INTO public.ciudad (id,nombre,estado) VALUES (1,'Ciudad Ficticia',TRUE)"),
    ("color", "INSERT INTO public.color (id,nombre,estado) VALUES (1,'Negro',TRUE)"),
    ("talla", "INSERT INTO public.talla (id,nombre,estado) VALUES (1,'M',TRUE)"),
    ("modulo", "INSERT INTO public.modulo (id,nombre,estado) VALUES (1,'Ventas',TRUE)"),
    ("accion", "INSERT INTO public.accion (id,nombre,estado) VALUES (1,'CREAR',TRUE)"),
    ("funcion", "INSERT INTO public.funcion (id,modulo_id,nombre,estado) VALUES (1,1,'Registrar venta',TRUE)"),
    ("usuario", "INSERT INTO public.usuario (id,rol_id,correo,password_hash,fecha_creacion,estado) "
                "VALUES (1,1,'admin@staging.invalid','hash-ficticio',now(),TRUE),"
                "(2,2,'cliente@staging.invalid','hash-ficticio',now(),TRUE)"),
    ("sucursal", "INSERT INTO public.sucursal (id,ciudad_id,nombre,direccion,estado) "
                 "VALUES (1,1,'Sucursal Ficticia','Calle Falsa 123',TRUE)"),
    ("categoria", "INSERT INTO public.categoria (id,nombre,estado) VALUES (1,'Polos',TRUE)"),
    ("temporada", "INSERT INTO public.temporada (id,nombre,fecha_inicio,fecha_fin,estado) "
                  "VALUES (1,'2026',DATE '2026-01-01',DATE '2026-12-31',TRUE)"),
    ("producto", "INSERT INTO public.producto (id,categoria_id,nombre,precio,estado) "
                 "VALUES (1,1,'Polo ficticio',99.90,TRUE)"),
    ("variante_producto", "INSERT INTO public.variante_producto (id,producto_id,talla_id,color_id,sku,estado) "
                          "VALUES (1,1,1,1,'SKU-FICTICIO-1',TRUE)"),
    ("recurso_producto", "INSERT INTO public.recurso_producto (id,producto_id,color_id,tipo,url,es_principal,estado) "
                         "VALUES (1,1,1,'imagen','https://staging.invalid/1.jpg',TRUE,TRUE)"),
    ("coleccion", "INSERT INTO public.coleccion (id,temporada_id,nombre,estado) VALUES (1,1,'Verano',TRUE)"),
    ("producto_coleccion", "INSERT INTO public.producto_coleccion (producto_id,coleccion_id) VALUES (1,1)"),
    ("promocion", "INSERT INTO public.promocion (id,nombre,tipo_descuento,valor_descuento,fecha_inicio,fecha_fin,estado) "
                  "VALUES (1,'Promo ficticia','PORCENTAJE',10,now(),now() + interval '30 days',TRUE)"),
    ("promocion_producto", "INSERT INTO public.promocion_producto (promocion_id,producto_id) VALUES (1,1)"),
    ("proveedor", "INSERT INTO public.proveedor (id,razon_social,nit,estado) "
                  "VALUES (1,'Proveedor Ficticio SRL','NIT-1',TRUE)"),
    ("proveedor_producto", "INSERT INTO public.proveedor_producto (proveedor_id,producto_id,costo_referencia,estado) "
                           "VALUES (1,1,50,TRUE)"),
    ("inventario", "INSERT INTO public.inventario (id,sucursal_id,variante_producto_id,temporada_id,stock_actual,stock_reservado,fecha_actualizacion) "
                   "VALUES (1,1,1,1,10,0,now())"),
    ("movimiento_inventario", "INSERT INTO public.movimiento_inventario (id,inventario_id,usuario_id,tipo,cantidad,fecha_hora) "
                              "VALUES (1,1,1,'ENTRADA',10,now())"),
    ("cliente", "INSERT INTO public.cliente (id,usuario_id,nombre,apellido,ci,estado) "
                "VALUES (1,2,'Ana','Ficticia','CI-1',TRUE)"),
    ("empleado", "INSERT INTO public.empleado (id,usuario_id,sucursal_id,nombres,apellidos,ci,fecha_contratacion,estado) "
                 "VALUES (1,1,1,'Luis','Ficticio','CI-2',DATE '2026-01-02',TRUE)"),
    ("carrito", "INSERT INTO public.carrito (id,cliente_id,sucursal_id,fecha_creacion,fecha_actualizacion,estado) "
                "VALUES (1,1,1,now(),now(),'ACTIVO')"),
    ("detalle_carrito", "INSERT INTO public.detalle_carrito (id,carrito_id,inventario_id,cantidad) VALUES (1,1,1,2)"),
    ("reserva", "INSERT INTO public.reserva (id,cliente_id,sucursal_id,carrito_id,fecha_reserva,fecha_atencion,estado) "
                "VALUES (1,1,1,1,now(),now(),'ACTIVA')"),
    ("detalle_reserva", "INSERT INTO public.detalle_reserva (id,reserva_id,inventario_id,cantidad) VALUES (1,1,1,2)"),
    ("venta", "INSERT INTO public.venta (id,cliente_id,empleado_id,sucursal_id,reserva_id,fecha_hora,canal,estado,total,carrito_id) "
              "VALUES (1,1,1,1,NULL,now(),'TIENDA','COMPLETADA',99.90,1)"),
    ("detalle_venta", "INSERT INTO public.detalle_venta (id,venta_id,inventario_id,cantidad,precio_unitario) "
                      "VALUES (1,1,1,1,99.90)"),
    ("pago", "INSERT INTO public.pago (id,venta_id,fecha_hora,monto,metodo,estado) "
             "VALUES (1,1,now(),99.90,'EFECTIVO','CONFIRMADO')"),
    ("devolucion", "INSERT INTO public.devolucion (id,venta_id,fecha_hora,motivo,estado) "
                   "VALUES (1,1,now(),'Talla incorrecta','APROBADA')"),
    ("detalle_devolucion", "INSERT INTO public.detalle_devolucion (id,devolucion_id,detalle_venta_id,cantidad) "
                           "VALUES (1,1,1,1)"),
    ("bitacora", "INSERT INTO public.bitacora (id,usuario_id,fecha_hora,ip,accion) "
                 "VALUES (1,1,now(),'127.0.0.1','LOGIN')"),
    ("configuracion_vestidor_ar", "INSERT INTO public.configuracion_vestidor_ar "
                                  "(id,recurso_producto_id,zona_cuerpo,tipo_asset,factor_ancho,factor_alto,offset_x,offset_y,rotacion_offset,orden_capa,opacidad,estado) "
                                  "VALUES (1,1,'TORSO','PECHO',1,1,0,0,0,1,1,TRUE)"),
    ("sesion_vestidor_ar", "INSERT INTO public.sesion_vestidor_ar (id,cliente_id,fecha_inicio,estado) "
                           "VALUES (1,1,now(),'ACTIVA')"),
    ("prueba_vestidor_ar", "INSERT INTO public.prueba_vestidor_ar (id,sesion_vestidor_ar_id,configuracion_vestidor_ar_id,variante_producto_id,fecha_inicio,estado) "
                           "VALUES (1,1,1,1,now(),'ACTIVA')"),
    ("orden_compra", "INSERT INTO public.orden_compra (id,proveedor_id,sucursal_id,estado,empleado_id,fecha_orden) "
                     "VALUES (1,1,1,'COMPLETADA',1,now())"),
    ("detalle_orden_compra", "INSERT INTO public.detalle_orden_compra (id,orden_compra_id,variante_producto_id,temporada_id,cantidad,costo_unitario) "
                             "VALUES (1,1,1,1,5,50)"),
]


class StagingError(RuntimeError):
    pass


class _DescartarPrueba(Exception):
    """Fuerza el rollback de una transaccion de prueba que no debe persistir."""


def _dsn_seguro(dsn: str, allow_remote: bool) -> None:
    parsed = urlparse(dsn)
    host = (parsed.hostname or "").lower()
    db = (parsed.path or "").lstrip("/").lower()
    if not allow_remote and host not in {"127.0.0.1", "localhost", "::1"}:
        raise StagingError(f"Host no local rechazado: {host}")
    if not allow_remote and not ("staging" in db or "lab" in db):
        raise StagingError(
            f"La base '{db}' no parece descartable: use un nombre con 'staging' o 'lab'"
        )


def _esquema_heredado(engine) -> None:
    """Crea el esquema del primer parcial a partir del mapeo ORM."""
    import importlib
    import pkgutil

    import app.modules
    from app.core.database import Base

    for mod in pkgutil.walk_packages(app.modules.__path__, "app.modules."):
        if mod.name.endswith(".models") or ".models." in mod.name:
            importlib.import_module(mod.name)

    Base.metadata.create_all(engine)


def _reducir_a_historico(conn) -> None:
    """Quita los artefactos SaaS que el ORM ya declara y repone UNIQUE globales."""
    for tabla in EMPRESAS:
        conn.execute(
            f'ALTER TABLE public."{tabla}" DROP COLUMN IF EXISTS empresa_id CASCADE'
        )
    for tabla, uniques in UNIQUES_HISTORICOS.items():
        for nombre, columnas in uniques:
            cols = ", ".join(f'"{c}"' for c in columnas)
            conn.execute(
                f'ALTER TABLE public."{tabla}" ADD CONSTRAINT "{nombre}" UNIQUE ({cols})'
            )


def _sembrar(conn) -> dict[str, int]:
    for _, sql in SEMILLA:
        conn.execute(sql)
    return _conteos(conn)


def _conteos(conn) -> dict[str, int]:
    out: dict[str, int] = {}
    for tabla in EMPRESAS:
        out[tabla] = conn.execute(
            f'SELECT count(*) FROM public."{tabla}"'
        ).fetchone()[0]
    return out


def _aplicar_migraciones(conn, slug: str, crear_membresias: bool) -> None:
    # 1) Nucleo SaaS (crea empresa/plan_saas/suscripcion/membresias).
    sql_core = (ROOT / "supabase" / "migrations" / BLOQUE[0]).read_text(encoding="utf-8")
    conn.execute(sql_core)

    # 2) Registro del tenant historico, como lo haria el superadministrador
    #    antes de migrar. Idempotente por slug.
    conn.execute(
        "INSERT INTO public.empresa (razon_social,nombre_comercial,slug,estado) "
        "VALUES ('Empresa Historica Staging','Empresa Historica Staging',%s,TRUE) "
        "ON CONFLICT (slug) DO NOTHING",
        (slug,),
    )
    empresa_id = conn.execute(
        "SELECT id FROM public.empresa WHERE slug=%s", (slug,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO public.empresa_configuracion (empresa_id) VALUES (%s) "
        "ON CONFLICT (empresa_id) DO NOTHING",
        (empresa_id,),
    )
    if conn.execute(
        "SELECT count(*) FROM public.suscripcion "
        "WHERE empresa_id=%s AND estado='ACTIVA'",
        (empresa_id,),
    ).fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO public.suscripcion "
            "(empresa_id,plan_saas_id,fecha_inicio,fecha_vencimiento,estado,observacion) "
            "SELECT %s, id, CURRENT_DATE, CURRENT_DATE + 365, 'ACTIVA', "
            "'Migracion staging desechable' FROM public.plan_saas "
            "WHERE codigo='PROFESIONAL'",
            (empresa_id,),
        )

    conn.execute(f"SET saas.bootstrap_empresa_slug = '{slug}'")
    conn.execute(
        "SET saas.bootstrap_crear_membresias = "
        + ("'true'" if crear_membresias else "'false'")
    )
    for archivo in BLOQUE[1:]:
        sql = (ROOT / "supabase" / "migrations" / archivo).read_text(encoding="utf-8")
        conn.execute(sql)


def _verificar(conn, slug: str, antes: dict[str, int]) -> dict:
    fallos: list[str] = []
    datos: dict = {}

    for tabla in BASE_SAAS:
        existe = conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL", (f"public.{tabla}",)
        ).fetchone()[0]
        if not existe:
            fallos.append(f"falta tabla SaaS {tabla}")

    planes = dict(
        conn.execute(
            "SELECT codigo, max_sucursales_activas FROM public.plan_saas"
        ).fetchall()
    )
    datos["planes"] = planes
    if planes.get("BASICO") != 2 or planes.get("PROFESIONAL") != 10:
        fallos.append("limites de plan incorrectos")
    ia = conn.execute(
        "SELECT permite_reportes_ia FROM public.plan_saas WHERE codigo='BASICO'"
    ).fetchone()
    if ia is None or ia[0] is not False:
        fallos.append("BASICO no debe permitir reportes IA")

    empresa_id = conn.execute(
        "SELECT id FROM public.empresa WHERE slug=%s", (slug,)
    ).fetchone()
    if empresa_id is None:
        raise StagingError("No se creo la empresa bootstrap")
    datos["empresa_id"] = empresa_id[0]

    sin_columna = []
    sin_tenant = []
    for tabla in EMPRESAS:
        tiene = conn.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name='empresa_id'",
            (tabla,),
        ).fetchone()[0]
        if not tiene:
            sin_columna.append(tabla)
            continue
        nulos = conn.execute(
            f'SELECT count(*) FROM public."{tabla}" WHERE empresa_id IS NULL'
        ).fetchone()[0]
        if nulos and tabla != "bitacora":
            sin_tenant.append(tabla)
    datos["tablas_sin_empresa_id"] = sin_columna
    datos["tablas_con_filas_sin_tenant"] = sin_tenant
    if sin_columna:
        fallos.append(f"tablas sin empresa_id: {sin_columna}")
    if sin_tenant:
        fallos.append(f"tablas con filas sin empresa_id: {sin_tenant}")

    despues = _conteos(conn)
    datos["filas_antes"] = antes
    datos["filas_despues"] = despues
    if despues != antes:
        fallos.append(f"conteos alterados: antes={antes} despues={despues}")

    fk = conn.execute(
        "SELECT count(*) FROM pg_constraint WHERE contype='f' AND conname LIKE 'fk_tenant_%'"
    ).fetchone()[0]
    uq = conn.execute(
        "SELECT count(*) FROM pg_constraint WHERE contype='u' AND conname LIKE 'uq_tenant_%'"
    ).fetchone()[0]
    datos["fk_tenant"] = fk
    datos["uq_tenant"] = uq
    if fk < 40:
        fallos.append(f"FK compuestas insuficientes: {fk}")
    if uq < 25:
        fallos.append(f"UNIQUE por tenant insuficientes: {uq}")

    rls = conn.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relrowsecurity AND c.relname = ANY(%s)",
        (BASE_SAAS,),
    ).fetchone()[0]
    datos["rls_base_saas"] = rls
    if rls != 5:
        fallos.append(f"RLS no habilitado en las 5 tablas SaaS: {rls}")

    datos["membresias"] = conn.execute(
        "SELECT count(*) FROM public.usuario_empresa"
    ).fetchone()[0]
    if datos["membresias"] < 2:
        fallos.append("no se crearon membresias historicas")

    # --- Aislamiento A/B a nivel de integridad referencial -----------------
    conn.execute(
        "INSERT INTO public.empresa (razon_social,nombre_comercial,slug) "
        "VALUES ('Empresa B Ficticia','Empresa B','empresa-b-staging')"
    )
    empresa_b = conn.execute(
        "SELECT id FROM public.empresa WHERE slug='empresa-b-staging'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO public.empresa_configuracion (empresa_id) VALUES (%s)",
        (empresa_b,),
    )
    conn.execute(
        "INSERT INTO public.sucursal (id,ciudad_id,nombre,direccion,estado,empresa_id) "
        "VALUES (100,1,'Sucursal B','Calle B',TRUE,%s)",
        (empresa_b,),
    )
    conn.execute(
        "INSERT INTO public.categoria (id,nombre,estado,empresa_id) "
        "VALUES (100,'Polos',TRUE,%s)",
        (empresa_b,),
    )
    conn.execute(
        "INSERT INTO public.producto (id,categoria_id,nombre,precio,estado,empresa_id) "
        "VALUES (100,100,'Polo B',50,TRUE,%s)",
        (empresa_b,),
    )
    datos["mismo_nombre_entre_empresas"] = "permitido"

    cruzado_rechazado = False
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO public.producto (id,categoria_id,nombre,precio,estado,empresa_id) "
                "VALUES (101,1,'Producto B con categoria A',50,TRUE,%s)",
                (empresa_b,),
            )
            raise _DescartarPrueba()
    except _DescartarPrueba:
        pass
    except Exception:
        cruzado_rechazado = True
    datos["fk_cruzada_rechazada"] = cruzado_rechazado
    if not cruzado_rechazado:
        fallos.append("una FK compuesta no rechazo la referencia A/B")

    duplicado_rechazado = False
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO public.categoria (id,nombre,estado,empresa_id) "
                "VALUES (102,'Polos',TRUE,%s)",
                (empresa_id[0],),
            )
            raise _DescartarPrueba()
    except _DescartarPrueba:
        pass
    except Exception:
        duplicado_rechazado = True
    datos["unique_por_empresa_rechaza_duplicado"] = duplicado_rechazado
    if not duplicado_rechazado:
        fallos.append("el UNIQUE por empresa no rechazo un duplicado intra-tenant")

    conn.execute("DELETE FROM public.producto WHERE id=100")
    conn.execute("DELETE FROM public.categoria WHERE id=100")
    conn.execute("DELETE FROM public.sucursal WHERE id=100")
    conn.execute("DELETE FROM public.empresa_configuracion WHERE empresa_id=%s", (empresa_b,))
    conn.execute("DELETE FROM public.empresa WHERE id=%s", (empresa_b,))

    datos["fallos"] = fallos
    datos["ok"] = not fallos
    return datos


def preparar_esquema_historico(dsn: str, allow_remote: bool) -> dict[str, int]:
    """Crea el esquema heredado y datos ficticios, sin aplicar migraciones."""
    from sqlalchemy import create_engine

    _dsn_seguro(dsn, allow_remote)
    engine = create_engine(dsn, future=True)
    _esquema_heredado(engine)

    import psycopg

    with psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://"),
                         autocommit=True) as conn:
        conn.execute("SET search_path = public")
        _reducir_a_historico(conn)
        return _sembrar(conn)


def run(dsn: str, slug: str, allow_remote: bool, crear_membresias: bool) -> dict:
    import psycopg
    from sqlalchemy import create_engine

    _dsn_seguro(dsn, allow_remote)

    engine = create_engine(dsn, future=True)
    _esquema_heredado(engine)

    dsn_psycopg = dsn.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn_psycopg, autocommit=True) as conn:
        conn.execute("SET search_path = public")
        _reducir_a_historico(conn)
        antes = _sembrar(conn)
        _aplicar_migraciones(conn, slug, crear_membresias)
        primera = _verificar(conn, slug, antes)
        # Idempotencia: reaplicar el bloque completo no debe fallar ni duplicar.
        _aplicar_migraciones(conn, slug, crear_membresias)
        fk1 = conn.execute(
            "SELECT count(*) FROM pg_constraint WHERE contype='f' AND conname LIKE 'fk_tenant_%'"
        ).fetchone()[0]
        uq1 = conn.execute(
            "SELECT count(*) FROM pg_constraint WHERE contype='u' AND conname LIKE 'uq_tenant_%'"
        ).fetchone()[0]

    segunda = {
        "fk_tenant": fk1,
        "uq_tenant": uq1,
        "idempotente": fk1 == primera["fk_tenant"] and uq1 == primera["uq_tenant"],
    }
    if not segunda["idempotente"]:
        primera["fallos"].append("la segunda aplicacion duplico restricciones")
        primera["ok"] = False
    return {"primera": primera, "segunda": segunda}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.environ.get("FASHIONSTORE_STAGING_DSN"))
    parser.add_argument("--slug", default="staging-historico")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--sin-membresias", action="store_true")
    parser.add_argument(
        "--solo-esquema",
        action="store_true",
        help="Solo reconstruye el esquema heredado y los datos ficticios",
    )
    args = parser.parse_args(argv)

    if not args.dsn:
        print("Falta --dsn o FASHIONSTORE_STAGING_DSN", file=sys.stderr)
        return 2

    if args.solo_esquema:
        conteos = preparar_esquema_historico(args.dsn, args.allow_remote)
        print(json.dumps({"tablas_sembradas": conteos}, indent=2, ensure_ascii=False))
        return 0

    resultado = run(
        dsn=args.dsn,
        slug=args.slug,
        allow_remote=args.allow_remote,
        crear_membresias=not args.sin_membresias,
    )
    print(json.dumps(resultado, indent=2, ensure_ascii=False, default=str))
    return 0 if resultado["primera"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
