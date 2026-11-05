"""5BK / Etapa C - Laboratorio DESECHABLE con principal SQL restringido.

Crea (o reutiliza, tras verificar identidad) un cluster PostgreSQL 18 exclusivo
de este encargo, aplica el esquema heredado simplificado de la Etapa B, siembra
DOS empresas ficticias con administrador, cajero y cliente, instala el principal
restringido + politicas RLS (migracion 20261030000400) y deja un manifiesto local
con las credenciales efimeras fuera del repositorio.

Reglas de seguridad del script
------------------------------
* Solo loopback y solo el puerto reservado 55441 (no 5432/55432/55433/55440).
* El nombre de base debe contener "staging" o "lab" y el pgdata debe vivir bajo
  el directorio temporal de OpenCode.
* Exige ``--confirm-desechable``; si el cluster ya existe, verifica la tabla
  marcador ``saas.lab_identity_5bk`` antes de tocar nada.
* Nunca imprime contrasenas, DSN completos ni la clave HMAC.
* Nunca usa Base.metadata.create_all sobre una base historica: el esquema se
  construye SOLO en la base de este laboratorio, recien creada.

Uso:
    python database/saas/74_laboratorio_rls_principal_5bk.py --confirm-desechable
    python database/saas/74_laboratorio_rls_principal_5bk.py --status
    python database/saas/74_laboratorio_rls_principal_5bk.py --stop
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
PUERTO_LAB = 55441
BASE_LAB = "fashionstore_staging_5bk"
HOST_LAB = "127.0.0.1"
PG_BIN = Path(r"C:\Program Files\PostgreSQL\18\bin")

# Evita que la importacion del ORM lea credenciales reales del .env.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://staging@127.0.0.1/staging")
os.environ.setdefault("SECRET_KEY", "lab-5bk-placeholder-no-usar")

MIGRACION_RLS = "20261030000400_saas_rls_principal_restringido_portable.sql"
ROL_RUNTIME = "fs_saas_runtime"
ROL_OWNER = "saas_rls_owner"

PASSWORD_USUARIOS = "Lab5bk-" + secrets.token_urlsafe(12)


class LabError(RuntimeError):
    pass


def raiz_lab() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Temp" / "opencode" / "fs_lab_5bk"


def ruta_pgdata() -> Path:
    return raiz_lab() / "pgdata"


def ruta_manifiesto() -> Path:
    return raiz_lab() / "lab_5bk_manifest.json"


def _bin(nombre: str) -> Path:
    exe = PG_BIN / f"{nombre}.exe"
    if not exe.exists():
        raise LabError(f"No se encontro {exe}")
    return exe


def _ejecutar(args: list[str], *, env: dict | None = None, check: bool = True,
              capturar: bool = True):
    # En Windows el postmaster hereda los handles del padre: capturar la salida
    # de `pg_ctl start` deja el pipe abierto y bloquea subprocess.run. El arranque
    # se lanza sin captura y su salida va al archivo de log indicado con -l.
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
        # No se vuelca stdout/stderr completo: puede contener el DSN o el pwfile.
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
    if not ("staging" in base.lower() or "lab" in base.lower()):
        raise LabError("El nombre de base debe contener 'staging' o 'lab'")
    temp = Path(os.environ.get("TEMP", ".")).resolve()
    local = Path(os.environ.get("LOCALAPPDATA", str(temp))).resolve()
    resuelto = pgdata.resolve()
    if not (str(resuelto).lower().startswith(str(temp).lower())
            or str(resuelto).lower().startswith(str(local).lower())):
        raise LabError("pgdata debe estar bajo el directorio temporal del usuario")


def _dsn_admin(password: str, base: str = BASE_LAB) -> str:
    return f"postgresql://postgres:{password}@{HOST_LAB}:{PUERTO_LAB}/{base}"


def _dsn_runtime(password: str, base: str = BASE_LAB) -> str:
    return (
        f"postgresql://{ROL_RUNTIME}:{password}@{HOST_LAB}:{PUERTO_LAB}/{base}"
    )


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------


def iniciar_cluster(pgdata: Path, password_admin: str) -> None:
    if pgdata.exists() and any(pgdata.iterdir()):
        raise LabError(
            f"{pgdata} ya contiene datos: use --reuse solo tras verificar el marcador"
        )
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


def crear_base(password_admin: str) -> None:
    _ejecutar(
        [str(_bin("createdb")), "-h", HOST_LAB, "-p", str(PUERTO_LAB),
         "-U", "postgres", BASE_LAB],
        env={"PGPASSWORD": password_admin},
    )


def recrear_base(password_admin: str) -> None:
    """Solo laboratorio: elimina y recrea la base desechable de este cluster."""
    if BASE_LAB != "fashionstore_staging_5bk" or PUERTO_LAB != 55441:
        raise LabError("Guardas de recreacion alteradas: abortar")
    _ejecutar(
        [str(_bin("dropdb")), "--force", "-h", HOST_LAB, "-p", str(PUERTO_LAB),
         "-U", "postgres", BASE_LAB],
        env={"PGPASSWORD": password_admin},
    )
    crear_base(password_admin)


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
# Esquema heredado + migraciones SaaS (reutiliza el trabajo de Etapa B)
# ---------------------------------------------------------------------------


def _modulo_etapa_b():
    ruta = ROOT / "database" / "saas" / "70_staging_saas_desde_cero_5bj.py"
    spec = importlib.util.spec_from_file_location("staging_5bj", ruta)
    if spec is None or spec.loader is None:
        raise LabError("No se pudo cargar 70_staging_saas_desde_cero_5bj.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def construir_esquema(admin_dsn: str) -> dict:
    import psycopg
    from sqlalchemy import create_engine

    etapa_b = _modulo_etapa_b()
    engine = create_engine(admin_dsn.replace("postgresql://", "postgresql+psycopg://"), future=True)
    try:
        etapa_b._esquema_heredado(engine)
    finally:
        engine.dispose()

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("SET search_path = public")
        etapa_b._reducir_a_historico(conn)
        conteos = etapa_b._sembrar(conn)
        etapa_b._aplicar_migraciones(conn, "empresa-a", True)
    return {"tablas_sembradas": len(conteos), "filas_iniciales": conteos}


# ---------------------------------------------------------------------------
# Datos ficticios de dos empresas
# ---------------------------------------------------------------------------

SQL_SEMILLA_A_B = [
    # Roles base adicionales (globales del primer parcial).
    ("rol_cajero",
     "INSERT INTO public.rol (id, nombre, estado) VALUES (3, 'CAJERO', TRUE) "
     "ON CONFLICT (id) DO NOTHING"),
    # --- Empresa B -------------------------------------------------------
    ("empresa_b",
     "INSERT INTO public.empresa (id, razon_social, nombre_comercial, slug, estado) "
     "VALUES (2, 'Empresa B Ficticia SRL', 'Boutique B', 'empresa-b', TRUE)"),
    ("empresa_b_config",
     "INSERT INTO public.empresa_configuracion (empresa_id) VALUES (2)"),
    ("empresa_b_suscripcion",
     "INSERT INTO public.suscripcion "
     "(empresa_id, plan_saas_id, fecha_inicio, fecha_vencimiento, estado) "
     "SELECT 2, id, CURRENT_DATE, CURRENT_DATE + 365, 'ACTIVA' "
     "FROM public.plan_saas WHERE codigo = 'BASICO'"),
    # --- Empresa C: suscripcion suspendida -------------------------------
    ("empresa_c",
     "INSERT INTO public.empresa (id, razon_social, nombre_comercial, slug, estado) "
     "VALUES (3, 'Empresa C Suspendida', 'Empresa C', 'empresa-c', TRUE)"),
    ("empresa_c_config",
     "INSERT INTO public.empresa_configuracion (empresa_id) VALUES (3)"),
    ("empresa_c_suscripcion",
     "INSERT INTO public.suscripcion "
     "(empresa_id, plan_saas_id, fecha_inicio, fecha_vencimiento, estado) "
     "SELECT 3, id, CURRENT_DATE, CURRENT_DATE + 365, 'SUSPENDIDA' "
     "FROM public.plan_saas WHERE codigo = 'PROFESIONAL'"),
    # --- Empresa D: empresa inactiva, suscripcion activa -----------------
    ("empresa_d",
     "INSERT INTO public.empresa (id, razon_social, nombre_comercial, slug, estado) "
     "VALUES (4, 'Empresa D Inactiva', 'Empresa D', 'empresa-d', FALSE)"),
    ("empresa_d_config",
     "INSERT INTO public.empresa_configuracion (empresa_id) VALUES (4)"),
    ("empresa_d_suscripcion",
     "INSERT INTO public.suscripcion "
     "(empresa_id, plan_saas_id, fecha_inicio, fecha_vencimiento, estado) "
     "SELECT 4, id, CURRENT_DATE, CURRENT_DATE + 365, 'ACTIVA' "
     "FROM public.plan_saas WHERE codigo = 'PROFESIONAL'"),
    # --- Usuarios --------------------------------------------------------
    ("usuarios",
     "INSERT INTO public.usuario (id, rol_id, correo, password_hash, fecha_creacion, estado) "
     "VALUES (3, 1, 'admin.b@staging.invalid', 'pendiente', NOW(), TRUE),"
     "       (4, 2, 'cliente.b@staging.invalid', 'pendiente', NOW(), TRUE),"
     "       (5, 3, 'cajero.b@staging.invalid', 'pendiente', NOW(), TRUE),"
     "       (6, 1, 'deshabilitado@staging.invalid', 'pendiente', NOW(), FALSE),"
     "       (7, 1, 'revocado@staging.invalid', 'pendiente', NOW(), TRUE),"
     "       (8, 1, 'suspendida@staging.invalid', 'pendiente', NOW(), TRUE),"
     "       (9, 1, 'inactiva@staging.invalid', 'pendiente', NOW(), TRUE)"),
    # --- Sucursales ------------------------------------------------------
    ("sucursal_b",
     "INSERT INTO public.sucursal (id, empresa_id, ciudad_id, nombre, direccion, estado) "
     "VALUES (100, 2, 1, 'Sucursal B Centro', 'Calle B 100', TRUE)"),
    ("sucursal_c",
     "INSERT INTO public.sucursal (id, empresa_id, ciudad_id, nombre, direccion, estado) "
     "VALUES (200, 3, 1, 'Sucursal C', 'Calle C 200', TRUE)"),
    ("sucursal_d",
     "INSERT INTO public.sucursal (id, empresa_id, ciudad_id, nombre, direccion, estado) "
     "VALUES (300, 4, 1, 'Sucursal D', 'Calle D 300', TRUE)"),
    # --- Catalogo B ------------------------------------------------------
    ("temporada_b",
     "INSERT INTO public.temporada (id, empresa_id, nombre, fecha_inicio, fecha_fin, estado) "
     "VALUES (100, 2, '2026-B', DATE '2026-01-01', DATE '2026-12-31', TRUE)"),
    ("categoria_b",
     "INSERT INTO public.categoria (id, empresa_id, nombre, estado) "
     "VALUES (100, 2, 'Camisas B', TRUE)"),
    ("producto_b",
     "INSERT INTO public.producto (id, empresa_id, categoria_id, nombre, precio, estado) "
     "VALUES (100, 2, 100, 'Camisa Ficticia B', 150.00, TRUE)"),
    ("variante_b",
     "INSERT INTO public.variante_producto "
     "(id, empresa_id, producto_id, talla_id, color_id, sku, estado) "
     "VALUES (100, 2, 100, 1, 1, 'SKU-B-100', TRUE)"),
    ("inventario_b",
     "INSERT INTO public.inventario "
     "(id, empresa_id, sucursal_id, variante_producto_id, temporada_id, "
     " stock_actual, stock_reservado, fecha_actualizacion) "
     "VALUES (100, 2, 100, 100, 100, 5, 0, NOW())"),
    # --- Personas --------------------------------------------------------
    ("empleado_b_admin",
     "INSERT INTO public.empleado "
     "(id, empresa_id, usuario_id, sucursal_id, nombres, apellidos, ci, "
     " fecha_contratacion, estado) "
     "VALUES (100, 2, 3, 100, 'Admin', 'B', 'CI-B-1', DATE '2026-01-02', TRUE)"),
    ("empleado_b_cajero",
     "INSERT INTO public.empleado "
     "(id, empresa_id, usuario_id, sucursal_id, nombres, apellidos, ci, "
     " fecha_contratacion, estado) "
     "VALUES (101, 2, 5, 100, 'Cajero', 'B', 'CI-B-2', DATE '2026-01-02', TRUE)"),
    ("cliente_b",
     "INSERT INTO public.cliente (id, empresa_id, usuario_id, nombre, apellido, ci, estado) "
     "VALUES (100, 2, 4, 'Cliente', 'B', 'CI-B-3', TRUE)"),
    ("empleado_a_deshabilitado",
     "INSERT INTO public.empleado "
     "(id, empresa_id, usuario_id, sucursal_id, nombres, apellidos, ci, "
     " fecha_contratacion, estado) "
     "VALUES (200, 1, 6, 1, 'Deshabilitado', 'A', 'CI-A-6', DATE '2026-01-02', TRUE)"),
    ("empleado_a_revocado",
     "INSERT INTO public.empleado "
     "(id, empresa_id, usuario_id, sucursal_id, nombres, apellidos, ci, "
     " fecha_contratacion, estado) "
     "VALUES (201, 1, 7, 1, 'Revocado', 'A', 'CI-A-7', DATE '2026-01-02', TRUE)"),
    ("empleado_c",
     "INSERT INTO public.empleado "
     "(id, empresa_id, usuario_id, sucursal_id, nombres, apellidos, ci, "
     " fecha_contratacion, estado) "
     "VALUES (300, 3, 8, 200, 'Suspendida', 'C', 'CI-C-8', DATE '2026-01-02', TRUE)"),
    ("empleado_d",
     "INSERT INTO public.empleado "
     "(id, empresa_id, usuario_id, sucursal_id, nombres, apellidos, ci, "
     " fecha_contratacion, estado) "
     "VALUES (400, 4, 9, 300, 'Inactiva', 'D', 'CI-D-9', DATE '2026-01-02', TRUE)"),
    # --- Membresias ------------------------------------------------------
    ("membresia_b_admin",
     "INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado) "
     "VALUES (3, 2, 1, TRUE)"),
    ("membresia_b_cajero",
     "INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado) "
     "VALUES (5, 2, 3, TRUE)"),
    ("membresia_b_cliente",
     "INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado) "
     "VALUES (4, 2, 2, TRUE)"),
    ("membresia_a_admin",
     "INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado) "
     "VALUES (1, 1, 1, TRUE), (2, 1, 2, TRUE) "
     "ON CONFLICT (usuario_id, empresa_id) DO NOTHING"),
    ("membresia_a_deshabilitado",
     "INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado) "
     "VALUES (6, 1, 1, TRUE)"),
    ("membresia_a_revocada",
     "INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado) "
     "VALUES (7, 1, 1, FALSE)"),
    ("membresia_c",
     "INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado) "
     "VALUES (8, 3, 1, TRUE)"),
    ("membresia_d",
     "INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado) "
     "VALUES (9, 4, 1, TRUE)"),
]


def sembrar_empresas(admin_dsn: str) -> dict:
    import psycopg

    from app.core.security import hash_password

    hash_argon2 = hash_password(PASSWORD_USUARIOS)

    # Orden de dependencias: (1) catalogos/usuarios/empresas/sucursales,
    # (2) membresias (las FK de cliente/empleado apuntan a usuario_empresa),
    # (3) perfiles de cliente y empleado.
    def _prioridad(nombre: str) -> int:
        if nombre.startswith("membresia"):
            return 2
        if nombre.startswith(("empleado", "cliente")):
            return 3
        return 1

    pasos = sorted(SQL_SEMILLA_A_B, key=lambda paso: _prioridad(paso[0]))

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("SET search_path = public")
        for nombre, sql in pasos:
            try:
                conn.execute(sql)
            except Exception as exc:  # noqa: BLE001 - se reporta el paso, no el DSN
                raise LabError(f"Fallo la semilla '{nombre}': {type(exc).__name__}") from exc
        conn.execute(
            "UPDATE public.usuario SET password_hash = %s WHERE id BETWEEN 1 AND 9",
            (hash_argon2,),
        )
        # La semilla usa IDs explicitos: adelantar cada secuencia para que los
        # INSERT del backend (que no envia id) no colisionen con las PK sembradas.
        tablas = [
            fila[0] for fila in conn.execute(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"
            ).fetchall()
        ]
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
        ids = dict(
            conn.execute("SELECT slug, id FROM public.empresa ORDER BY id").fetchall()
        )
        ids_rev = {valor: clave for clave, valor in ids.items()}
        if len(ids) != 4:
            raise LabError("Se esperaban 4 empresas ficticias")
    return {
        "empresa_a": ids["empresa-a"],
        "empresa_b": ids["empresa-b"],
        "empresa_c": ids["empresa-c"],
        "empresa_d": ids["empresa-d"],
        "empresas_por_id": ids_rev,
    }


# ---------------------------------------------------------------------------
# Roles SQL y RLS
# ---------------------------------------------------------------------------


def crear_roles(admin_dsn: str, password_runtime: str) -> None:
    import psycopg
    from psycopg import sql

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        for rol in (ROL_OWNER, ROL_RUNTIME):
            existe = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (rol,)
            ).fetchone()
            if existe is None:
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
        # El propietario de funciones necesita leer las tablas que valida.
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(ROL_OWNER))
        )


def aplicar_rls(admin_dsn: str, clave_hmac: str) -> None:
    import psycopg

    ruta = ROOT / "supabase" / "migrations" / MIGRACION_RLS
    contenido = ruta.read_text(encoding="utf-8")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f"SET saas.runtime_role = '{ROL_RUNTIME}'")
        conn.execute(f"SET saas.rls_owner_role = '{ROL_OWNER}'")
        conn.execute(contenido)
        conn.execute(
            "INSERT INTO saas.rls_secret (singleton, clave) VALUES (TRUE, %s) "
            "ON CONFLICT (singleton) DO UPDATE SET clave = EXCLUDED.clave, "
            "actualizado = NOW()",
            (clave_hmac,),
        )


def verificar_principal(admin_dsn: str, runtime_dsn: str) -> dict:
    """Comprobaciones minimas de identidad del principal restringido."""
    import psycopg

    with psycopg.connect(runtime_dsn) as conn:
        row = conn.execute(
            """
            SELECT session_user, current_user, r.rolsuper, r.rolbypassrls,
                   r.rolinherit,
                   (SELECT count(*) FROM pg_auth_members m WHERE m.member = r.oid),
                   current_setting('transaction_read_only')
              FROM pg_roles r WHERE r.rolname = current_user
            """
        ).fetchone()
        tablas = conn.execute(
            """
            SELECT count(*) FILTER (WHERE c.relrowsecurity),
                   count(*) FILTER (WHERE c.relforcerowsecurity),
                   count(*) FILTER (WHERE c.relowner = r.oid)
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
              CROSS JOIN pg_roles r
             WHERE n.nspname = 'public' AND c.relkind = 'r'
               AND r.rolname = current_user
            """
        ).fetchone()
        politicas = conn.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname = 'public'"
        ).fetchone()[0]
        tabla_priv = conn.execute(
            "SELECT has_table_privilege(current_user, 'public.usuario', 'SELECT')"
        ).fetchone()[0]
        func_priv = conn.execute(
            "SELECT has_function_privilege(current_user, 'saas.login_credenciales(text)', 'EXECUTE')"
        ).fetchone()[0]
        tenant_actual = conn.execute("SELECT saas.tenant_actual()").fetchone()[0]
    return {
        "session_user": row[0],
        "current_user": row[1],
        "rolsuper": row[2],
        "rolbypassrls": row[3],
        "hereda_roles": row[4],
        "membresias_pg": row[5],
        "read_only": row[6],
        "tablas_con_rls": tablas[0],
        "tablas_forzadas": tablas[1],
        "tablas_propias": tablas[2],
        "politicas": politicas,
        "select_directo_usuario": tabla_priv,
        "execute_login_credenciales": func_priv,
        "tenant_actual_sin_asercion": tenant_actual,
    }


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------


def provisionar(password_admin: str, password_runtime: str) -> dict:
    admin_dsn = _dsn_admin(password_admin)
    runtime_dsn = _dsn_runtime(password_runtime)
    info = preflight_admin(admin_dsn)
    esquema = construir_esquema(admin_dsn)
    crear_roles(admin_dsn, password_runtime)
    empresas = sembrar_empresas(admin_dsn)
    clave_hmac = secrets.token_urlsafe(48)
    aplicar_rls(admin_dsn, clave_hmac)
    principal = verificar_principal(admin_dsn, runtime_dsn)
    manifiesto = {
        "fecha": date.today().isoformat(),
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
        "password_usuarios": PASSWORD_USUARIOS,
        "roles": {"runtime": ROL_RUNTIME, "owner": ROL_OWNER},
        "empresas": empresas,
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
        "esquema": esquema,
    }
    ruta_manifiesto().parent.mkdir(parents=True, exist_ok=True)
    ruta_manifiesto().write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return manifiesto


def _resumen(manifiesto: dict) -> dict:
    """Vista sin secretos para consola."""
    return {
        "cluster": manifiesto["cluster"],
        "principal": manifiesto["principal"],
        "empresas": {k: v for k, v in manifiesto["empresas"].items()
                     if k != "empresas_por_id"},
        "manifiesto": str(ruta_manifiesto()),
        "esquema": manifiesto["esquema"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-desechable", action="store_true")
    parser.add_argument("--init", action="store_true", help="Crear cluster y base")
    parser.add_argument("--reuse", action="store_true", help="Reutilizar cluster ya verificado")
    parser.add_argument("--recrear-base", action="store_true",
                        help="Borrar y recrear SOLO la base desechable del laboratorio")
    parser.add_argument("--status", action="store_true")
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

    if args.stop:
        detener_cluster(pgdata)
        print(json.dumps({"cluster": "detenido", "pgdata": str(pgdata)}, indent=2))
        return 0

    if not args.confirm_desechable:
        print("Falta --confirm-desechable: este script crea un cluster completo", file=sys.stderr)
        return 2

    _validar_destino(HOST_LAB, PUERTO_LAB, BASE_LAB, pgdata)
    raiz_lab().mkdir(parents=True, exist_ok=True)
    print("[lab-5bk] rutas verificadas", file=sys.stderr, flush=True)

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
        crear_base(password_admin)
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
