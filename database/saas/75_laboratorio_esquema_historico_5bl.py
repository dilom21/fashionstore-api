"""5BL / Etapa D - Laboratorio DESECHABLE con el esquema historico reconstruido.

Objetivo
--------
Construir un cluster PostgreSQL 18 exclusivo de esta etapa (loopback, puerto
55442) que contenga:

* el esquema heredado del primer parcial reconstruido (tablas del mapeo ORM,
  restricciones historicas, 14 triggers documentados, 3 procedimientos CU16/CU18
  con sus cuerpos auditados y la vista ``vw_inventario_stock``);
* las cuatro migraciones portables 20261030000100..00400 de las Etapas B/C;
* la migracion nueva 20261030000500 que endurece triggers y define el contrato
  de auditoria;
* dos empresas ficticias A/B (mas C suspendida y D inactiva) con el principal
  SQL restringido ``fs_saas_runtime`` y el propietario NOLOGIN ``saas_rls_owner``.

Reglas de seguridad
-------------------
* Solo loopback y solo el puerto 55442 (no 5432/55432/55433/55440/55441).
* El nombre de base debe contener ``lab`` y el ``pgdata`` vivir bajo el
  directorio temporal del usuario.
* Exige ``--confirm-desechable`` y verifica la identidad efectiva del cluster
  (``data_directory`` y puerto) antes de cualquier DDL.
* Nunca imprime contrasenas, DSN completos ni la clave HMAC.
* No toca ``.env``, Supabase, Render, Vercel ni bases historicas.

Uso
---
    python database/saas/75_laboratorio_esquema_historico_5bl.py --init --confirm-desechable
    python database/saas/75_laboratorio_esquema_historico_5bl.py --recrear-base --confirm-desechable
    python database/saas/75_laboratorio_esquema_historico_5bl.py --status
    python database/saas/75_laboratorio_esquema_historico_5bl.py --stop
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
PUERTO_LAB = 55442
BASE_LAB = "fashionstore_lab_5bl"
HOST_LAB = "127.0.0.1"
PG_BIN = Path(r"C:\Program Files\PostgreSQL\18\bin")

# Evita que la importacion del ORM lea credenciales reales del .env.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://staging@127.0.0.1/staging")
os.environ.setdefault("SECRET_KEY", "lab-5bl-placeholder-no-usar")

MIGRACIONES = (
    "20261030000100_saas_core_portable.sql",
    "20261030000200_saas_empresa_id_portable.sql",
    "20261030000300_saas_integridad_tenant_portable.sql",
    "20261030000400_saas_rls_principal_restringido_portable.sql",
    "20261030000500_saas_compatibilidad_5bl.sql",
)
ESQUEMA_HISTORICO = "database/saas/75_esquema_historico_reconstruido_5bl.sql"

ROL_RUNTIME = "fs_saas_runtime"
ROL_OWNER = "saas_rls_owner"
SLUG_HISTORICO = "empresa-a"

# Fidelidad declarada: se completa con lo realmente instalado.
FIDELIDAD_ESPERADA = {
    "tablas_historicas_declaradas": 41,
    "tablas_historicas_reconstruidas": 40,
    "triggers_declarados": 27,
    "triggers_reconstruidos": 14,
    "rutinas_declaradas": 24,
    "rutinas_reproducidas": 13,
    "vistas_declaradas": 1,
    "vistas_reproducidas": 1,
    "origen": (
        "Auditorias locales de solo lectura 5F (cuerpos de los 3 SP), 5K/5AD "
        "(cuerpos de funciones de trigger y catalogo de disparadores) y mapeo "
        "ORM del repositorio. NO existe respaldo ni pg_dump del esquema real "
        "accesible: la fidelidad es PARCIAL."
    ),
}


class LabError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Rutas y utilidades
# ---------------------------------------------------------------------------


def raiz_lab() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Temp" / "opencode" / "fs_lab_5bl"


def ruta_pgdata() -> Path:
    return raiz_lab() / "pgdata"


def ruta_manifiesto() -> Path:
    return raiz_lab() / "lab_5bl_manifest.json"


def _bin(nombre: str) -> Path:
    exe = PG_BIN / f"{nombre}.exe"
    if not exe.exists():
        raise LabError(f"No se encontro {exe}")
    return exe


def _ejecutar(args, *, env=None, check=True, capturar=True):
    salida = subprocess.PIPE if capturar else subprocess.DEVNULL
    resultado = subprocess.run(
        args,
        stdout=salida,
        stderr=salida,
        text=capturar,
        encoding="utf-8" if capturar else None,
        errors="replace" if capturar else None,
        env={**os.environ, **(env or {})},
    )
    if check and resultado.returncode != 0:
        raise LabError(
            f"Fallo {' '.join(Path(a).name if i == 0 else a for i, a in enumerate(args))}: "
            f"rc={resultado.returncode}"
        )
    return resultado


def _validar_destino(host: str, puerto: int, base: str, pgdata: Path) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise LabError(f"Host no loopback rechazado: {host}")
    if puerto != PUERTO_LAB:
        raise LabError(
            f"Puerto {puerto} no autorizado; este laboratorio usa exclusivamente {PUERTO_LAB}"
        )
    if "lab" not in base.lower():
        raise LabError("El nombre de base debe contener 'lab'")
    temp = Path(os.environ.get("TEMP", ".")).resolve()
    local = Path(os.environ.get("LOCALAPPDATA", str(temp))).resolve()
    resuelto = pgdata.resolve()
    if not (str(resuelto).lower().startswith(str(temp).lower())
            or str(resuelto).lower().startswith(str(local).lower())):
        raise LabError("pgdata debe estar bajo el directorio temporal del usuario")


def _dsn_admin(password: str, base: str = BASE_LAB) -> str:
    return f"postgresql://postgres:{password}@{HOST_LAB}:{PUERTO_LAB}/{base}"


def _dsn_runtime(password: str, base: str = BASE_LAB) -> str:
    return f"postgresql://{ROL_RUNTIME}:{password}@{HOST_LAB}:{PUERTO_LAB}/{base}"


def _leer_migracion(nombre: str) -> str:
    return (ROOT / "supabase" / "migrations" / nombre).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------


def iniciar_cluster(pgdata: Path, password_admin: str) -> None:
    if pgdata.exists() and any(pgdata.iterdir()):
        raise LabError(f"{pgdata} ya contiene datos: use --recrear-base")
    pgdata.mkdir(parents=True, exist_ok=True)
    pwfile = raiz_lab() / ".initdb_pw"
    pwfile.write_text(password_admin, encoding="utf-8")
    try:
        _ejecutar([
            str(_bin("initdb")),
            "-D", str(pgdata),
            "-U", "postgres",
            f"--pwfile={pwfile}",
            "--auth-local=trust",
            "--auth-host=scram-sha-256",
            "--encoding=UTF8",
        ])
    finally:
        pwfile.unlink(missing_ok=True)
    arrancar_cluster(pgdata)


def arrancar_cluster(pgdata: Path) -> None:
    log = raiz_lab() / "postgres.log"
    _ejecutar([
        str(_bin("pg_ctl")),
        "-D", str(pgdata),
        "-l", str(log),
        "-o", f"-p {PUERTO_LAB} -c listen_addresses={HOST_LAB}",
        "-w",
        "start",
    ], capturar=False)


def detener_cluster(pgdata: Path) -> None:
    if not pgdata.exists():
        return
    _ejecutar([str(_bin("pg_ctl")), "-D", str(pgdata), "-w", "-m", "fast", "stop"],
              check=False, capturar=False)


def estado_cluster(pgdata: Path) -> str:
    if not pgdata.exists():
        return "ausente"
    resultado = _ejecutar([str(_bin("pg_ctl")), "-D", str(pgdata), "status"], check=False)
    return "activo" if resultado.returncode == 0 else "detenido"


def _crear_base_si_falta(password_admin: str) -> None:
    import psycopg

    with psycopg.connect(_dsn_admin(password_admin, "postgres"), autocommit=True) as conn:
        existe = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (BASE_LAB,)
        ).fetchone()
        if existe is None:
            conn.execute(f'CREATE DATABASE "{BASE_LAB}"')


def recrear_base(password_admin: str) -> None:
    """Solo laboratorio: elimina y recrea la base desechable de este cluster."""
    if BASE_LAB != "fashionstore_lab_5bl" or PUERTO_LAB != 55442:
        raise LabError("Guardas de recreacion alteradas: abortar")
    _ejecutar(
        [str(_bin("dropdb")), "--force", "-h", HOST_LAB, "-p", str(PUERTO_LAB),
         "-U", "postgres", BASE_LAB],
        env={"PGPASSWORD": password_admin},
    )
    _crear_base_si_falta(password_admin)


def preflight_admin(dsn: str) -> dict:
    """Verifica identidad del cluster y de la base antes de cualquier DDL."""
    parsed = urlparse(dsn)
    _validar_destino(parsed.hostname or "", parsed.port or 0,
                     (parsed.path or "").lstrip("/"), ruta_pgdata())
    import psycopg

    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            """
            SELECT current_database(), current_user, version(),
                   pg_is_in_recovery(), current_setting('data_directory'),
                   current_setting('port')
            """
        ).fetchone()
    base, usuario, version, recovery, data_dir, puerto = row
    if recovery:
        raise LabError("El cluster esta en recuperacion: no es el laboratorio")
    if str(Path(data_dir).resolve()).lower() != str(ruta_pgdata().resolve()).lower():
        raise LabError("El data_directory no coincide con el pgdata del laboratorio")
    if str(puerto) != str(PUERTO_LAB):
        raise LabError("El puerto efectivo no es el del laboratorio")
    if usuario != "postgres" or base != BASE_LAB:
        raise LabError("Base o usuario administrador inesperados")
    return {
        "base": base,
        "usuario": usuario,
        "version": version.split(" on ")[0],
        "recovery": recovery,
        "puerto": int(puerto),
    }


# ---------------------------------------------------------------------------
# Modulos reutilizados
# ---------------------------------------------------------------------------


_MODULOS_CARGADOS: dict[str, object] = {}


def _cargar(relativa: str, alias: str):
    if relativa in _MODULOS_CARGADOS:
        return _MODULOS_CARGADOS[relativa]
    ruta = ROOT / relativa
    spec = importlib.util.spec_from_file_location(alias, ruta)
    if spec is None or spec.loader is None:
        raise LabError(f"No se pudo cargar {relativa}")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    _MODULOS_CARGADOS[relativa] = modulo
    return modulo


# ---------------------------------------------------------------------------
# Construccion del laboratorio
# ---------------------------------------------------------------------------


def construir_tablas_historicas(admin_dsn: str) -> None:
    """Tablas historicas desde el ORM + restricciones globales del primer parcial."""
    import psycopg
    from sqlalchemy import create_engine

    etapa_b = _cargar("database/saas/70_staging_saas_desde_cero_5bj.py", "staging_5bj")
    engine = create_engine(
        admin_dsn.replace("postgresql://", "postgresql+psycopg://"), future=True
    )
    try:
        etapa_b._esquema_heredado(engine)
    finally:
        engine.dispose()

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("SET search_path = public")
        etapa_b._reducir_a_historico(conn)


def aplicar_esquema_historico(admin_dsn: str) -> None:
    """Instala triggers, SP y vista historicos antes de cualquier migracion SaaS."""
    import psycopg

    sql = (ROOT / ESQUEMA_HISTORICO).read_text(encoding="utf-8")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("SET saas.lab_5bl = 'true'")
        conn.execute(sql)


def sembrar_historico(admin_dsn: str) -> dict[str, int]:
    """Datos ficticios del primer parcial.

    La carga se hace con ``session_replication_role = replica``, igual que una
    restauracion con pg_restore: los triggers no se disparan durante la carga
    historica. El comportamiento de los triggers se ejercita despues, sobre el
    esquema ya migrado, que es lo que esta etapa debe certificar.
    """
    import psycopg

    etapa_b = _cargar("database/saas/70_staging_saas_desde_cero_5bj.py", "staging_5bj")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("SET search_path = public")
        conn.execute("SET session_replication_role = replica")
        try:
            conteos = etapa_b._sembrar(conn)
        finally:
            conn.execute("SET session_replication_role = origin")
        return conteos


def ajustar_secuencias(admin_dsn: str) -> int:
    """Adelanta cada secuencia al maximo id sembrado (los seeds usan IDs fijos)."""
    import psycopg

    ajustadas = 0
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        tablas = [fila[0] for fila in conn.execute(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"
        ).fetchall()]
        for tabla in tablas:
            if conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s AND column_name='id'",
                (tabla,),
            ).fetchone() is None:
                continue
            secuencia = conn.execute(
                "SELECT pg_get_serial_sequence(%s, 'id')", (f"public.{tabla}",)
            ).fetchone()[0]
            if not secuencia:
                continue
            maximo = conn.execute(
                f'SELECT COALESCE(MAX(id), 0) FROM public."{tabla}"'
            ).fetchone()[0]
            conn.execute("SELECT setval(%s, %s, %s)",
                         (secuencia, max(maximo, 1), maximo > 0))
            ajustadas += 1
    return ajustadas


def aplicar_nucleo_saas(admin_dsn: str) -> dict:
    """00100 + tenant historico + 00200 + 00300, en una sola sesion."""
    import psycopg

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("SET search_path = public")
        conn.execute(_leer_migracion(MIGRACIONES[0]))
        conn.execute(
            "INSERT INTO public.empresa (razon_social, nombre_comercial, slug, estado) "
            "VALUES ('Empresa Historica Lab','Empresa Historica Lab',%s,TRUE) "
            "ON CONFLICT (slug) DO NOTHING",
            (SLUG_HISTORICO,),
        )
        empresa_id = conn.execute(
            "SELECT id FROM public.empresa WHERE slug=%s", (SLUG_HISTORICO,)
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
                "(empresa_id, plan_saas_id, fecha_inicio, fecha_vencimiento, estado, observacion) "
                "SELECT %s, id, CURRENT_DATE, CURRENT_DATE + 365, 'ACTIVA', "
                "'Laboratorio 5BL' FROM public.plan_saas WHERE codigo='PROFESIONAL'",
                (empresa_id,),
            )
        conn.execute(f"SET saas.bootstrap_empresa_slug = '{SLUG_HISTORICO}'")
        conn.execute("SET saas.bootstrap_crear_membresias = 'true'")
        conn.execute(_leer_migracion(MIGRACIONES[1]))
        conn.execute(_leer_migracion(MIGRACIONES[2]))
    return {"empresa_historica_id": empresa_id}


def crear_roles(admin_dsn: str, password_runtime: str) -> None:
    """Roles del cluster: runtime con login, propietario de funciones NOLOGIN."""
    import psycopg
    from psycopg import sql

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        for rol in (ROL_OWNER, ROL_RUNTIME):
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (rol,)).fetchone() is None:
                conn.execute(
                    sql.SQL(
                        "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS "
                        "NOCREATEDB NOCREATEROLE NOINHERIT"
                    ).format(sql.Identifier(rol))
                )
        conn.execute(
            sql.SQL(
                "ALTER ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB "
                "NOCREATEROLE NOINHERIT PASSWORD {}"
            ).format(sql.Identifier(ROL_RUNTIME), sql.Literal(password_runtime))
        )
        conn.execute(
            sql.SQL(
                "ALTER ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB "
                "NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(ROL_OWNER))
        )
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(ROL_OWNER))
        )


def aplicar_rls(admin_dsn: str, clave_hmac: str) -> None:
    import psycopg

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f"SET saas.runtime_role = '{ROL_RUNTIME}'")
        conn.execute(f"SET saas.rls_owner_role = '{ROL_OWNER}'")
        conn.execute(_leer_migracion(MIGRACIONES[3]))
        conn.execute(
            "INSERT INTO saas.rls_secret (singleton, clave) VALUES (TRUE, %s) "
            "ON CONFLICT (singleton) DO UPDATE SET clave = EXCLUDED.clave, "
            "actualizado = NOW()",
            (clave_hmac,),
        )


def simular_deriva_auditoria(admin_dsn: str, runtime_dsn: str, clave: str,
                             empresa_id: int, usuario_id: int) -> int:
    """Reproduce la deriva real de auditoria ANTES de la migracion 00500.

    Con el cuerpo historico de `fn_registrar_bitacora` (anterior a empresa_id):
      * una accion de un usuario del tenant A deja una fila con empresa_id NULL;
      * una accion administrativa sobre `public.usuario` (tabla global) hace lo
        mismo.
    Son exactamente las filas historicas que la Etapa D debe clasificar y
    conservar sin reasignar ni borrar.
    """
    import hashlib
    import hmac
    import time

    import psycopg

    nonce = secrets.token_hex(8)
    expira = int(time.time()) + 120
    mensaje = f"{empresa_id}|{usuario_id}|{expira}|{nonce}"
    firma = hmac.new(clave.encode("utf-8"), mensaje.encode("utf-8"),
                     hashlib.sha256).hexdigest()
    asercion = f"{mensaje}|{firma}"

    with psycopg.connect(runtime_dsn) as conn:
        conn.execute("SELECT set_config('app.tenant_assertion', %s, true)", (asercion,))
        conn.execute("SELECT set_config('app.usuario_id', %s, true)", (str(usuario_id),))
        conn.execute("UPDATE public.cliente SET telefono = telefono WHERE id = 1")
        conn.commit()

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("UPDATE public.usuario SET estado = estado WHERE id = 2")
        return conn.execute(
            "SELECT count(*) FROM public.bitacora WHERE empresa_id IS NULL"
        ).fetchone()[0]


def aplicar_compatibilidad(admin_dsn: str) -> None:
    import psycopg

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f"SET saas.runtime_role = '{ROL_RUNTIME}'")
        conn.execute(f"SET saas.rls_owner_role = '{ROL_OWNER}'")
        conn.execute(_leer_migracion(MIGRACIONES[4]))


def sembrar_empresas(admin_dsn: str) -> dict:
    lab_5bk = _cargar(
        "database/saas/74_laboratorio_rls_principal_5bk.py", "lab_5bk_reuso"
    )
    return lab_5bk.sembrar_empresas(admin_dsn)


def verificar_compatibilidad(admin_dsn: str, runtime_dsn: str) -> dict:
    import psycopg

    with psycopg.connect(admin_dsn) as conn:
        row = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM public.bitacora),
              (SELECT count(*) FROM public.bitacora WHERE empresa_id IS NULL),
              (SELECT count(*) FROM saas.bitacora_global),
              (SELECT count(*) FROM saas.compatibilidad_5bl),
              (SELECT count(*) FROM saas.compatibilidad_5bl WHERE security_definer),
              (SELECT count(*) FROM saas.compatibilidad_5bl
                WHERE configuracion = '<sin search_path>'
                   OR configuracion NOT LIKE '%search_path=%'),
              (SELECT count(*) FROM saas.validar_triggers_5bl()),
              (SELECT count(*) FROM pg_policies
                WHERE schemaname='public' AND tablename='bitacora'),
              (SELECT count(*) FROM public.usuario_empresa),
              (SELECT count(*) FROM pg_constraint
                WHERE contype='f' AND conname LIKE 'fk_tenant_%')
            """
        ).fetchone()
        funciones = conn.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.prokind='f'"
        ).fetchone()[0]
        procedimientos = conn.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.prokind='p'"
        ).fetchone()[0]

    with psycopg.connect(runtime_dsn) as conn:
        privilegios = conn.execute(
            """
            SELECT
              has_table_privilege(current_user, 'public.bitacora', 'SELECT'),
              has_table_privilege(current_user, 'public.bitacora', 'INSERT'),
              has_table_privilege(current_user, 'public.bitacora', 'UPDATE'),
              has_table_privilege(current_user, 'public.bitacora', 'DELETE'),
              has_function_privilege(current_user,
                'saas.sp_ajustar_inventario_tenant(bigint,integer,text,text)', 'EXECUTE'),
              has_function_privilege(current_user,
                'public.sp_crear_reserva_desde_carrito(bigint,timestamp with time zone,text,bigint,bigint)',
                'EXECUTE'),
              has_function_privilege(current_user,
                'saas.registrar_evento_global(text,text,bigint,text,bigint,text)', 'EXECUTE')
            """
        ).fetchone()

    return {
        "bitacora_filas": row[0],
        "bitacora_historicas_sin_tenant": row[1],
        "bitacora_global_eventos": row[2],
        "triggers_instalados": row[3],
        "triggers_security_definer": row[4],
        "triggers_sin_search_path": row[5],
        "violaciones_validacion": row[6],
        "politicas_bitacora": row[7],
        "membresias": row[8],
        "fk_tenant": row[9],
        "funciones_public": funciones,
        "procedimientos_public": procedimientos,
        "privilegios_runtime": {
            "bitacora_select": privilegios[0],
            "bitacora_insert": privilegios[1],
            "bitacora_update": privilegios[2],
            "bitacora_delete": privilegios[3],
            "sp_ajuste_stock": privilegios[4],
            "legacy_sp_reserva": privilegios[5],
            "evento_global": privilegios[6],
        },
    }


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------


