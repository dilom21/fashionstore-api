"""Instalación del laboratorio 5CB: baseline, 010-040, fixtures y 050a-c.

Diferencias con el arnés 5CA que este módulo aprovecha a propósito:

- Se aplican las migraciones CORREGIDAS ``010``-``040`` de ``preparacion_pg17``
  (incluida la 040 con ``trg_bitacora_categoria``), no una copia.
- NO se aplica ``060_lab_correcciones.sql``: 050a-050c deben sostenerse solas.
- Las 050a-050c se copian retirando ÚNICAMENTE la guarda ``BLOQUEADO``; el
  sha256 del original se registra para poder auditar que nada más cambió. Las
  correcciones que el laboratorio demuestra bloqueantes se aplican aparte, en
  ``CORRECCIONES_5CB``, y quedan anotadas dentro de la copia: así la evidencia
  distingue «la propuesta tal cual falla» de «con este parche concreto pasa».

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

from lab_comun import HERE, SQLDIR, LabError, paso

ROOT = HERE.parents[2]
MIGR = ROOT / "database" / "saas" / "preparacion_pg17"
BASELINE = SQLDIR / "010_baseline_desde_catalogo.sql"
FIXTURES = SQLDIR / "030_lab_fixtures.sql"
VAULT_SUSTITUTO = SQLDIR / "000_lab_vault_substituto.sql"
SP_RESERVA = SQLDIR / "070_lab_sp_reserva_tenant.sql"
MIGRACIONES = (
    "010_saas_core_vanter.sql",
    "020_tenant_expand_backfill.sql",
    "030_tenant_integrity.sql",
    "040_routines_views_hardening.sql",
)
PROPUESTAS = {
    "050a": "050a_identidad_jwt_roles_PROPUESTA_NO_EJECUTAR.sql",
    "050b": "050b_rls_policies_PROPUESTA_NO_EJECUTAR.sql",
    "050c": "050c_acl_procedimientos_PROPUESTA_NO_EJECUTAR.sql",
}
PATRON_GUARDA = re.compile(
    r"DO \$\$ BEGIN\s+RAISE EXCEPTION 'BLOQUEADO[^']*';\s+END \$\$;\s*\n", re.S)

# Correcciones que el propio laboratorio demostró necesarias. Ya están
# incorporadas en la propuesta original, así que aquí sólo actúan como red de
# seguridad: si la línea vigente está presente no se toca nada (no se duplica),
# y si alguien revierte el original a la versión obsoleta el generador la
# restaura dejando la nota del motivo.
CORRECCIONES_5CB: dict[str, list[dict[str, str]]] = {
    "050a": [{
        "obsoleta":
            "GRANT SELECT(id) ON public.empresa TO fashionstore_bootstrap_owner;",
        "vigente":
            "GRANT SELECT(id,slug,estado) ON public.empresa "
            "TO fashionstore_bootstrap_owner;",
        "nota":
            "-- CORRECCIÓN 5CB (bloqueante): bootstrap_empresa_id() filtra por slug\n"
            "-- y estado, así que el privilegio por columnas debe cubrirlos. Con\n"
            "-- sólo SELECT(id) la función SECURITY DEFINER aborta con 42501.\n",
    }],
}

# Logins de servicio propios de 5CB (050c deja su creación fuera a propósito).
LOGINS = {
    "api": "fashionstore_5cb_api",
    "bootstrap": "fashionstore_5cb_bootstrap",
    "platform": "fashionstore_5cb_platform",
}
ROLES_GRUPO = {
    "api": "fashionstore_api",
    "bootstrap": "fashionstore_bootstrap",
    "platform": "fashionstore_platform_admin",
}


def generar_copias_lab() -> list[dict[str, str]]:
    """Copia 050a-c retirando la guarda de bloqueo sin duplicar correcciones.

    Las correcciones de ``CORRECCIONES_5CB`` ya viven en la propuesta original;
    si la línea vigente aparece no se aplica nada. Sólo se parchea cuando el
    original vuelve a la versión obsoleta, y entonces la copia lo declara.
    """
    registro = []
    for clave, nombre in PROPUESTAS.items():
        origen = MIGR / nombre
        texto = origen.read_text(encoding="utf-8")
        nuevo, retiradas = PATRON_GUARDA.subn("", texto, count=1)
        if retiradas != 1:
            raise LabError(f"No se pudo retirar exactamente una guarda de {nombre}")
        heredadas = aplicadas = 0
        for correccion in CORRECCIONES_5CB.get(clave, []):
            vigente = correccion["vigente"]
            if vigente in nuevo:
                heredadas += 1          # ya está en el original: no duplicar
                continue
            if nuevo.count(correccion["obsoleta"]) != 1:
                raise LabError(
                    f"La corrección 5CB de {clave} no encaja: {vigente[:60]}…")
            nuevo = nuevo.replace(
                correccion["obsoleta"], correccion["nota"] + vigente, 1)
            aplicadas += 1
        destino = SQLDIR / f"{clave}_lab.sql"
        huella = hashlib.sha256(texto.encode()).hexdigest()
        cabecera = (
            f"-- COPIA DE LABORATORIO 5CB de {nombre}\n"
            "-- Cambio 1: se retiró la guarda 'BLOQUEADO' de la primera línea\n"
            "-- para poder ensayarla en un clúster desechable.\n"
        )
        if aplicadas:
            cabecera += (
                f"-- Cambio 2: {aplicadas} corrección(es) 5CB reaplicada(s) porque\n"
                "-- el original había vuelto a la versión obsoleta; van marcadas\n"
                "-- en el cuerpo como 'CORRECCIÓN 5CB' con su motivo.\n")
        else:
            cabecera += "-- No se alteró nada más.\n"
        if heredadas:
            cabecera += (
                f"-- Las {heredadas} corrección(es) 5CB de este archivo ya están\n"
                "-- incorporadas en la propuesta original: no se duplican aquí.\n")
        cabecera += f"-- sha256 del original: {huella}\n"
        destino.write_text(cabecera + nuevo, encoding="utf-8")
        registro.append({"propuesta": clave, "origen": nombre,
                         "copia": destino.name, "sha256_original": huella,
                         "correcciones_heredadas": heredadas,
                         "correcciones_reaplicadas": aplicadas})
    return registro


def _psql(cluster, ruta: Path) -> None:
    paso(f"aplicando {Path(ruta).name}")
    cluster.archivo(Path(ruta))


def contar_trigger_categoria(cluster) -> int:
    return int(cluster.escalar(
        "SELECT count(*) FROM pg_catalog.pg_trigger t "
        "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname='categoria' "
        "AND t.tgname='trg_bitacora_categoria' AND NOT t.tgisinternal"))


def verificar_idempotencia_categoria(cluster) -> bool:
    """Ejecuta DOS veces el bloque DO de la 040 entregada y cuenta triggers.

    No usa una copia reescrita: extrae el bloque literal del archivo que se va a
    ejecutar en Supabase, de modo que la prueba cubre exactamente ese SQL.
    """
    texto = (MIGR / "040_routines_views_hardening.sql").read_text(encoding="utf-8")
    inicio = texto.find("DO $bitacora_categoria$")
    fin = texto.find("END $bitacora_categoria$;", inicio)
    if inicio < 0 or fin < 0:
        raise LabError("No se encontró el bloque DO de trg_bitacora_categoria en 040")
    bloque = texto[inicio:fin + len("END $bitacora_categoria$;")]
    temporal = SQLDIR / "_idempotencia_categoria.sql"
    temporal.write_text(bloque + "\n", encoding="utf-8")
    try:
        for _ in range(2):
            cluster.archivo(temporal)
        return contar_trigger_categoria(cluster) == 1
    finally:
        temporal.unlink(missing_ok=True)



def instalar(cluster, secreto_jwt: str) -> dict[str, object]:
    """Aplica el esquema completo y devuelve un resumen verificable."""
    if not BASELINE.is_file():
        raise LabError(
            "Falta sql/010_baseline_desde_catalogo.sql (copiar del laboratorio 5CA)")
    _psql(cluster, BASELINE)
    # Estado real heredado: categoria NO tenía trigger de auditoría.
    antes = contar_trigger_categoria(cluster)
    for nombre in MIGRACIONES:
        _psql(cluster, MIGR / nombre)
    despues = contar_trigger_categoria(cluster)
    if antes != 0 or despues != 1:
        raise LabError(
            f"trg_bitacora_categoria inesperado: antes={antes} despues={despues}")
    idempotente = verificar_idempotencia_categoria(cluster)
    _psql(cluster, FIXTURES)
    _psql(cluster, VAULT_SUSTITUTO)
    # 050a exige que la clave ya exista en Vault: se carga ANTES de la propuesta.
    paso("cargando secreto JWT en el sustituto local de Vault")
    vault = sembrar_vault(cluster, secreto_jwt)
    copias = generar_copias_lab()
    for clave in ("050a", "050b", "050c"):
        _psql(cluster, SQLDIR / f"{clave}_lab.sql")
    _psql(cluster, SP_RESERVA)
    paso("instalación SQL completa (sin 060_lab_correcciones.sql)")
    return {"copias_050": copias, "trigger_categoria_antes": antes,
            "trigger_categoria_despues": despues,
            "trigger_categoria_idempotente": idempotente, **vault}


def sembrar_vault(cluster, secreto_jwt: str) -> dict[str, object]:
    """Inserta el secreto HS256 sólo en el sustituto local de Vault."""
    dsn = cluster.dsn("postgres", cluster.passwd)
    with psycopg.connect(dsn, connect_timeout=10) as conexion:
        with conexion.cursor() as cur:
            cur.execute("DELETE FROM vault.secrets "
                        "WHERE name LIKE 'fashionstore_tenant_jwt_hs256%'")
            cur.execute(
                "INSERT INTO vault.secrets(name, secret, description) "
                "VALUES (%s,%s,%s)",
                ("fashionstore_tenant_jwt_hs256_active", secreto_jwt,
                 "Laboratorio 5CB: sustituto local, no es Supabase Vault"))
        conexion.commit()
        with conexion.cursor() as cur:
            cur.execute("SELECT count(*) FROM vault.decrypted_secrets")
            total = cur.fetchone()[0]
    return {"secretos_en_vault_local": total}


def provisionar_logins(cluster) -> dict[str, dict[str, str]]:
    """Crea los tres logins de servicio con contraseñas aleatorias en memoria.

    050c deja esta operación fuera de la migración a propósito. Los logins
    heredan (INHERIT) el rol de grupo para que RLS y ACL apliquen sin SET ROLE.
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
    paso("logins de servicio creados: "
         + ", ".join(v["rol"] for v in salida.values()))
    return salida


