"""Genera el paquete CU11 desde los tres borradores auditados existentes."""
from pathlib import Path
import hashlib

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
BORRADORES = ROOT / "database/saas/borradores"

PROCS = (
    ("12_sp_crear_reserva_tenant.sql.txt", "sp_crear_reserva_tenant",
     "bigint,bigint,bigint,timestamptz,text,bigint"),
    ("13_sp_cancelar_reserva_tenant.sql.txt", "sp_cancelar_reserva_tenant",
     "bigint,bigint,bigint,text"),
    ("14_sp_finalizar_reserva_sin_compra_tenant.sql.txt",
     "sp_finalizar_reserva_sin_compra_tenant", "bigint,bigint,bigint,text"),
)


def cuerpo(nombre: str) -> str:
    src = (BORRADORES / nombre).read_text(encoding="utf-8")
    inicio = src.index("CREATE OR REPLACE PROCEDURE")
    fin = src.index("\n-- ", inicio)
    sql = src[inicio:fin].strip()
    sql = sql.replace("SECURITY INVOKER", "SECURITY DEFINER")
    sql = sql.replace(
        "SET search_path TO public, pg_temp",
        "SET search_path TO pg_catalog, pg_temp",
    )
    guardia = """BEGIN
    IF public.fs_verificar_actor_reserva(
        p_empresa_id, p_usuario_id
    ) IS NOT TRUE THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;"""
    sql = sql.replace("BEGIN", guardia, 1)
    sql = sql.replace(
        "JOIN public.temporada tem ON tem.id=i.temporada_id AND tem.empresa_id=p_empresa_id",
        "JOIN public.temporada tem ON tem.id=i.temporada_id "
        "AND tem.empresa_id=p_empresa_id AND tem.estado IS TRUE",
    )
    # FOR SHARE exigiria UPDATE al owner sobre membresia/suscripcion. Las
    # policies vuelven a validar el JWT en cada sentencia; no se amplian ACL.
    sql = sql.replace("\n    FOR SHARE OF ue, ss;", ";")
    sql = sql.replace("\n    FOR SHARE OF ue,ss;", ";")
    return sql