def provisionar(password_admin: str, password_runtime: str) -> dict:
    admin_dsn = _dsn_admin(password_admin)
    runtime_dsn = _dsn_runtime(password_runtime)
    info = preflight_admin(admin_dsn)

    construir_tablas_historicas(admin_dsn)
    aplicar_esquema_historico(admin_dsn)
    filas_historicas = sembrar_historico(admin_dsn)
    ajustar_secuencias(admin_dsn)
    nucleo = aplicar_nucleo_saas(admin_dsn)
    crear_roles(admin_dsn, password_runtime)
    clave_hmac = secrets.token_urlsafe(48)
    aplicar_rls(admin_dsn, clave_hmac)
    nulos_historicos = simular_deriva_auditoria(
        admin_dsn, runtime_dsn, clave_hmac, nucleo["empresa_historica_id"], 1
    )
    aplicar_compatibilidad(admin_dsn)
    empresas = sembrar_empresas(admin_dsn)

    lab_5bk = _cargar(
        "database/saas/74_laboratorio_rls_principal_5bk.py", "lab_5bk_reuso"
    )
    principal = lab_5bk.verificar_principal(admin_dsn, runtime_dsn)
    compatibilidad = verificar_compatibilidad(admin_dsn, runtime_dsn)
    if compatibilidad["violaciones_validacion"] or compatibilidad["triggers_security_definer"]:
        raise LabError("La verificacion de compatibilidad no quedo limpia")

    manifiesto = {
        "fecha": date.today().isoformat(),
        "etapa": "5BL",
        "cluster": {
            "host": HOST_LAB,
            "puerto": PUERTO_LAB,
            "base": BASE_LAB,
            "pgdata": str(ruta_pgdata()),
            "version": info["version"],
        },
        "admin_dsn": admin_dsn,
        "runtime_dsn": runtime_dsn,
        "assertion_key": clave_hmac,
        "password_usuarios": lab_5bk.PASSWORD_USUARIOS,
        "roles": {"runtime": ROL_RUNTIME, "owner": ROL_OWNER},
        "empresas": empresas,
        "usuario_historico": {"id": 1, "correo": "admin@staging.invalid"},
        "usuarios": {
            "a_admin": 1,
            "a_cliente": 2,
            "b_admin": 3,
            "b_cliente": 4,
            "b_cajero": 5,
            "a_deshabilitado": 6,
            "a_revocado": 7,
            "c_suspendida": 8,
            "d_inactiva": 9,
        },
        "correos": {
            "a_admin": "admin@staging.invalid",
            "a_cliente": "cliente@staging.invalid",
            "b_admin": "admin.b@staging.invalid",
            "b_cliente": "cliente.b@staging.invalid",
            "b_cajero": "cajero.b@staging.invalid",
            "a_deshabilitado": "deshabilitado@staging.invalid",
            "a_revocado": "revocado@staging.invalid",
            "c_suspendida": "suspendida@staging.invalid",
            "d_inactiva": "inactiva@staging.invalid",
        },
        "principal": principal,
        "compatibilidad": compatibilidad,
        "esquema": {
            "nucleo": nucleo,
            "filas_historicas_iniciales": filas_historicas,
            "bitacora_null_historicas": nulos_historicos,
        },
        "fidelidad": FIDELIDAD_ESPERADA,
    }
    ruta_manifiesto().parent.mkdir(parents=True, exist_ok=True)
    ruta_manifiesto().write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return manifiesto