def fijar_hashes_argon2(cluster, password_plano: str) -> list[str]:
    """Reemplaza el hash marcador por Argon2 real (igual que usa FastAPI)."""
    from argon2 import PasswordHasher

    hash_real = PasswordHasher().hash(password_plano)
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
       JOIN pg_class c ON c.oid=co.conrelid
       JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND co.contype='f' AND NOT co.convalidated),
 'indices',(SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
       JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND i.indisvalid),
 'triggers',(SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
       JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND NOT t.tgisinternal),
 'tablas_rls',(SELECT count(*) FROM pg_class c
       JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND c.relkind='r' AND c.relrowsecurity),
 'tablas_force_rls',(SELECT count(*) FROM pg_class c
       JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity),
 'politicas',(SELECT count(*) FROM pg_policies WHERE schemaname='public'),
 'funciones_privadas',(SELECT count(*) FROM pg_proc p
       JOIN pg_namespace n ON n.oid=p.pronamespace
       WHERE n.nspname='fashionstore_private'),
 'empresas',(SELECT count(*) FROM public.empresa),
 'planes',(SELECT count(*) FROM public.plan_saas),
 'membresias',(SELECT count(*) FROM public.usuario_empresa)
)::text
"""


def resumen_estructural(cluster) -> dict[str, object]:
    return json.loads(cluster.escalar(CONSULTA_RESUMEN))
