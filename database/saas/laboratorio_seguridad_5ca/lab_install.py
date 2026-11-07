"""Instalación del laboratorio 5CA: baseline, migraciones, fixtures y 050a-c.

Prepara además los tres logins de servicio (API, bootstrap, platform_admin) con
contraseñas aleatorias en memoria y el secreto JWT dentro del sustituto de Vault.
Ningún secreto se escribe en archivos del repositorio ni se imprime.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path

import psycopg
from psycopg import sql

import lab_baseline
from lab_common import HERE, SQLDIR, LabError, paso

ROOT = HERE.parents[2]
MIGR = ROOT / "database" / "saas" / "preparacion_pg17"
PROPUESTAS = {
    "050a": "050a_identidad_jwt_roles_PROPUESTA_NO_EJECUTAR.sql",
    "050b": "050b_rls_policies_PROPUESTA_NO_EJECUTAR.sql",
    "050c": "050c_acl_procedimientos_PROPUESTA_NO_EJECUTAR.sql",
}
PATRON_GUARDA = re.compile(
    r"DO \$\$ BEGIN\s+RAISE EXCEPTION 'BLOQUEADO[^']*';\s+END \$\$;\s*\n", re.S)

LOGINS = {
    "api": "fashionstore_5ca_api",
    "bootstrap": "fashionstore_5ca_bootstrap",
    "platform": "fashionstore_5ca_platform",
}
ROLES_GRUPO = {
    "api": "fashionstore_api",
    "bootstrap": "fashionstore_bootstrap",
    "platform": "fashionstore_platform_admin",
}


def generar_copias_lab() -> list[dict[str, str]]:
    """Copia 050a-c retirando ÚNICAMENTE la guarda de bloqueo."""
    registro = []
    for clave, nombre in PROPUESTAS.items():
        origen = MIGR / nombre
        texto = origen.read_text(encoding="utf-8")
        nuevo, retiradas = PATRON_GUARDA.subn("", texto, count=1)
        if retiradas != 1:
            raise LabError(f"No se pudo retirar exactamente una guarda de {nombre}")
        destino = SQLDIR / f"{clave}_lab.sql"
        cabecera = (
            f"-- COPIA DE LABORATORIO 5CA de {nombre}\n"
            "-- Cambio único: se retiró la guarda 'BLOQUEADO' de la primera línea\n"
            "-- para poder ensayarla en un clúster desechable. Nada más se alteró.\n"
            f"-- sha256 del original: {hashlib.sha256(texto.encode()).hexdigest()}\n"
        )
        destino.write_text(cabecera + nuevo, encoding="utf-8")
        registro.append({"propuesta": clave, "origen": nombre,
                         "copia": destino.name,
                         "sha256_original": hashlib.sha256(texto.encode()).hexdigest()})
    return registro


def _psql(cluster, ruta: Path) -> None:
    paso(f"aplicando {Path(ruta).name}")
    cluster.archivo(Path(ruta))


def instalar(cluster, secreto_jwt: str) -> dict[str, object]:
    """Devuelve un resumen estructural del laboratorio instalado."""
    lab_baseline.main()
    _psql(cluster, lab_baseline.SALIDA)
    for nombre in ("010_saas_core_vanter.sql", "020_tenant_expand_backfill.sql",
                   "030_tenant_integrity.sql", "040_routines_views_hardening.sql"):
        _psql(cluster, MIGR / nombre)
    _psql(cluster, SQLDIR / "030_lab_fixtures.sql")
    _psql(cluster, SQLDIR / "000_lab_vault_substituto.sql")
    # 050a exige que la clave ya exista en Vault: se carga ANTES de la propuesta.
    paso("cargando secreto JWT en el sustituto local de Vault")
    vault = sembrar_vault(cluster, secreto_jwt)
    copias = generar_copias_lab()
    for clave in ("050a", "050b", "050c"):
        _psql(cluster, SQLDIR / f"{clave}_lab.sql")
    _psql(cluster, SQLDIR / "060_lab_correcciones.sql")
    _psql(cluster, SQLDIR / "070_lab_sp_tenant.sql")
    paso("instalación SQL completa")
    return {"copias_050": copias, **vault}


def sembrar_vault(cluster, secreto_jwt: str) -> dict[str, object]:
    """Inserta el secreto HS256 sólo en el sustituto local de Vault."""
    dsn = cluster.dsn("postgres", cluster.passwd)
    with psycopg.connect(dsn, connect_timeout=10) as conexion:
        with conexion.cursor() as cur:
            cur.execute("DELETE FROM vault.secrets WHERE name LIKE 'fashionstore_tenant_jwt_hs256%'")
            cur.execute(
                "INSERT INTO vault.secrets(name, secret, description) VALUES (%s,%s,%s)",
                ("fashionstore_tenant_jwt_hs256_active", secreto_jwt,
                 "Laboratorio 5CA: sustituto local, no es Supabase Vault"))
        conexion.commit()
        with conexion.cursor() as cur:
            cur.execute("SELECT count(*) FROM vault.decrypted_secrets")
            total = cur.fetchone()[0]
    return {"secretos_en_vault_local": total}


def provisionar_logins(cluster) -> dict[str, dict[str, str]]:
    """Crea los tres logins de servicio con contraseñas aleatorias en memoria.

    050c deja esta operación fuera de la migración a propósito. Los logins
    heredan (INHERIT) el rol de grupo para que RLS y ACL se apliquen sin SET ROLE.
    """
    dsn_admin = cluster.dsn("postgres", cluster.passwd)
    salida: dict[str, dict[str, str]] = {}
    with psycopg.connect(dsn_admin, connect_timeout=10) as conexion:
        conexion.autocommit = True
        with conexion.cursor() as cur:
            for tipo, login in LOGINS.items():
                clave = secrets.token_urlsafe(36)
                cur.execute(sql.SQL("DROP ROLE IF EXISTS {}")
                            .format(sql.Identifier(login)))
                cur.execute(sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOREPLICATION NOBYPASSRLS INHERIT PASSWORD {}"
                ).format(sql.Identifier(login), sql.Literal(clave)))
                cur.execute(sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(ROLES_GRUPO[tipo]), sql.Identifier(login)))
                salida[tipo] = {"rol": login, "dsn": cluster.dsn(login, clave)}
    paso("logins de servicio creados: " + ", ".join(v["rol"] for v in salida.values()))
    return salida


def fijar_hashes_argon2(cluster, password_plano: str) -> list[str]:
    """Reemplaza el hash marcador por Argon2 real (igual que usa FastAPI)."""
    from argon2 import PasswordHasher

    hasher = PasswordHasher()
    hash_real = hasher.hash(password_plano)
    dsn = cluster.dsn("postgres", cluster.passwd)
    with psycopg.connect(dsn, connect_timeout=10) as conexion:
        with conexion.cursor() as cur:
            cur.execute(
                "UPDATE public.usuario SET password_hash = %s "
                "WHERE correo LIKE '%%@lab.test'", (hash_real,))
            afectados = cur.rowcount
        conexion.commit()
    paso(f"hashes Argon2 de laboratorio actualizados: {afectados}")
    return ["a1.admin@lab.test", "a2.cliente@lab.test", "a3.cliente2@lab.test",
            "b1.cliente@lab.test", "b2.admin@lab.test"]


CONSULTA_RESUMEN = """
SELECT json_build_object(
 'tablas',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname='public' AND c.relkind='r'),
 'vistas',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname='public' AND c.relkind='v'),
 'fk',(SELECT count(*) FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid
       JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND co.contype='f' AND co.convalidated),
 'fk_no_validadas',(SELECT count(*) FROM pg_constraint co
       JOIN pg_class c ON c.oid=co.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND co.contype='f' AND NOT co.convalidated),
 'indices',(SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
       JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND i.indisvalid),
 'triggers',(SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
       JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND NOT t.tgisinternal),
 'tablas_rls',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND c.relkind='r' AND c.relrowsecurity),
 'tablas_force_rls',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity),
 'politicas',(SELECT count(*) FROM pg_policies WHERE schemaname='public'),
 'funciones_privadas',(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
       WHERE n.nspname='fashionstore_private'),
 'empresas',(SELECT count(*) FROM public.empresa),
 'planes',(SELECT count(*) FROM public.plan_saas),
 'membresias',(SELECT count(*) FROM public.usuario_empresa)
)::text
"""


def resumen_estructural(cluster) -> dict[str, object]:
    return json.loads(cluster.escalar(CONSULTA_RESUMEN))