def escribir_evidencia() -> Path:
    """Evidencia sanitizada de triggers, bitacora y privilegios (sin secretos)."""
    import psycopg

    manifiesto = json.loads(ruta_manifiesto().read_text(encoding="utf-8"))
    with psycopg.connect(manifiesto["admin_dsn"]) as conn:
        triggers = conn.execute(
            "SELECT tabla, trigger, funcion, security_definer, configuracion "
            "FROM saas.compatibilidad_5bl ORDER BY tabla, trigger"
        ).fetchall()
        bitacora = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE empresa_id IS NULL) FROM public.bitacora"
        ).fetchone()
        globales = conn.execute("SELECT count(*) FROM saas.bitacora_global").fetchone()[0]
        clases = conn.execute(
            "SELECT clase, eventos FROM saas.bitacora_clasificacion ORDER BY clase"
        ).fetchall()
        politicas = conn.execute(
            "SELECT policyname, cmd FROM pg_policies "
            "WHERE schemaname='public' AND tablename='bitacora' ORDER BY policyname"
        ).fetchall()
        rutinas_publicas = conn.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a "
            "WHERE n.nspname='public' AND p.prokind IN ('f','p') AND a.grantee=0"
        ).fetchone()[0]
    with psycopg.connect(manifiesto["runtime_dsn"]) as conn:
        privilegios = conn.execute(
            "SELECT has_table_privilege(current_user,'public.bitacora','SELECT'), "
            "has_table_privilege(current_user,'public.bitacora','INSERT'), "
            "has_table_privilege(current_user,'public.bitacora','UPDATE'), "
            "has_table_privilege(current_user,'public.bitacora','DELETE')"
        ).fetchone()

    lineas = [
        "EVIDENCIA TRIGGERS / BITACORA - ETAPA D (5BL) SOBRE ESQUEMA HISTORICO",
        f"Fecha UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "Cluster: " + manifiesto["cluster"]["version"] + " en "
        + manifiesto["cluster"]["host"] + ":" + str(manifiesto["cluster"]["puerto"])
        + " / " + manifiesto["cluster"]["base"],
        "Principal: " + manifiesto["principal"]["session_user"]
        + f" (rolsuper={manifiesto['principal']['rolsuper']}, "
          f"rolbypassrls={manifiesto['principal']['rolbypassrls']}, "
          f"tablas_propias={manifiesto['principal']['tablas_propias']})",
        "Sin secretos: no se incluyen DSN, JWT, contrasenas ni datos personales.",
        "-" * 78,
        f"Triggers en public: {len(triggers)}",
    ]
    for tabla, trigger, funcion, definer, config in triggers:
        lineas.append(
            f"  {tabla}.{trigger} -> {funcion} | definer={definer} | {config}"
        )
    lineas += [
        f"Rutinas public con EXECUTE a PUBLIC: {rutinas_publicas}",
        f"Bitacora empresarial: filas={bitacora[0]} sin_tenant={bitacora[1]}",
        f"Canal global (saas.bitacora_global): eventos={globales}",
        "Clasificacion: " + ", ".join(f"{clase}={eventos}" for clase, eventos in clases),
        "Politicas de bitacora: " + ", ".join(f"{p}={c}" for p, c in politicas),
        "Privilegios del runtime sobre bitacora (S,I,U,D): "
        + ",".join(str(bool(x)) for x in privilegios),
        "-" * 78,
        "Fidelidad del origen: " + manifiesto["fidelidad"]["origen"],
        f"Tablas historicas: {manifiesto['fidelidad']['tablas_historicas_reconstruidas']}"
        f"/{manifiesto['fidelidad']['tablas_historicas_declaradas']}; "
        f"triggers: {manifiesto['fidelidad']['triggers_reconstruidos']}"
        f"/{manifiesto['fidelidad']['triggers_declarados']}; "
        f"rutinas: {manifiesto['fidelidad']['rutinas_reproducidas']}"
        f"/{manifiesto['fidelidad']['rutinas_declaradas']}",
    ]
    evidencias = ROOT / "database" / "saas" / "evidencias"
    evidencias.mkdir(parents=True, exist_ok=True)
    marca = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ruta = evidencias / f"EVIDENCIA_TRIGGERS_BITACORA_5BL_{marca}.txt"
    base, n = ruta, 2
    while ruta.exists():
        ruta = base.with_name(f"EVIDENCIA_TRIGGERS_BITACORA_5BL_{marca}_v{n:02d}.txt")
        n += 1
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def _resumen(manifiesto: dict) -> dict:
    """Vista sin secretos para consola."""
    return {
        "cluster": manifiesto["cluster"],
        "principal": manifiesto["principal"],
        "compatibilidad": manifiesto["compatibilidad"],
        "fidelidad": manifiesto["fidelidad"],
        "empresas": {k: v for k, v in manifiesto["empresas"].items()
                     if k != "empresas_por_id"},
        "manifiesto": str(ruta_manifiesto()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-desechable", action="store_true")
    parser.add_argument("--init", action="store_true", help="Crear cluster y base")
    parser.add_argument("--recrear-base", action="store_true",
                        help="Borrar y recrear SOLO la base desechable del laboratorio")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--evidencia", action="store_true",
                        help="Escribe la evidencia sanitizada del laboratorio actual")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)

    pgdata = ruta_pgdata()

    if args.status:
        print(json.dumps({
            "pgdata": str(pgdata),
            "estado": estado_cluster(pgdata),
            "manifiesto": ruta_manifiesto().exists(),
        }, indent=2))
        return 0

    if args.evidencia:
        if not ruta_manifiesto().exists():
            print("No hay manifiesto del laboratorio 5BL; use --init", file=sys.stderr)
            return 2
        print(str(escribir_evidencia()))
        return 0

    if args.stop:
        detener_cluster(pgdata)
        print(json.dumps({"cluster": "detenido", "pgdata": str(pgdata)}, indent=2))
        return 0

    if not args.confirm_desechable:
        print("Falta --confirm-desechable: este script crea un cluster completo", file=sys.stderr)
        return 2

    _validar_destino(HOST_LAB, PUERTO_LAB, BASE_LAB, pgdata)
    raiz_lab().mkdir(parents=True, exist_ok=True)
    print("[lab-5bl] rutas verificadas", file=sys.stderr, flush=True)

    password_admin = ""
    archivo_admin = raiz_lab() / ".admin_pw"
    if archivo_admin.exists():
        password_admin = archivo_admin.read_text(encoding="utf-8").strip()
    if not password_admin:
        password_admin = secrets.token_urlsafe(24)
        archivo_admin.write_text(password_admin, encoding="utf-8")
    password_runtime = secrets.token_urlsafe(24)

    if args.init:
        iniciar_cluster(pgdata, password_admin)
        _crear_base_si_falta(password_admin)
    else:
        if not archivo_admin.exists():
            print("No hay credencial admin local; use --init", file=sys.stderr)
            return 2
        if estado_cluster(pgdata) != "activo":
            arrancar_cluster(pgdata)
        if args.recrear_base:
            recrear_base(password_admin)

    manifiesto = provisionar(password_admin, password_runtime)
    print(json.dumps(_resumen(manifiesto), indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