def generar() -> str:
    procs = "\n\n".join(cuerpo(n) for n, _, _ in PROCS)
    alteraciones = "\n".join(
        f"REVOKE ALL ON PROCEDURE public.{n}({sig}) FROM PUBLIC;\n"
        f"GRANT EXECUTE ON PROCEDURE public.{n}({sig}) TO fashionstore_api;\n"
        f"ALTER PROCEDURE public.{n}({sig}) OWNER TO fashionstore_sp_owner;"
        for _, n, sig in PROCS
    )
    return f"""-- CU11 RESERVAS SAAS - PROPUESTA NO EJECUTAR SIN AUTORIZACION
-- PostgreSQL 17. Instalacion atomica; no habilita SAAS_RESERVAS_ENABLED.
\\echo 'NO EJECUTAR EN SUPABASE SIN AUTORIZACION EXPRESA'
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='120s';

DO $pre$
BEGIN
 IF current_setting('server_version_num')::int < 170000 THEN
  RAISE EXCEPTION 'Requiere PostgreSQL 17'; END IF;
 IF NOT (SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user) THEN
  RAISE EXCEPTION 'Instalador sin CREATEROLE'; END IF;
 IF to_regprocedure('public.fs_verificar_actor_reserva(bigint,bigint)') IS NOT NULL
 OR to_regprocedure('public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint)') IS NOT NULL
 OR to_regprocedure('public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text)') IS NOT NULL
 OR to_regprocedure('public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text)') IS NOT NULL THEN
  RAISE EXCEPTION 'Objetos CU11 ya existen; detener y auditar'; END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN
   ('fashionstore_context_owner','fashionstore_sp_owner')
   AND (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole
        OR rolcreatedb OR rolinherit))
 OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fashionstore_api'
   AND (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole OR rolcreatedb))
 OR (SELECT count(*) FROM pg_roles WHERE rolname IN
   ('fashionstore_context_owner','fashionstore_sp_owner','fashionstore_api'))<>3 THEN
  RAISE EXCEPTION 'Roles base ausentes o inseguros'; END IF;
 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname IN
   ('reserva','detalle_reserva','inventario','carrito')
   AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)) THEN
  RAISE EXCEPTION 'RLS/FORCE RLS incompleto'; END IF;
 IF NOT has_table_privilege('fashionstore_sp_owner','public.reserva','INSERT')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.reserva','UPDATE')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.detalle_reserva','INSERT')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.inventario','UPDATE')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.carrito','UPDATE')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.movimiento_inventario','INSERT') THEN
  RAISE EXCEPTION 'Owner SP sin ACL minima ya aprobada'; END IF;
 IF EXISTS (
  SELECT 1 FROM unnest(ARRAY[
   'usuario_empresa','usuario','empresa','rol','suscripcion','plan_saas',
   'carrito','cliente','sucursal','detalle_carrito','inventario',
   'variante_producto','producto','categoria','temporada','talla','color',
   'reserva','detalle_reserva','empleado','rol_funcion','funcion','accion','venta'
  ]) t(nombre)
  WHERE NOT has_table_privilege(
   'fashionstore_sp_owner',format('public.%I',nombre),'SELECT')) THEN
  RAISE EXCEPTION 'Owner SP sin SELECT requerido por CU11'; END IF;
 IF EXISTS (
  SELECT 1 FROM unnest(ARRAY[
   'reserva_id_seq','detalle_reserva_id_seq','movimiento_inventario_id_seq'
  ]) s(nombre)
  WHERE NOT has_sequence_privilege(
   'fashionstore_sp_owner',format('public.%I',nombre),'USAGE')) THEN
  RAISE EXCEPTION 'Owner SP sin USAGE de secuencias CU11'; END IF;
 IF EXISTS (
  WITH requeridas(tabla,politica,comando,tenant) AS (VALUES
   ('carrito','p_sp_tenant_carrito','ALL',true),
   ('detalle_carrito','p_sp_tenant_detalle_carrito','ALL',true),
   ('inventario','p_sp_tenant_inventario','ALL',true),
   ('reserva','p_sp_tenant_reserva','ALL',true),
   ('detalle_reserva','p_sp_tenant_detalle_reserva','ALL',true),
   ('movimiento_inventario','p_sp_tenant_movimiento_inventario','ALL',true),
   ('venta','p_sp_tenant_venta','ALL',true),
   ('cliente','p_sp_select_cliente','SELECT',true),
   ('empleado','p_sp_select_empleado','SELECT',true),
   ('sucursal','p_sp_select_sucursal','SELECT',true),
   ('categoria','p_sp_select_categoria','SELECT',true),
   ('producto','p_sp_select_producto','SELECT',true),
   ('variante_producto','p_sp_select_variante_producto','SELECT',true),
   ('temporada','p_sp_select_temporada','SELECT',true),
   ('empresa','p_sp_empresa_select','SELECT',true),
   ('usuario_empresa','p_sp_membresia_select','SELECT',true),
   ('suscripcion','p_sp_suscripcion_select','SELECT',true),
   ('usuario','p_sp_select_global_usuario','SELECT',false),
   ('rol','p_sp_select_global_rol','SELECT',false),
   ('plan_saas','p_sp_select_global_plan_saas','SELECT',false),
   ('talla','p_sp_select_global_talla','SELECT',false),
   ('color','p_sp_select_global_color','SELECT',false),
   ('funcion','p_sp_select_global_funcion','SELECT',false),
   ('accion','p_sp_select_global_accion','SELECT',false),
   ('rol_funcion','p_sp_select_global_rol_funcion','SELECT',false)
  )
  SELECT 1 FROM requeridas r
  LEFT JOIN pg_policies p ON p.schemaname='public'
   AND p.tablename=r.tabla AND p.policyname=r.politica
   AND p.cmd=r.comando AND 'fashionstore_sp_owner'=ANY(p.roles)
  WHERE p.policyname IS NULL
     OR (r.tenant AND position('actor_empresa_id' in coalesce(p.qual,''))=0)
     OR (r.comando='ALL' AND position(
          'actor_empresa_id' in coalesce(p.with_check,''))=0)
 ) THEN
  RAISE EXCEPTION 'Policies RLS CU11 ausentes o incompatibles'; END IF;
END $pre$;

CREATE FUNCTION public.fs_verificar_actor_reserva(
 p_empresa_id bigint,p_usuario_id bigint
) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $actor$
 SELECT p_empresa_id= fashionstore_private.actor_empresa_id()
    AND p_usuario_id= fashionstore_private.actor_usuario_id()
$actor$;
REVOKE ALL ON FUNCTION public.fs_verificar_actor_reserva(bigint,bigint) FROM PUBLIC;

{procs}

-- Marcador verificable: CREATE OR REPLACE conserva comentarios, por lo que un
-- cambio posterior del cuerpo rompe la comparación md5(prosrc) de 081/082.
DO $marcar$
DECLARE r record; marca text:='fashionstore.cu11.080.v1';
 reserva_seq bigint; detalle_seq bigint; movimiento_seq bigint;
BEGIN
 SELECT last_value INTO reserva_seq FROM public.reserva_id_seq;
 SELECT last_value INTO detalle_seq FROM public.detalle_reserva_id_seq;
 SELECT last_value INTO movimiento_seq FROM public.movimiento_inventario_id_seq;
 FOR r IN SELECT p.oid,p.prokind,p.oid::regprocedure firma,md5(p.prosrc) hash
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.proname IN (
   'fs_verificar_actor_reserva','sp_crear_reserva_tenant',
   'sp_cancelar_reserva_tenant','sp_finalizar_reserva_sin_compra_tenant')
 LOOP
  EXECUTE format('COMMENT ON %s %s IS %L',
   CASE WHEN r.prokind='p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,
   r.firma,
   marca||'|body_md5='||r.hash||CASE WHEN r.prokind='f'
    THEN '|installed_at='||transaction_timestamp()::text
      ||'|reserva_seq='||reserva_seq::text
      ||'|detalle_seq='||detalle_seq::text
      ||'|movimiento_seq='||movimiento_seq::text
    ELSE '' END);
 END LOOP;
END $marcar$;

-- Transferencias: SET temporal, CREATE temporal y retirada en la misma TX.
GRANT fashionstore_context_owner TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;
GRANT CREATE ON SCHEMA public TO fashionstore_context_owner;
GRANT EXECUTE ON FUNCTION public.fs_verificar_actor_reserva(bigint,bigint)
 TO fashionstore_api,fashionstore_sp_owner;
ALTER FUNCTION public.fs_verificar_actor_reserva(bigint,bigint)
 OWNER TO fashionstore_context_owner;
REVOKE CREATE ON SCHEMA public FROM fashionstore_context_owner;
REVOKE fashionstore_context_owner FROM CURRENT_USER GRANTED BY CURRENT_USER;

GRANT fashionstore_sp_owner TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;
GRANT CREATE ON SCHEMA public TO fashionstore_sp_owner;
{alteraciones}
REVOKE CREATE ON SCHEMA public FROM fashionstore_sp_owner;
REVOKE fashionstore_sp_owner FROM CURRENT_USER GRANTED BY CURRENT_USER;

COMMIT;
"""


if __name__ == "__main__":
    destino = HERE / "080_instalar_reservas_saas_PROPUESTA_NO_EJECUTAR.sql"
    destino.write_text(generar(), encoding="utf-8", newline="\n")
    nombres = (
        destino.name,
        "081_verificar_reservas_saas.sql",
        "082_revertir_reservas_saas_CONTROLADO.sql",
    )
    manifiesto = []
    for nombre in nombres:
        ruta = HERE / nombre
        if ruta.is_file():
            manifiesto.append(
                f"{hashlib.sha256(ruta.read_bytes()).hexdigest()}  {nombre}")
    (HERE / "MANIFIESTO.sha256").write_text(
        "\n".join(manifiesto) + "\n", encoding="ascii", newline="\n")
    print(destino)
