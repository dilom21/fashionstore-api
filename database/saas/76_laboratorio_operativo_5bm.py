"""5BM / Etapa E1 - Laboratorio DESECHABLE del circuito SaaS operativo.

Construye un cluster PostgreSQL 18 exclusivo en loopback (puerto 55443, base
``fashionstore_lab_5bm``) con:

* el esquema historico reconstruido de 5BL (tablas ORM + 14 triggers + 3 SP
  legacy revocados);
* las migraciones 20261030000100..00500 (RLS, asercion HMAC, bitacora);
* las cuatro empresas ficticias de 5BK para regresion;
* **el plano global nuevo de 5BM**: roles ``saas_admin_runtime`` (LOGIN sin
  privilegios de tabla), ``saas_admin_owner`` y ``saas_actor_sql_owner``
  (NOLOGIN), funciones ``saas_admin.*``, SP tenant de reserva/venta y la tabla
  de idempotencia (migracion 20261030000600).

Reglas de seguridad
-------------------
* Solo loopback y solo el puerto 55443 (no 5432/55432/55433/55440..55442).
* El nombre de base debe contener ``lab`` y el pgdata vive bajo el directorio
  temporal del usuario.
* Exige ``--confirm-desechable`` y verifica ``data_directory``/puerto antes de
  cualquier DDL.
* Nunca imprime contrasenas, DSN completos ni la clave HMAC.
* No toca .env, Supabase, Render, Vercel ni bases historicas.

Uso
---
    python database/saas/76_laboratorio_operativo_5bm.py --init --confirm-desechable
    python database/saas/76_laboratorio_operativo_5bm.py --recrear-base --confirm-desechable
    python database/saas/76_laboratorio_operativo_5bm.py --status
    python database/saas/76_laboratorio_operativo_5bm.py --evidencia
    python database/saas/76_laboratorio_operativo_5bm.py --stop
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
PUERTO_LAB = 55443
BASE_LAB = "fashionstore_lab_5bm"
HOST_LAB = "127.0.0.1"
PG_BIN = Path(r"C:\Program Files\PostgreSQL\18\bin")
MIGRACION_OPERATIVA = "20261030000600_saas_operativo_5bm.sql"

ROL_RUNTIME = "fs_saas_runtime"
ROL_RLS_OWNER = "saas_rls_owner"
ROL_ADMIN = "saas_admin_runtime"
ROL_ADMIN_OWNER = "saas_admin_owner"
ROL_ACTOR_OWNER = "saas_actor_sql_owner"

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://staging@127.0.0.1/staging")
os.environ.setdefault("SECRET_KEY", "lab-5bm-placeholder-no-usar")


class LabError(RuntimeError):
    pass


def raiz_lab() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Temp" / "opencode" / "fs_lab_5bm"


def ruta_pgdata() -> Path:
    return raiz_lab() / "pgdata"


def ruta_manifiesto() -> Path:
    return raiz_lab() / "lab_5bm_manifest.json"


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


def _dsn(rol: str, password: str, base: str = BASE_LAB) -> str:
    return f"postgresql://{rol}:{password}@{HOST_LAB}:{PUERTO_LAB}/{base}"


def _cargar(relativa: str, alias: str):
    ruta = ROOT / relativa
    spec = importlib.util.spec_from_file_location(alias, ruta)
    if spec is None or spec.loader is None:
        raise LabError(f"No se pudo cargar {relativa}")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[alias] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def modulo_5bl():
    """Carga el runner 5BL y lo reapunta a este cluster (sin editarlo)."""
    modulo = _cargar("database/saas/75_laboratorio_esquema_historico_5bl.py", "lab_5bl_5bm")
    modulo.PUERTO_LAB = PUERTO_LAB
    modulo.BASE_LAB = BASE_LAB
    modulo.HOST_LAB = HOST_LAB
    modulo.raiz_lab = raiz_lab
    modulo.ruta_pgdata = ruta_pgdata
    modulo.ruta_manifiesto = ruta_manifiesto
    return modulo


def modulo_5bk():
    modulo = _cargar("database/saas/74_laboratorio_rls_principal_5bk.py", "lab_5bk_5bm")
    modulo.PUERTO_LAB = PUERTO_LAB
    modulo.BASE_LAB = BASE_LAB
    modulo.HOST_LAB = HOST_LAB
    modulo.raiz_lab = raiz_lab
    modulo.ruta_pgdata = ruta_pgdata
    modulo.ruta_manifiesto = ruta_manifiesto
    return modulo


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
            str(_bin("initdb")), "-D", str(pgdata), "-U", "postgres",
            f"--pwfile={pwfile}", "--auth-local=trust",
            "--auth-host=scram-sha-256", "--encoding=UTF8",
        ])
    finally:
        pwfile.unlink(missing_ok=True)
    arrancar_cluster(pgdata)


def arrancar_cluster(pgdata: Path) -> None:
    log = raiz_lab() / "postgres.log"
    _ejecutar([
        str(_bin("pg_ctl")), "-D", str(pgdata), "-l", str(log),
        "-o", f"-p {PUERTO_LAB} -c listen_addresses={HOST_LAB}", "-w", "start",
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

    with psycopg.connect(_dsn("postgres", password_admin, "postgres"),
                         autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s",
                        (BASE_LAB,)).fetchone() is None:
            conn.execute(f'CREATE DATABASE "{BASE_LAB}"')


def recrear_base(password_admin: str) -> None:
    if BASE_LAB != "fashionstore_lab_5bm" or PUERTO_LAB != 55443:
        raise LabError("Guardas de recreacion alteradas: abortar")
    _ejecutar(
        [str(_bin("dropdb")), "--force", "-h", HOST_LAB, "-p", str(PUERTO_LAB),
         "-U", "postgres", BASE_LAB],
        env={"PGPASSWORD": password_admin},
    )
    _crear_base_si_falta(password_admin)


def preflight_admin(admin_dsn: str) -> dict:
    parsed = urlparse(admin_dsn)
    _validar_destino(parsed.hostname or "", parsed.port or 0,
                     (parsed.path or "").lstrip("/"), ruta_pgdata())
    import psycopg

    with psycopg.connect(admin_dsn) as conn:
        fila = conn.execute(
            """
            SELECT current_database(), current_user, version(),
                   pg_is_in_recovery(), current_setting('data_directory'),
                   current_setting('port')
            """
        ).fetchone()
    base, usuario, version, recovery, data_dir, puerto = fila
    if recovery:
        raise LabError("El cluster esta en recuperacion: no es el laboratorio")
    if str(Path(data_dir).resolve()).lower() != str(ruta_pgdata().resolve()).lower():
        raise LabError("El data_directory no coincide con el pgdata del laboratorio")
    if str(puerto) != str(PUERTO_LAB):
        raise LabError("El puerto efectivo no es el del laboratorio")
    if usuario != "postgres" or base != BASE_LAB:
        raise LabError("Base o usuario administrador inesperados")
    return {"base": base, "usuario": usuario, "version": version.split(" on ")[0],
            "recovery": recovery, "puerto": int(puerto)}


# ---------------------------------------------------------------------------
# Roles globales nuevos y migracion 00600
# ---------------------------------------------------------------------------


def crear_roles_globales(admin_dsn: str, password_admin_runtime: str) -> None:
    import psycopg
    from psycopg import sql

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        for rol in (ROL_ADMIN_OWNER, ROL_ACTOR_OWNER):
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (rol,)).fetchone() is None:
                conn.execute(sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS "
                    "NOCREATEDB NOCREATEROLE NOINHERIT"
                ).format(sql.Identifier(rol)))
            conn.execute(sql.SQL(
                "ALTER ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS "
                "NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(rol)))
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (ROL_ADMIN,)).fetchone() is None:
            conn.execute(sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS "
                "NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(ROL_ADMIN)))
        conn.execute(sql.SQL(
            "ALTER ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB "
            "NOCREATEROLE NOINHERIT PASSWORD {}"
        ).format(sql.Identifier(ROL_ADMIN), sql.Literal(password_admin_runtime)))


def aplicar_operativo(admin_dsn: str) -> None:
    import psycopg

    sql_migracion = (ROOT / "supabase" / "migrations" / MIGRACION_OPERATIVA).read_text(
        encoding="utf-8")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f"SET saas.runtime_role = '{ROL_RUNTIME}'")
        conn.execute(f"SET saas.rls_owner_role = '{ROL_RLS_OWNER}'")
        conn.execute(f"SET saas.admin_runtime_role = '{ROL_ADMIN}'")
        conn.execute(f"SET saas.admin_owner_role = '{ROL_ADMIN_OWNER}'")
        conn.execute(f"SET saas.actor_owner_role = '{ROL_ACTOR_OWNER}'")
        conn.execute(sql_migracion)


def verificar_operativo(admin_dsn: str, runtime_dsn: str,
                        admin_runtime_dsn: str) -> dict:
    import psycopg

    with psycopg.connect(admin_dsn) as conn:
        funciones = dict(conn.execute(
            """
            SELECT p.proname, p.prosecdef
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'saas_admin' ORDER BY p.proname
            """
        ).fetchall())
        sps = conn.execute(
            """
            SELECT p.proname, p.prokind, p.prosecdef,
                   pg_get_userbyid(p.proowner) AS propietario,
                   COALESCE(array_to_string(p.proconfig, ','), '<sin>') AS config
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'public'
               AND p.proname IN ('sp_crear_reserva_tenant', 'sp_cancelar_reserva_tenant',
                                 'sp_finalizar_reserva_sin_compra_tenant',
                                 'sp_registrar_venta_contado_tenant',
                                 'fs_verificar_actor_reserva')
             ORDER BY p.proname
            """
        ).fetchall()
        polizas_admin = conn.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname='public' "
            "AND policyname='admin_global_acceso'"
        ).fetchone()[0]
        idem = conn.execute(
            "SELECT count(*) FROM pg_constraint WHERE conname='uq_operacion_idempotente'"
        ).fetchone()[0]
        # Matriz de EXECUTE por OID: el principal tenant no puede ni resolver
        # nombres del esquema administrativo, asi que la consulta se hace aqui.
        privilegios = conn.execute(
            """
            SELECT r.rolname, p.proname, has_function_privilege(r.oid, p.oid, 'EXECUTE')
              FROM pg_roles r
              CROSS JOIN pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname IN ('saas_admin', 'public')
               AND p.proname IN ('listar_empresas', 'crear_empresa',
                                 'registrar_primer_administrador',
                                 'sp_crear_reserva_tenant',
                                 'sp_registrar_venta_contado_tenant',
                                 'fs_verificar_actor_reserva')
               AND r.rolname IN ('fs_saas_runtime', 'saas_admin_runtime')
             ORDER BY r.rolname, p.proname
            """
        ).fetchall()
        privilegios_tabla_admin = conn.execute(
            "SELECT count(*) FROM information_schema.table_privileges "
            "WHERE grantee = 'saas_admin_runtime'"
        ).fetchone()[0]

    matriz: dict[str, dict[str, bool]] = {}
    for rol, funcion, permitido in privilegios:
        matriz.setdefault(rol, {})[funcion] = bool(permitido)

    with psycopg.connect(runtime_dsn) as conn:
        tenant_runtime = conn.execute(
            """
            SELECT has_table_privilege(current_user, 'public.usuario', 'INSERT'),
                   has_table_privilege(current_user, 'saas.operacion_idempotente', 'INSERT')
            """
        ).fetchone()

    with psycopg.connect(admin_runtime_dsn) as conn:
        admin = conn.execute(
            """
            SELECT session_user, current_user, r.rolsuper, r.rolbypassrls,
                   r.rolcanlogin, r.rolinherit,
                   (SELECT count(*) FROM pg_auth_members m WHERE m.member = r.oid)
              FROM pg_roles r WHERE r.rolname = current_user
            """
        ).fetchone()

    return {
        "funciones_admin": funciones,
        "rutinas_comerciales": [
            {"nombre": n, "tipo": t, "definer": d, "propietario": o, "config": c}
            for n, t, d, o, c in sps
        ],
        "politicas_admin": polizas_admin,
        "idempotencia_unique": idem,
        "matriz_execute": matriz,
        "runtime_tenant": {
            "insert_usuario": tenant_runtime[0],
            "insert_idempotencia": tenant_runtime[1],
            "admin_listar_empresas": matriz.get("fs_saas_runtime", {}).get(
                "listar_empresas", False),
            "admin_crear_empresa": matriz.get("fs_saas_runtime", {}).get(
                "crear_empresa", False),
            "sp_crear_reserva": matriz.get("fs_saas_runtime", {}).get(
                "sp_crear_reserva_tenant", False),
            "sp_venta_contado": matriz.get("fs_saas_runtime", {}).get(
                "sp_registrar_venta_contado_tenant", False),
            "verificador_actor": matriz.get("fs_saas_runtime", {}).get(
                "fs_verificar_actor_reserva", False),
        },
        "runtime_admin": {
            "session_user": admin[0],
            "current_user": admin[1],
            "rolsuper": admin[2],
            "rolbypassrls": admin[3],
            "rolcanlogin": admin[4],
            "rolinherit": admin[5],
            "membresias_pg": admin[6],
            "privilegios_tabla": privilegios_tabla_admin,
            "admin_listar_empresas": matriz.get("saas_admin_runtime", {}).get(
                "listar_empresas", False),
            "admin_crear_empresa": matriz.get("saas_admin_runtime", {}).get(
                "crear_empresa", False),
            "tenant_sp_reserva": matriz.get("saas_admin_runtime", {}).get(
                "sp_crear_reserva_tenant", False),
            "tenant_sp_venta": matriz.get("saas_admin_runtime", {}).get(
                "sp_registrar_venta_contado_tenant", False),
        },
    }


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------


def provisionar(password_admin: str, password_runtime: str,
                password_admin_runtime: str) -> dict:
    lab75 = modulo_5bl()
    admin_dsn = _dsn("postgres", password_admin)
    runtime_dsn = _dsn(ROL_RUNTIME, password_runtime)
    admin_runtime_dsn = _dsn(ROL_ADMIN, password_admin_runtime)
    info = preflight_admin(admin_dsn)

    lab75.construir_tablas_historicas(admin_dsn)
    lab75.aplicar_esquema_historico(admin_dsn)
    filas_historicas = lab75.sembrar_historico(admin_dsn)
    lab75.ajustar_secuencias(admin_dsn)
    nucleo = lab75.aplicar_nucleo_saas(admin_dsn)
    lab75.crear_roles(admin_dsn, password_runtime)
    crear_roles_globales(admin_dsn, password_admin_runtime)
    clave_hmac = secrets.token_urlsafe(48)
    lab75.aplicar_rls(admin_dsn, clave_hmac)
    nulos_historicos = lab75.simular_deriva_auditoria(
        admin_dsn, runtime_dsn, clave_hmac, nucleo["empresa_historica_id"], 1)
    lab75.aplicar_compatibilidad(admin_dsn)
    empresas = lab75.sembrar_empresas(admin_dsn)
    aplicar_operativo(admin_dsn)

    principal = modulo_5bk().verificar_principal(admin_dsn, runtime_dsn)
    compatibilidad = lab75.verificar_compatibilidad(admin_dsn, runtime_dsn)
    operativo = verificar_operativo(admin_dsn, runtime_dsn, admin_runtime_dsn)

    if compatibilidad["violaciones_validacion"] or compatibilidad["triggers_security_definer"]:
        raise LabError("La verificacion de compatibilidad 5BL no quedo limpia")
    tenant = operativo["runtime_tenant"]
    if any(tenant[k] for k in (
        "admin_listar_empresas", "admin_crear_empresa", "insert_usuario")):
        raise LabError("El principal tenant conserva privilegios globales")
    if not all(tenant[k] for k in ("sp_crear_reserva", "sp_venta_contado",
                                   "verificador_actor", "insert_idempotencia")):
        raise LabError("El principal tenant no puede ejecutar las rutinas comerciales")
    admin = operativo["runtime_admin"]
    if (admin["privilegios_tabla"] != 0 or admin["rolsuper"] or admin["rolbypassrls"]
            or not admin["admin_listar_empresas"] or not admin["admin_crear_empresa"]
            or admin["tenant_sp_reserva"] or admin["tenant_sp_venta"]):
        raise LabError("El principal administrativo no esta aislado")

    manifiesto = {
        "fecha": date.today().isoformat(),
        "etapa": "5BM",
        "cluster": {
            "host": HOST_LAB,
            "puerto": PUERTO_LAB,
            "base": BASE_LAB,
            "pgdata": str(ruta_pgdata()),
            "version": info["version"],
        },
        "admin_dsn": admin_dsn,
        "runtime_dsn": runtime_dsn,
        "admin_runtime_dsn": admin_runtime_dsn,
        "assertion_key": clave_hmac,
        "password_usuarios": lab75._cargar(
            "database/saas/74_laboratorio_rls_principal_5bk.py", "lab_5bk_5bm"
        ).PASSWORD_USUARIOS,
        "roles": {
            "runtime": ROL_RUNTIME,
            "rls_owner": ROL_RLS_OWNER,
            "admin_runtime": ROL_ADMIN,
            "admin_owner": ROL_ADMIN_OWNER,
            "actor_owner": ROL_ACTOR_OWNER,
        },
        "empresas": empresas,
        "usuarios": {
            "a_admin": 1, "a_cliente": 2, "b_admin": 3, "b_cliente": 4,
            "b_cajero": 5, "a_deshabilitado": 6, "a_revocado": 7,
            "c_suspendida": 8, "d_inactiva": 9,
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
        "operativo": operativo,
        "esquema": {
            "nucleo": nucleo,
            "filas_historicas_iniciales": filas_historicas,
            "bitacora_null_historicas": nulos_historicos,
        },
        "fidelidad": lab75.FIDELIDAD_ESPERADA,
    }
    ruta_manifiesto().parent.mkdir(parents=True, exist_ok=True)
    ruta_manifiesto().write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    return manifiesto


def escribir_evidencia() -> Path:
    import psycopg

    datos = json.loads(ruta_manifiesto().read_text(encoding="utf-8"))
    with psycopg.connect(datos["admin_dsn"]) as conn:
        roles = conn.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolinherit, "
            "rolcreatedb, rolcreaterole FROM pg_roles "
            "WHERE rolname IN ('fs_saas_runtime','saas_rls_owner','saas_admin_runtime',"
            "'saas_admin_owner','saas_actor_sql_owner') ORDER BY rolname"
        ).fetchall()
        funciones = conn.execute(
            "SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner), "
            "COALESCE(array_to_string(p.proconfig, ','), '<sin>') "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='saas_admin' ORDER BY p.proname"
        ).fetchall()
        rutinas = conn.execute(
            "SELECT p.proname, p.prokind, p.prosecdef, pg_get_userbyid(p.proowner), "
            "COALESCE(array_to_string(p.proconfig, ','), '<sin>') "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.proname LIKE '%_tenant' "
            "ORDER BY p.proname"
        ).fetchall()
        ejecutables = conn.execute(
            "SELECT r.rolname, p.proname, has_function_privilege(r.rolname, p.oid, 'EXECUTE') "
            "FROM pg_roles r CROSS JOIN pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='saas_admin' AND r.rolname IN "
            "('fs_saas_runtime','saas_admin_runtime','saas_admin_owner') "
            "ORDER BY r.rolname, p.proname"
        ).fetchall()
        politicas = conn.execute(
            "SELECT tablename, policyname, cmd FROM pg_policies "
            "WHERE schemaname='public' AND policyname IN "
            "('admin_global_acceso','bitacora_admin_insercion') ORDER BY tablename"
        ).fetchall()
        idem = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE estado='PROCESADA') "
            "FROM saas.operacion_idempotente"
        ).fetchone()
        bitacora_global = conn.execute("SELECT count(*) FROM saas.bitacora_global").fetchone()[0]

    lineas = [
        "EVIDENCIA OPERATIVA - ETAPA E1 (5BM) - APROVISIONAMIENTO + RESERVAS + VENTA",
        f"Fecha UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "Cluster: " + datos["cluster"]["version"] + " en "
        + datos["cluster"]["host"] + ":" + str(datos["cluster"]["puerto"])
        + " / " + datos["cluster"]["base"],
        "Sin secretos: no se incluyen DSN, JWT, contrasenas, HMAC ni datos personales.",
        "-" * 78,
        "ROLES SQL (rol|login|super|bypassrls|inherit|createdb|createrole)",
    ]
    for fila in roles:
        lineas.append("  " + "|".join(str(x) for x in fila))
    lineas.append("FUNCIONES saas_admin (nombre|definer|propietario|config):")
    for nombre, definer, propietario, config in funciones:
        lineas.append(f"  {nombre} | definer={definer} | owner={propietario} | {config}")
    lineas.append("RUTINAS tenant/comerciales (nombre|kind|definer|propietario|config):")
    for nombre, kind, definer, propietario, config in rutinas:
        lineas.append(f"  {nombre} | {kind} | definer={definer} | owner={propietario} | {config}")
    lineas.append("EXECUTE sobre saas_admin (rol|funcion|permiso):")
    for rol, funcion, permiso in ejecutables:
        lineas.append(f"  {rol} | {funcion} | {permiso}")
    lineas.append(
        "Politicas del plano global: "
        + ", ".join(f"{t}.{p}({c})" for t, p, c in politicas))
    lineas.append(
        f"Idempotencia: filas={idem[0]} procesadas={idem[1]} | eventos globales={bitacora_global}")
    lineas += [
        "-" * 78,
        "Principal tenant: " + json.dumps(datos["operativo"]["runtime_tenant"], ensure_ascii=False),
        "Principal administrativo: " + json.dumps(datos["operativo"]["runtime_admin"], ensure_ascii=False),
        "Privilegios del runtime sobre public.usuario INSERT: "
        + str(datos["operativo"]["runtime_tenant"]["insert_usuario"]),
    ]
    evidencias = ROOT / "database" / "saas" / "evidencias"
    evidencias.mkdir(parents=True, exist_ok=True)
    marca = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ruta = evidencias / f"EVIDENCIA_OPERATIVA_5BM_{marca}.txt"
    base, n = ruta, 2
    while ruta.exists():
        ruta = base.with_name(f"EVIDENCIA_OPERATIVA_5BM_{marca}_v{n:02d}.txt")
        n += 1
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def _resumen(manifiesto: dict) -> dict:
    return {
        "cluster": manifiesto["cluster"],
        "principal": manifiesto["principal"],
        "operativo": manifiesto["operativo"],
        "manifiesto": str(ruta_manifiesto()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-desechable", action="store_true")
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--recrear-base", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--evidencia", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)

    pgdata = ruta_pgdata()

    if args.status:
        print(json.dumps({"pgdata": str(pgdata), "estado": estado_cluster(pgdata),
                          "manifiesto": ruta_manifiesto().exists()}, indent=2))
        return 0
    if args.evidencia:
        if not ruta_manifiesto().exists():
            print("No hay manifiesto 5BM; use --init", file=sys.stderr)
            return 2
        print(str(escribir_evidencia()))
        return 0
    if args.stop:
        detener_cluster(pgdata)
        print(json.dumps({"cluster": "detenido", "pgdata": str(pgdata)}, indent=2))
        return 0
    if not args.confirm_desechable:
        print("Falta --confirm-desechable: este script crea un cluster completo",
              file=sys.stderr)
        return 2

    _validar_destino(HOST_LAB, PUERTO_LAB, BASE_LAB, pgdata)
    raiz_lab().mkdir(parents=True, exist_ok=True)
    print("[lab-5bm] rutas verificadas", file=sys.stderr, flush=True)

    archivo_admin = raiz_lab() / ".admin_pw"
    password_admin = archivo_admin.read_text(encoding="utf-8").strip() if archivo_admin.exists() else ""
    if not password_admin:
        password_admin = secrets.token_urlsafe(24)
        archivo_admin.write_text(password_admin, encoding="utf-8")
    password_runtime = secrets.token_urlsafe(24)
    password_admin_runtime = secrets.token_urlsafe(24)

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

    manifiesto = provisionar(password_admin, password_runtime, password_admin_runtime)
    print(json.dumps(_resumen(manifiesto), indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
