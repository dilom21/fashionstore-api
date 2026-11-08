"""Validacion integral y desechable de la propuesta 080 V2 (sin Supabase)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import psycopg
from argon2 import PasswordHasher
from psycopg import sql

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LAB5 = ROOT / "database" / "saas" / "laboratorio_pg17_5cb"
sys.path.insert(0, str(LAB5))
sys.path.insert(0, str(ROOT))

from lab_comun import Cluster, LabError  # noqa: E402
import lab_instalar  # noqa: E402

LAB5CA = ROOT / "database" / "saas" / "laboratorio_seguridad_5ca"

ORIGINAL = ROOT / "database/saas/preparacion_pg17/080_bootstrap_empresas_registro_cliente_PROPUESTA_NO_EJECUTAR.sql"
RESULTADO = HERE / "resultado.json"
GUARDA = re.compile(
    r"DO \$no_ejecutar\$ BEGIN\s+RAISE EXCEPTION '[^']+';\s+END \$no_ejecutar\$;\s*",
    re.S,
)


def check(items, codigo, condicion, detalle):
    items.append({"codigo": codigo, "pass": bool(condicion), "detalle": detalle})
    if not condicion:
        raise LabError(f"{codigo}: {detalle}")


def preparar_copia(cluster):
    texto = ORIGINAL.read_text(encoding="utf-8")
    copia, n = GUARDA.subn("", texto, count=1)
    if n != 1 or "GUARDA: PROPUESTA 080" not in texto:
        raise LabError("No se retiro exactamente la guarda 080 esperada")
    destino = cluster.datadir.parent / "080_v2_lab.sql"
    destino.write_text(
        "-- COPIA EFIMERA: unico cambio, guarda NO EJECUTAR retirada.\n" + copia,
        encoding="utf-8",
    )
    return destino, hashlib.sha256(texto.encode()).hexdigest()


def preparar_instalador(cluster):
    clave = secrets.token_urlsafe(32)
    dsn = cluster.dsn("postgres", cluster.passwd)
    with psycopg.connect(dsn, autocommit=True) as con, con.cursor() as cur:
        cur.execute(sql.SQL("CREATE ROLE fs_080_installer LOGIN CREATEROLE NOSUPERUSER NOCREATEDB NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Literal(clave)))
        cur.execute("GRANT fashionstore_bootstrap_owner TO fs_080_installer WITH ADMIN TRUE, INHERIT FALSE, SET FALSE")
        cur.execute("ALTER SCHEMA fashionstore_private OWNER TO fs_080_installer")
        for tabla in ("empresa","suscripcion","plan_saas","rol","usuario","usuario_empresa","cliente"):
            cur.execute(sql.SQL("ALTER TABLE public.{} OWNER TO fs_080_installer").format(sql.Identifier(tabla)))
    return cluster.dsn("fs_080_installer", clave)


def instalar_contrato_base(cluster, secreto):
    """Instala el contrato 050 previamente validado, sin regenerar sus copias."""
    cluster.archivo(LAB5 / "sql/010_baseline_desde_catalogo.sql")
    for nombre in ("010_saas_core_vanter.sql","020_tenant_expand_backfill.sql",
                   "030_tenant_integrity.sql","040_routines_views_hardening.sql"):
        cluster.archivo(ROOT / "database/saas/preparacion_pg17" / nombre)
    cluster.archivo(LAB5 / "sql/030_lab_fixtures.sql")
    cluster.archivo(LAB5 / "sql/000_lab_vault_substituto.sql")
    admin = cluster.dsn("postgres",cluster.passwd)
    with psycopg.connect(admin) as con, con.cursor() as cur:
        cur.execute("INSERT INTO vault.secrets(name,secret,description) VALUES(%s,%s,%s)",
                    ("fashionstore_tenant_jwt_hs256_active",secreto,"Lab 080"))
        con.commit()
    for nombre in ("050a_lab.sql","050b_lab.sql","050c_lab.sql"):
        cluster.archivo(LAB5CA / "sql" / nombre)
    cluster.archivo(LAB5CA / "sql/060_lab_correcciones.sql")
    # El snapshot 5CA precede bootstrap_empresa_id; se agrega el mismo contrato
    # estrecho ya instalado en el sistema actual, sin privilegios al login.
    cluster.sql("""BEGIN;
      GRANT USAGE ON SCHEMA fashionstore_private TO fashionstore_bootstrap_owner;
      GRANT SELECT(id,slug,estado) ON public.empresa TO fashionstore_bootstrap_owner;
      CREATE POLICY p_bootstrap_owner_empresa ON public.empresa FOR SELECT
        TO fashionstore_bootstrap_owner USING(estado IS TRUE);
      CREATE FUNCTION fashionstore_private.bootstrap_empresa_id(p_slug text)
      RETURNS TABLE(empresa_id bigint) LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $$
        SELECT e.id FROM public.empresa e
        WHERE e.slug=lower(btrim(p_slug)) AND e.estado LIMIT 2 $$;
      REVOKE ALL ON FUNCTION fashionstore_private.bootstrap_empresa_id(text) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION fashionstore_private.bootstrap_empresa_id(text) TO fashionstore_bootstrap;
      ALTER FUNCTION fashionstore_private.bootstrap_empresa_id(text) OWNER TO fashionstore_bootstrap_owner;
      COMMIT;""")
    return {"baseline":"5CA validado + bootstrap_empresa_id vigente"}


def ejecutar_archivo(dsn, ruta):
    with psycopg.connect(dsn, autocommit=True) as con, con.cursor() as cur:
        cur.execute(sql.SQL(ruta.read_text(encoding="utf-8")), prepare=False)


def consulta(dsn, sentencia, params=()):
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        if params:
            cur.execute(sentencia, params)
        else:
            cur.execute(sentencia)
        return cur.fetchall()


def llamar_registro(dsn, slug, correo, ci, password_hash):
    try:
        with psycopg.connect(dsn) as con, con.cursor() as cur:
            cur.execute("SELECT * FROM fashionstore_private.bootstrap_registrar_cliente(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (slug,correo,password_hash,"Prueba","Aislada",ci,"70000000","OTRO",date(1995,1,1)))
            fila = cur.fetchone()
        return {"ok": True, "fila": fila}
    except psycopg.Error as exc:
        return {"ok": False, "sqlstate": exc.sqlstate, "mensaje": str(exc)}


def main():
    cluster = None
    pruebas = []
    try:
        cluster = Cluster.crear("fashionstore_lab_080")
        secreto = secrets.token_urlsafe(48)
        instalacion = instalar_contrato_base(cluster,secreto)
        copia, hash_original = preparar_copia(cluster)
        instalador_dsn = preparar_instalador(cluster)
        ejecutar_archivo(instalador_dsn, copia)

        admin_dsn = cluster.dsn("postgres", cluster.passwd)
        meta = consulta(admin_dsn, """
          SELECT p.proname,pg_get_userbyid(p.proowner),p.prosecdef,r.rolbypassrls,r.rolcanlogin
          FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          JOIN pg_roles r ON r.oid=p.proowner
          WHERE n.nspname='fashionstore_private' AND p.proname LIKE 'bootstrap_%'
          ORDER BY p.proname""")
        nuevas = [x for x in meta if x[0] in ("bootstrap_empresas_publicas","bootstrap_registrar_cliente")]
        check(pruebas,"INST-01",len(nuevas)==2 and all(x[1]=="fashionstore_bootstrap_owner" and x[2] and not x[3] and not x[4] for x in nuevas),nuevas)
        residuos = consulta(admin_dsn,"""
          SELECT pg_has_role('fs_080_installer','fashionstore_bootstrap_owner','SET'),
          has_schema_privilege('fashionstore_bootstrap_owner','fashionstore_private','CREATE'),
          has_table_privilege('fashionstore_bootstrap','public.cliente','INSERT')""")[0]
        check(pruebas,"INST-02",residuos==(False,False,False),residuos)

        logins = lab_instalar.provisionar_logins(cluster)
        bootstrap_dsn = logins["bootstrap"]["dsn"]
        empresas = consulta(bootstrap_dsn,"SELECT * FROM fashionstore_private.bootstrap_empresas_publicas()")
        check(pruebas,"SEL-01",len(empresas)>=2 and all(len(x)==2 for x in empresas),empresas)
        slugs = [x[0] for x in empresas]
        slug_a,slug_b = slugs[:2]

        password = "Aislada-080-123!"
        ph = PasswordHasher().hash(password)
        alta = llamar_registro(bootstrap_dsn,slug_a,"nuevo.080@lab.test","CI-080-A",ph)
        check(pruebas,"REG-01",alta["ok"],alta)
        uid,cid,_ = alta["fila"]
        filas = consulta(admin_dsn,"""SELECT u.id,ue.empresa_id,c.empresa_id,r.nombre
          FROM usuario u JOIN usuario_empresa ue ON ue.usuario_id=u.id
          JOIN cliente c ON c.usuario_id=u.id AND c.empresa_id=ue.empresa_id
          JOIN rol r ON r.id=ue.rol_id WHERE u.id=%s AND c.id=%s""",(uid,cid))
        check(pruebas,"REG-02",len(filas)==1 and filas[0][1]==filas[0][2] and filas[0][3]=="CLIENTE",filas)

        dup = llamar_registro(bootstrap_dsn,slug_b,"nuevo.080@lab.test","CI-080-X",ph)
        check(pruebas,"REG-03",not dup["ok"] and dup["sqlstate"]=="P2002",dup)
        ci_dup = llamar_registro(bootstrap_dsn,slug_a,"otro.080@lab.test","CI-080-A",ph)
        restos = consulta(admin_dsn,"SELECT count(*) FROM usuario WHERE correo='otro.080@lab.test'")[0][0]
        check(pruebas,"REG-04",not ci_dup["ok"] and ci_dup["sqlstate"]=="P2003" and restos==0,{"resultado":ci_dup,"restos":restos})

        misma_ci = llamar_registro(bootstrap_dsn,slug_b,"empresa2.080@lab.test","CI-080-A",ph)
        check(pruebas,"REG-05",misma_ci["ok"],misma_ci)

        eid_b = consulta(admin_dsn,"SELECT id FROM empresa WHERE slug=%s",(slug_b,))[0][0]
        with psycopg.connect(admin_dsn) as con, con.cursor() as cur:
            cur.execute("UPDATE empresa SET estado=false WHERE id=%s",(eid_b,)); con.commit()
        inactive = llamar_registro(bootstrap_dsn,slug_b,"inactiva.080@lab.test","CI-080-I",ph)
        check(pruebas,"REG-06",not inactive["ok"] and inactive["sqlstate"]=="P2001",inactive)
        with psycopg.connect(admin_dsn) as con, con.cursor() as cur:
            cur.execute("UPDATE empresa SET estado=true WHERE id=%s",(eid_b,));
            cur.execute("UPDATE suscripcion SET estado='SUSPENDIDA' WHERE empresa_id=%s AND estado='ACTIVA'",(eid_b,)); con.commit()
        suspended = llamar_registro(bootstrap_dsn,slug_b,"suspendida.080@lab.test","CI-080-S",ph)
        selector2 = consulta(bootstrap_dsn,"SELECT slug FROM fashionstore_private.bootstrap_empresas_publicas()")
        check(pruebas,"REG-07",not suspended["ok"] and suspended["sqlstate"]=="P2001" and slug_b not in [x[0] for x in selector2],{"registro":suspended,"selector":selector2})
        with psycopg.connect(admin_dsn) as con, con.cursor() as cur:
            cur.execute("UPDATE suscripcion SET estado='ACTIVA',fecha_inicio=CURRENT_DATE-20,fecha_vencimiento=CURRENT_DATE-1 WHERE empresa_id=%s",(eid_b,)); con.commit()
        expired = llamar_registro(bootstrap_dsn,slug_b,"vencida.080@lab.test","CI-080-V",ph)
        selector3 = consulta(bootstrap_dsn,"SELECT slug FROM fashionstore_private.bootstrap_empresas_publicas()")
        check(pruebas,"REG-08",not expired["ok"] and expired["sqlstate"]=="P2001" and slug_b not in [x[0] for x in selector3],{"registro":expired,"selector":selector3})

        correo_carrera="carrera.080@lab.test"
        with ThreadPoolExecutor(max_workers=2) as pool:
            carreras=list(pool.map(lambda _: llamar_registro(bootstrap_dsn,slug_a,correo_carrera,"CI-080-C",ph),range(2)))
        check(pruebas,"CON-01",sum(x["ok"] for x in carreras)==1 and sorted(x.get("sqlstate","") for x in carreras if not x["ok"])==["P2002"],carreras)

        auditoria=consulta(admin_dsn,"SELECT entidad_afectada,empresa_id FROM bitacora WHERE entidad_afectada IN('usuario','cliente') AND descripcion LIKE %s ORDER BY id",("%"+str(cid)+"%",))
        cliente_audit=consulta(admin_dsn,"SELECT count(*) FROM bitacora WHERE entidad_afectada='cliente' AND empresa_id=(SELECT empresa_id FROM cliente WHERE id=%s)",(cid,))[0][0]
        check(pruebas,"AUD-01",cliente_audit>=1,{"eventos_cliente":cliente_audit,"muestra":auditoria})

        # HTTP real sobre los pools restringidos del cluster efimero.
        sqlalchemy_dsn=lambda value: value.replace("postgresql://","postgresql+psycopg://",1)
        os.environ.update({"DATABASE_URL":sqlalchemy_dsn(logins["api"]["dsn"]),
          "DATABASE_BOOTSTRAP_URL":sqlalchemy_dsn(bootstrap_dsn),
          "DATABASE_PLATFORM_URL":sqlalchemy_dsn(logins["platform"]["dsn"]),
          "SECRET_KEY":secreto,"SAAS_AUTH_ENABLED":"true"})
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            selector_http=client.get("/auth/tenant/empresas")
            registro_http=client.post("/auth/tenant/clientes/registro",json={
              "empresa_slug":slug_a,"correo":"http.080@lab.test","password":password,
              "password_confirmacion":password,"nombre":"Cliente","apellido":"HTTP",
              "ci":"CI-080-HTTP","telefono":"70000001","sexo":"OTRO",
              "fecha_nacimiento":"1994-02-03"})
            login_http=client.post("/auth/tenant/login",json={"empresa_slug":slug_a,"correo":"http.080@lab.test","password":password,"contexto":"cliente"})
        check(pruebas,"HTTP-01",selector_http.status_code==200 and all(set(x)=={"slug","nombre_comercial"} for x in selector_http.json()),{"status":selector_http.status_code,"body":selector_http.json()})
        check(pruebas,"HTTP-02",registro_http.status_code==201 and "access_token" not in registro_http.json(),{"status":registro_http.status_code,"body":registro_http.json()})
        check(pruebas,"HTTP-03",login_http.status_code==200 and login_http.json().get("contexto")=="cliente",{"status":login_http.status_code})

        preexistentes=consulta(admin_dsn,"SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='fashionstore_private' AND p.proname IN('bootstrap_credencial','bootstrap_empresa_id')")[0][0]
        reversal="""BEGIN;
          DROP FUNCTION fashionstore_private.bootstrap_registrar_cliente(text,text,text,text,text,text,text,text,date);
          DROP FUNCTION fashionstore_private.bootstrap_empresas_publicas();
          DROP POLICY p_bootstrap_080_cliente_id_select ON public.cliente;
          DROP POLICY p_bootstrap_080_cliente_insert ON public.cliente;
          DROP POLICY p_bootstrap_080_membresia_insert ON public.usuario_empresa;
          DROP POLICY p_bootstrap_080_usuario_insert ON public.usuario;
          DROP POLICY p_bootstrap_080_rol_select ON public.rol;
          DROP POLICY p_bootstrap_080_plan_select ON public.plan_saas;
          DROP POLICY p_bootstrap_080_suscripcion_select ON public.suscripcion;
          REVOKE SELECT(id),INSERT ON public.cliente FROM fashionstore_bootstrap_owner;
          REVOKE INSERT ON public.usuario_empresa,public.usuario FROM fashionstore_bootstrap_owner;
          REVOKE SELECT ON public.rol,public.plan_saas,public.suscripcion FROM fashionstore_bootstrap_owner;
          REVOKE SELECT(nombre_comercial) ON public.empresa FROM fashionstore_bootstrap_owner;
          DO $$ DECLARE seq text; BEGIN
            FOREACH seq IN ARRAY ARRAY[pg_get_serial_sequence('public.usuario','id'),
              pg_get_serial_sequence('public.usuario_empresa','id'),
              pg_get_serial_sequence('public.cliente','id')] LOOP
              EXECUTE format('REVOKE USAGE ON SEQUENCE %s FROM fashionstore_bootstrap_owner',seq);
            END LOOP;
          END $$;
          COMMIT;"""
        with psycopg.connect(instalador_dsn, autocommit=True) as con, con.cursor() as cur: cur.execute(reversal,prepare=False)
        despues=consulta(admin_dsn,"SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='fashionstore_private' AND p.proname IN('bootstrap_credencial','bootstrap_empresa_id')")[0][0]
        creados=consulta(admin_dsn,"SELECT count(*) FROM usuario WHERE correo IN('nuevo.080@lab.test','empresa2.080@lab.test','carrera.080@lab.test')")[0][0]
        residuos_080=consulta(admin_dsn,"""SELECT
          (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='fashionstore_private' AND p.proname IN('bootstrap_empresas_publicas','bootstrap_registrar_cliente')),
          (SELECT count(*) FROM pg_policies WHERE policyname LIKE 'p_bootstrap_080_%'),
          has_table_privilege('fashionstore_bootstrap_owner','public.usuario','INSERT'),
          has_column_privilege('fashionstore_bootstrap_owner','public.cliente','id','SELECT'),
          has_sequence_privilege('fashionstore_bootstrap_owner',pg_get_serial_sequence('public.cliente','id'),'USAGE')""")[0]
        check(pruebas,"REV-01",preexistentes==2 and despues==2 and creados==3 and residuos_080==(0,0,False,False,False),
          {"preexistentes":despues,"datos_conservados":creados,"residuos_080":residuos_080})

        data={"postgres":cluster.escalar("SHOW server_version"),"hash_original":hash_original,
          "copia_temporal":copia.name,"baseline":instalacion,"pruebas":pruebas,
          "pass":sum(x["pass"] for x in pruebas),"fail":sum(not x["pass"] for x in pruebas)}
        RESULTADO.write_text(json.dumps(data,indent=2,ensure_ascii=False,default=str),encoding="utf-8")
        print(json.dumps({"pass":data["pass"],"fail":data["fail"],"resultado":str(RESULTADO)},ensure_ascii=False))
        return 0
    except Exception as exc:
        traceback.print_exc()
        RESULTADO.write_text(json.dumps({"error":type(exc).__name__,"detalle":str(exc),"pruebas":pruebas},indent=2,ensure_ascii=False,default=str),encoding="utf-8")
        print(json.dumps({"error":type(exc).__name__,"detalle":str(exc)},ensure_ascii=False))
        return 1
    finally:
        if cluster is not None: cluster.destruir()


if __name__=="__main__": raise SystemExit(main())
