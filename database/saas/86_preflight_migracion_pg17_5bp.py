"""5BP / Etapa G - Preflight y runner del pipeline de migraciones controladas.

Que resuelve
------------
`supabase/migrations` mezcla cuatro familias de archivos con propositos
incompatibles:

* ``20261023000100..000300``: version local previa del nucleo SaaS (Etapa B).
* ``20261023000400_cuatro_tenants_demo_local.sql``: seed de CUATRO empresas de
  demostracion. Es exclusivamente de laboratorio y **no debe ejecutarse jamas**
  en un destino real.
* ``20261030000100..000900``: secuencia portable y aditiva de produccion.
* ``database/saas/75_esquema_historico_reconstruido_5bl.sql``: reconstruccion
  PARCIAL del esquema heredado, solo valida en laboratorio.

Por eso ``supabase db push`` sobre esa carpeta es inseguro: no permite excluir
las demos y no tiene orden explicito ni checksums. Este runner aplica un
manifiesto versionado (``85_manifiesto_migracion_controlada_5bp.json``) que:

1. fija el ORDEN con un campo numerico (nunca orden lexicografico);
2. exige SHA-256 de cada archivo incluido y aborta si cambia;
3. prohibe explicita y automaticamente los seeds demo;
4. registra cada version aplicada en ``saas_migracion.version_aplicada``;
5. rechaza puertos, bases y origenes que parezcan protegidos.

Modos
-----
``--plan``          (por defecto) valida todo sin abrir conexion ni escribir.
``--check-local``   plan + clasificacion completa de ``supabase/migrations``.
``--rehash``        recalcula los SHA-256 y reescribe el manifiesto.
``--estado``        muestra las versiones aplicadas en un laboratorio concreto.
``--aplicar-lab``   aplica el pipeline en loopback + base desechable.

Reglas de seguridad del modo de aplicacion
------------------------------------------
* No existe una opcion para pasarle un DSN ni una URL de Supabase.
* Requiere ``--confirm-desechable``, ``--base`` con marcador de descarte y
  host loopback. Los puertos de E1/E2/F y de las bases historicas estan
  prohibidos de forma dura.
* Las contrasenas se leen de variables de entorno; nunca se imprimen, ni se
  escriben en el manifiesto, ni se incluyen en evidencia.

Uso
---
    python database/saas/86_preflight_migracion_pg17_5bp.py --check-local
    python database/saas/86_preflight_migracion_pg17_5bp.py --plan --json
    python database/saas/86_preflight_migracion_pg17_5bp.py --aplicar-lab \
        --base fashionstore_lab_pg17_5bp --confirm-desechable
    python database/saas/86_preflight_migracion_pg17_5bp.py --estado \
        --base fashionstore_lab_pg17_5bp
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUTA_MANIFIESTO = ROOT / "database" / "saas" / "85_manifiesto_migracion_controlada_5bp.json"
DIR_MIGRACIONES = ROOT / "supabase" / "migrations"

HOST_PERMITIDO = "127.0.0.1"
PUERTO_POR_DEFECTO = 55446
# 5BR mantiene su propio conjunto de puertos prohibidos en 96 y en su manifiesto.
# Aqui se conserva el contrato verificado de la Etapa G (55446 es su laboratorio).
PUERTOS_PROHIBIDOS = {5432, 55432, 55433, 55440, 55441, 55442, 55443, 55444, 55445}
MARCADOR_BASE = re.compile(r"(lab|desechable|staging_tmp|restore)")
ESQUEMA_REGISTRO = "saas_migracion"
TABLA_REGISTRO = f"{ESQUEMA_REGISTRO}.version_aplicada"

# Firmas de contenido que nunca pueden aparecer en un archivo aplicable.
FIRMAS_DEMO = (
    "cuatro_tenants_demo",
    "cuatro tenants demo",
    "tenant_demo_local",
)

VARIABLES_SECRETO = {
    "runtime_password": "FASHIONSTORE_LAB_5BP_RUNTIME_PASSWORD",
    "admin_runtime_password": "FASHIONSTORE_LAB_5BP_ADMIN_PASSWORD",
    "tenant_assertion_key": "FASHIONSTORE_LAB_5BP_ASSERTION_KEY",
}
VARIABLE_PASSWORD_ADMIN = "FASHIONSTORE_LAB_5BP_DB_PASSWORD"


class MigracionError(RuntimeError):
    """Error de validacion o de ejecucion del pipeline."""


# ---------------------------------------------------------------------------
# Carga y validacion estatica
# ---------------------------------------------------------------------------


def cargar_manifiesto(ruta: Path | None = None) -> dict:
    ruta = ruta or RUTA_MANIFIESTO
    if not ruta.exists():
        raise MigracionError(f"No existe el manifiesto: {ruta}")
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensivo
        raise MigracionError(f"Manifiesto JSON invalido: {exc}") from exc


def sha256_archivo(relativa: str) -> str:
    ruta = ROOT / relativa
    if not ruta.exists():
        raise MigracionError(f"Archivo declarado inexistente: {relativa}")
    digest = hashlib.sha256()
    with ruta.open("rb") as manejador:
        for bloque in iter(lambda: manejador.read(65536), b""):
            digest.update(bloque)
    return digest.hexdigest()


def archivos_referenciados(manifiesto: dict) -> list[str]:
    vistos: list[str] = []
    for fase in manifiesto.get("fases", []):
        for archivo in fase.get("archivos", []):
            if archivo not in vistos:
                vistos.append(archivo)
    return vistos


def calcular_checksums(manifiesto: dict) -> dict[str, str]:
    return {archivo: sha256_archivo(archivo) for archivo in archivos_referenciados(manifiesto)}


def actualizar_checksums(manifiesto: dict) -> dict[str, str]:
    calculados = calcular_checksums(manifiesto)
    manifiesto["checksums"] = calculados
    return calculados


def _clasificacion(manifiesto: dict) -> tuple[set[str], dict[str, dict]]:
    incluidas = set(archivos_referenciados(manifiesto))
    excluidas = {
        item["archivo"]: item
        for item in manifiesto.get("exclusiones", [])
        if item.get("archivo")
    }
    return incluidas, excluidas


def clasificar_migraciones(manifiesto: dict) -> dict:
    """Verifica que TODO archivo de ``supabase/migrations`` este clasificado.

    Un archivo nuevo sin clasificar es exactamente el escenario que este runner
    debe impedir: alguien agrega una migracion y el pipeline la ignora en
    silencio (o la aplica sin revision). Por eso se trata como error.
    """
    incluidas, excluidas = _clasificacion(manifiesto)
    presentes = sorted(p.name for p in DIR_MIGRACIONES.glob("*.sql"))
    en_fases = {Path(ruta).name for ruta in incluidas if Path(ruta).parent.name == "migrations"}
    excluidas_migraciones = {
        Path(ruta).name for ruta in excluidas if Path(ruta).parent.name == "migrations"
    }
    sin_clasificar = [
        nombre for nombre in presentes
        if nombre not in en_fases and nombre not in excluidas_migraciones
    ]
    inexistentes = [
        nombre for nombre in (en_fases | excluidas_migraciones) if nombre not in presentes
    ]
    doble_clasificacion = sorted(en_fases & excluidas_migraciones)
    return {
        "presentes": presentes,
        "incluidas": sorted(en_fases),
        "excluidas": sorted(excluidas_migraciones),
        "sin_clasificar": sin_clasificar,
        "inexistentes": inexistentes,
        "doble_clasificacion": doble_clasificacion,
    }


def validar_manifiesto(manifiesto: dict, *, exigir_checksums: bool = True) -> dict:
    errores: list[str] = []
    advertencias: list[str] = []

    for clave in ("manifiesto_id", "version", "fases", "exclusiones", "checksums"):
        if clave not in manifiesto:
            errores.append(f"Falta la clave obligatoria '{clave}'")

    fases = manifiesto.get("fases", [])
    if not fases:
        errores.append("El manifiesto no declara fases")

    ids: set[str] = set()
    ordenes: list[int] = []
    incluidas, excluidas = _clasificacion(manifiesto)
    demo = {
        ruta for ruta, item in excluidas.items()
        if item.get("categoria") == "datos_demo"
    }

    for fase in fases:
        ident = fase.get("id")
        if not ident:
            errores.append("Fase sin 'id'")
            continue
        if ident in ids:
            errores.append(f"Fase duplicada: {ident}")
        ids.add(ident)

        orden = fase.get("orden")
        if not isinstance(orden, int):
            errores.append(f"{ident}: 'orden' debe ser entero")
        else:
            ordenes.append(orden)

        archivos = fase.get("archivos") or []
        if not archivos:
            errores.append(f"{ident}: no declara archivos")
        for archivo in archivos:
            if archivo in demo:
                errores.append(
                    f"{ident}: incluye un seed demo prohibido ({archivo})")
            if not (ROOT / archivo).exists():
                errores.append(f"{ident}: archivo inexistente {archivo}")

        if fase.get("tipo") == "python" and not fase.get("operacion"):
            errores.append(f"{ident}: fase python sin 'operacion'")

        secreto = fase.get("secreto")
        if secreto and secreto not in VARIABLES_SECRETO:
            errores.append(f"{ident}: secreto desconocido '{secreto}'")

    if ordenes != sorted(ordenes):
        errores.append("Las fases no estan declaradas en orden ascendente")
    if len(set(ordenes)) != len(ordenes):
        errores.append("Hay ordenes repetidos: el plan dejaria de ser determinista")

    for fase in fases:
        for requisito in fase.get("requisitos", []):
            if requisito not in ids:
                errores.append(f"{fase.get('id')}: requisito huerfano '{requisito}'")
            else:
                previa = next(f for f in fases if f.get("id") == requisito)
                if previa.get("orden", 0) >= fase.get("orden", 0):
                    errores.append(
                        f"{fase.get('id')}: el requisito '{requisito}' no es anterior")

    # Versiones de motor.
    motor = manifiesto.get("motor_esperado", {})
    if int(motor.get("version_minima", 0) or 0) < 17:
        errores.append("El manifiesto no exige PostgreSQL 17 o superior")

    # Puertos prohibidos en la politica global.
    politica = manifiesto.get("requisitos_globales", {})
    for puerto in politica.get("puertos_prohibidos", []):
        if puerto not in PUERTOS_PROHIBIDOS:
            advertencias.append(
                f"El manifiesto prohibe el puerto {puerto}, no listado por el runner")

    # Clasificacion de la carpeta de migraciones.
    clasificacion = clasificar_migraciones(manifiesto)
    for nombre in clasificacion["sin_clasificar"]:
        errores.append(f"Migracion sin clasificar en supabase/migrations: {nombre}")
    for nombre in clasificacion["inexistentes"]:
        errores.append(f"El manifiesto referencia una migracion ausente: {nombre}")
    for nombre in clasificacion["doble_clasificacion"]:
        errores.append(f"Migracion incluida y excluida a la vez: {nombre}")

    # Firmas demo en cualquier archivo aplicable.
    for archivo in incluidas:
        if archivo in demo:
            continue
        try:
            texto = (ROOT / archivo).read_text(encoding="utf-8", errors="ignore").lower()
        except OSError as exc:
            errores.append(f"No se pudo leer {archivo}: {exc}")
            continue
        for firma in FIRMAS_DEMO:
            if firma in texto:
                errores.append(f"{archivo}: contiene la firma demo '{firma}'")

    # Checksums.
    if exigir_checksums:
        declarados = manifiesto.get("checksums") or {}
        for archivo in incluidas:
            esperado = declarados.get(archivo)
            if not esperado:
                errores.append(f"Sin SHA-256 declarado: {archivo}")
                continue
            try:
                real = sha256_archivo(archivo)
            except MigracionError as exc:
                errores.append(str(exc))
                continue
            if real != esperado:
                errores.append(
                    f"Checksum divergente en {archivo}: declarado {esperado[:12]}..., "
                    f"real {real[:12]}...")
        for archivo in declarados:
            if archivo not in incluidas:
                advertencias.append(
                    f"Checksum de un archivo que ya no esta en el plan: {archivo}")

    return {
        "ok": not errores,
        "errores": errores,
        "advertencias": advertencias,
        "clasificacion": clasificacion,
    }


def planificar(manifiesto: dict, *, solo_laboratorio: bool = True) -> list[dict]:
    """Devuelve el plan efectivo, en orden, sin abrir conexion."""
    plan: list[dict] = []
    for fase in sorted(manifiesto.get("fases", []), key=lambda f: f.get("orden", 0)):
        if fase.get("solo_laboratorio") and not solo_laboratorio:
            continue
        plan.append({
            "orden": fase.get("orden"),
            "id": fase.get("id"),
            "tipo": fase.get("tipo"),
            "operacion": fase.get("operacion"),
            "solo_laboratorio": bool(fase.get("solo_laboratorio")),
            "archivos": list(fase.get("archivos", [])),
            "secreto": fase.get("secreto"),
            "requisitos": list(fase.get("requisitos", [])),
        })
    return plan


# ---------------------------------------------------------------------------
# Guardas de destino
# ---------------------------------------------------------------------------


@dataclass
class Destino:
    host: str
    puerto: int
    base: str
    password: str = field(repr=False, default="")


def validar_destino(host: str, puerto: int, base: str) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise MigracionError(
            "Solo se permite loopback. Este runner no acepta origenes remotos.")
    if puerto in PUERTOS_PROHIBIDOS:
        raise MigracionError(
            f"Puerto {puerto} prohibido: pertenece a un laboratorio o base protegida.")
    if not (1024 <= int(puerto) <= 65535):
        raise MigracionError(f"Puerto fuera de rango: {puerto}")
    if not MARCADOR_BASE.search(base or ""):
        raise MigracionError(
            f"La base '{base}' no lleva marcador de descarte (lab/desechable/"
            "staging_tmp/restore). Abortado para no tocar una base real.")
    if base.lower() in {"postgres", "template0", "template1"}:
        raise MigracionError("Base de sistema prohibida como destino")


def dsn_laboratorio(destino: Destino, usuario: str) -> str:
    from urllib.parse import quote

    return (
        f"postgresql://{usuario}:{quote(destino.password, safe='')}"
        f"@{destino.host}:{destino.puerto}/{destino.base}"
    )


def _secreto(clave: str) -> str:
    variable = VARIABLES_SECRETO.get(clave)
    if not variable:
        raise MigracionError(f"Secreto no reconocido: {clave}")
    valor = os.environ.get(variable, "")
    if not valor:
        raise MigracionError(
            f"Falta el secreto '{clave}'. Definir la variable de entorno {variable}.")
    return valor


# ---------------------------------------------------------------------------
# Registro de versiones
# ---------------------------------------------------------------------------


def _asegurar_registro(conn) -> None:
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {ESQUEMA_REGISTRO}")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLA_REGISTRO} (
          fase        TEXT PRIMARY KEY,
          orden       INTEGER NOT NULL,
          checksum    TEXT NOT NULL,
          archivos    TEXT NOT NULL,
          aplicado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          duracion_ms INTEGER,
          reaplicada  BOOLEAN NOT NULL DEFAULT FALSE
        )
        """
    )
    conn.execute(
        f"COMMENT ON TABLE {TABLA_REGISTRO} IS "
        "'5BP: versiones aplicadas del manifiesto controlado. Solo laboratorio.'"
    )


def version_aplicada(conn, fase_id: str) -> dict | None:
    fila = conn.execute(
        f"SELECT fase, checksum, aplicado_en, reaplicada FROM {TABLA_REGISTRO} "
        "WHERE fase = %s",
        (fase_id,),
    ).fetchone()
    if not fila:
        return None
    return {"fase": fila[0], "checksum": fila[1], "aplicado_en": fila[2],
            "reaplicada": fila[3]}


def checksum_fase(fase: dict, manifiesto: dict) -> str:
    """Huella compuesta: identidad de la fase + hash de cada archivo."""
    digest = hashlib.sha256()
    digest.update(str(fase.get("id")).encode("utf-8"))
    for archivo in fase.get("archivos", []):
        digest.update(archivo.encode("utf-8"))
        digest.update(sha256_archivo(archivo).encode("utf-8"))
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Operaciones del plan
# ---------------------------------------------------------------------------


def _modulo(relativa: str, alias: str):
    ruta = ROOT / relativa
    if not ruta.exists():
        raise MigracionError(f"Modulo no encontrado: {relativa}")
    if alias in sys.modules:
        # Identidad estable: un modulo importado dos veces debe conservar sus
        # constantes (por ejemplo el password de usuarios del laboratorio).
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, ruta)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[alias] = modulo
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


def _aplicar_gucs(conn, gucs: dict[str, str]) -> None:
    from psycopg import sql

    for clave, valor in (gucs or {}).items():
        if not re.fullmatch(r"saas\.[a-z_][a-z0-9_]*", clave):
            raise MigracionError(f"GUC no permitido: {clave}")
        conn.execute(
            sql.SQL("SET {} = {}").format(sql.SQL(clave), sql.Literal(str(valor)))
        )


def _ejecutar_sql(ctx: dict, fase: dict) -> None:
    import psycopg

    archivo = next(a for a in fase["archivos"] if a.endswith(".sql"))
    texto = (ROOT / archivo).read_text(encoding="utf-8")
    with psycopg.connect(ctx["admin_dsn"], autocommit=True) as conn:
        _aplicar_gucs(conn, fase.get("gucs", {}))
        conn.execute(texto)


def _ejecutar_operacion(ctx: dict, fase: dict) -> None:
    import psycopg

    operacion = fase.get("operacion")
    admin_dsn = ctx["admin_dsn"]

    if operacion == "sql":
        _ejecutar_sql(ctx, fase)
        return

    if operacion == "esquema_real_verificado":
        # 5BR: el esquema historico NO se reconstruye: se restauro antes con 96.
        # Esta fase solo certifica que el destino contiene el original exacto.
        modulo = _modulo("database/saas/99_ensayo_esquema_real_5br.py",
                         "lab_5br_5bp")
        ctx["esquema_real"] = modulo.verificar_esquema_real(admin_dsn)
        return

    if operacion == "semilla_esquema_real":
        modulo = _modulo("database/saas/99_ensayo_esquema_real_5br.py",
                         "lab_5br_5bp")
        ctx["conteos_historicos"] = modulo.sembrar_esquema_real(admin_dsn)
        return

    if operacion == "historico_tablas":
        modulo = _modulo("database/saas/75_laboratorio_esquema_historico_5bl.py",
                         "lab_5bl_5bp")
        modulo.construir_tablas_historicas(admin_dsn)
        return

    if operacion == "historico_objetos":
        _ejecutar_sql(ctx, fase)
        return

    if operacion == "historico_datos":
        modulo = _modulo("database/saas/75_laboratorio_esquema_historico_5bl.py",
                         "lab_5bl_5bp")
        ctx["conteos_historicos"] = modulo.sembrar_historico(admin_dsn)
        return

    if operacion == "historico_secuencias":
        modulo = _modulo("database/saas/75_laboratorio_esquema_historico_5bl.py",
                         "lab_5bl_5bp")
        ctx["secuencias"] = modulo.ajustar_secuencias(admin_dsn)
        return

    if operacion == "roles_tenant":
        modulo = _modulo("database/saas/75_laboratorio_esquema_historico_5bl.py",
                         "lab_5bl_5bp")
        modulo.crear_roles(admin_dsn, _secreto("runtime_password"))
        return

    if operacion == "roles_globales":
        modulo = _modulo("database/saas/76_laboratorio_operativo_5bm.py",
                         "lab_5bm_5bp")
        modulo.crear_roles_globales(admin_dsn, _secreto("admin_runtime_password"))
        return

    if operacion == "nucleo_saas":
        modulo = _modulo("database/saas/70_staging_saas_desde_cero_5bj.py",
                         "staging_5bj_5bp")
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute("SET search_path = public")
            modulo._aplicar_migraciones(conn, ctx["slug_historico"], True)
        return

    if operacion == "rls_principal":
        modulo = _modulo("database/saas/75_laboratorio_esquema_historico_5bl.py",
                         "lab_5bl_5bp")
        modulo.aplicar_rls(admin_dsn, _secreto("tenant_assertion_key"))
        return

    if operacion == "compatibilidad":
        modulo = _modulo("database/saas/75_laboratorio_esquema_historico_5bl.py",
                         "lab_5bl_5bp")
        modulo.aplicar_compatibilidad(admin_dsn)
        return

    if operacion == "fixtures_empresas":
        # Se invoca el modulo 74 directamente (y no el envoltorio de 75) para
        # conservar la MISMA instancia que genero el password de los usuarios
        # de prueba; el laboratorio necesita conocerlo para el login HTTP.
        # El modulo historico usa setdefault() para protegerse del .env real.
        # En el pipeline, sin embargo, el proceso puede heredar un
        # DATABASE_URL remoto; la siembra debe quedar ligada al destino local
        # ya validado por este runner.
        os.environ["DATABASE_URL"] = admin_dsn
        os.environ["SAAS_AUTH_ENABLED"] = "false"
        os.environ["LEGACY_SURFACE_ENABLED"] = "true"
        os.environ.pop("LEGACY_DATABASE_URL", None)
        modulo = _modulo("database/saas/74_laboratorio_rls_principal_5bk.py",
                         "lab_5bk_5bp")
        ctx["empresas"] = modulo.sembrar_empresas(admin_dsn)
        ctx["password_usuarios"] = modulo.PASSWORD_USUARIOS
        _modulo("database/saas/75_laboratorio_esquema_historico_5bl.py",
                "lab_5bl_5bp")
        return

    raise MigracionError(f"Operacion desconocida: {operacion}")


# ---------------------------------------------------------------------------
# Aplicacion
# ---------------------------------------------------------------------------


def aplicar_lab(manifiesto: dict, destino: Destino, *, reaplicar: bool = False) -> dict:
    import time

    import psycopg

    validar_destino(destino.host, destino.puerto, destino.base)
    validacion = validar_manifiesto(manifiesto, exigir_checksums=True)
    if not validacion["ok"]:
        raise MigracionError(
            "El manifiesto no pasa la validacion estatica:\n- "
            + "\n- ".join(validacion["errores"]))

    admin_dsn = dsn_laboratorio(destino, "postgres")
    ctx = {
        "admin_dsn": admin_dsn,
        "slug_historico": "empresa-a",
        "destino": destino,
    }

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        version = conn.execute("SHOW server_version").fetchone()[0]
        minimo = int(manifiesto.get("motor_esperado", {}).get("version_minima", 17))
        if int(version.split(".")[0]) < minimo:
            raise MigracionError(
                f"El servidor es PostgreSQL {version}; el manifiesto exige >= {minimo}")
        _asegurar_registro(conn)

    aplicadas: list[dict] = []
    omitidas: list[dict] = []

    for fase in planificar(manifiesto, solo_laboratorio=True):
        fase_completa = next(f for f in manifiesto["fases"] if f["id"] == fase["id"])
        huella = checksum_fase(fase_completa, manifiesto)
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            previa = version_aplicada(conn, fase["id"])
        if previa and not reaplicar:
            if previa["checksum"] != huella:
                raise MigracionError(
                    f"{fase['id']}: ya aplicada con otro checksum. No se reintenta "
                    "sobre un destino con historia divergente.")
            omitidas.append({"id": fase["id"], "motivo": "ya aplicada"})
            print(f"[5BP] {fase['id']:<32} SKIP (ya aplicada)", file=sys.stderr,
                  flush=True)
            continue

        inicio = time.monotonic()
        try:
            _ejecutar_operacion(ctx, fase_completa)
        except Exception as exc:  # fail-closed: se detiene en la fase exacta
            raise MigracionError(
                f"Fase {fase['id']} fallo y el pipeline se detuvo: "
                f"{type(exc).__name__}: {exc}") from exc
        duracion = int((time.monotonic() - inicio) * 1000)
        print(f"[5BP] {fase['id']:<32} OK  {duracion:>6} ms", file=sys.stderr,
              flush=True)

        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(
                f"""
                INSERT INTO {TABLA_REGISTRO}
                  (fase, orden, checksum, archivos, duracion_ms, reaplicada)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (fase) DO UPDATE
                  SET checksum = EXCLUDED.checksum,
                      aplicado_en = NOW(),
                      duracion_ms = EXCLUDED.duracion_ms,
                      reaplicada = EXCLUDED.reaplicada
                """,
                (
                    fase["id"], fase["orden"], huella,
                    json.dumps(fase["archivos"]), duracion, bool(previa),
                ),
            )
        aplicadas.append({"id": fase["id"], "duracion_ms": duracion})

    with psycopg.connect(admin_dsn) as conn:
        version_servidor = conn.execute("SHOW server_version").fetchone()[0]
        filas = conn.execute(
            f"SELECT fase, orden, duracion_ms FROM {TABLA_REGISTRO} ORDER BY orden"
        ).fetchall()

    return {
        "destino": {"host": destino.host, "puerto": destino.puerto,
                    "base": destino.base},
        "version_servidor": version_servidor,
        "aplicadas": aplicadas,
        "omitidas": omitidas,
        "registro": [
            {"fase": f[0], "orden": f[1], "duracion_ms": f[2]} for f in filas
        ],
        "contexto": {
            "conteos_historicos": ctx.get("conteos_historicos"),
            "secuencias_ajustadas": ctx.get("secuencias"),
            "empresas": ctx.get("empresas"),
        },
    }


def estado_lab(destino: Destino) -> dict:
    import psycopg

    validar_destino(destino.host, destino.puerto, destino.base)
    with psycopg.connect(dsn_laboratorio(destino, "postgres")) as conn:
        existe = conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL", (TABLA_REGISTRO,)
        ).fetchone()[0]
        if not existe:
            return {"aplicado": False, "versiones": []}
        filas = conn.execute(
            f"SELECT fase, orden, checksum, aplicado_en, reaplicada "
            f"FROM {TABLA_REGISTRO} ORDER BY orden"
        ).fetchall()
    return {
        "aplicado": True,
        "versiones": [
            {"fase": f[0], "orden": f[1], "checksum": f[2],
             "aplicado_en": str(f[3]), "reaplicada": f[4]}
            for f in filas
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resumen_plan(plan: list[dict]) -> str:
    lineas = []
    for fase in plan:
        marca = "LAB" if fase["solo_laboratorio"] else "PROD"
        lineas.append(
            f"[{fase['orden']:>4}] {fase['id']:<32} {marca:<4} "
            f"{fase['tipo']:<14} {', '.join(Path(a).name for a in fase['archivos'])}"
        )
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="5BP: preflight y runner del pipeline de migraciones controladas")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--plan", action="store_true",
                       help="valida y muestra el plan sin conexion (por defecto)")
    grupo.add_argument("--check-local", action="store_true",
                       help="plan + clasificacion completa de supabase/migrations")
    grupo.add_argument("--rehash", action="store_true",
                       help="recalcula los SHA-256 del manifiesto")
    grupo.add_argument("--aplicar-lab", action="store_true",
                       help="aplica el plan en un laboratorio desechable")
    grupo.add_argument("--estado", action="store_true",
                       help="muestra las versiones aplicadas en el laboratorio")
    parser.add_argument("--base", default=None, help="nombre de la base desechable")
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO)
    parser.add_argument("--manifiesto", default=None,
                        help="ruta del manifiesto (por defecto 85 de la Etapa G)")
    parser.add_argument("--confirm-desechable", action="store_true")
    parser.add_argument("--reaplicar-todo", action="store_true",
                        help="ignora el registro para demostrar idempotencia")
    parser.add_argument("--json", action="store_true", help="salida JSON")
    args = parser.parse_args(argv)

    ruta_manifiesto = Path(args.manifiesto) if args.manifiesto else RUTA_MANIFIESTO
    manifiesto = cargar_manifiesto(ruta_manifiesto)

    try:
        if args.rehash:
            calculados = actualizar_checksums(manifiesto)
            ruta_manifiesto.write_text(
                json.dumps(manifiesto, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
            print(f"Manifiesto actualizado: {len(calculados)} archivos con SHA-256")
            for archivo, huella in sorted(calculados.items()):
                print(f"  {huella[:16]}  {archivo}")
            return 0

        if args.aplicar_lab or args.estado:
            if not args.base:
                raise MigracionError("--base es obligatorio en este modo")
            if args.aplicar_lab and not args.confirm_desechable:
                raise MigracionError(
                    "Falta --confirm-desechable: el modo de aplicacion exige "
                    "confirmacion explicita de que el destino es descartable.")
            password = os.environ.get(VARIABLE_PASSWORD_ADMIN, "")
            if not password:
                raise MigracionError(
                    "Falta la contrasena del administrador del laboratorio. "
                    f"Definir {VARIABLE_PASSWORD_ADMIN}.")
            destino = Destino(host=HOST_PERMITIDO, puerto=args.puerto,
                              base=args.base, password=password)
            if args.estado:
                resultado = estado_lab(destino)
                print(json.dumps(resultado, indent=2, ensure_ascii=False, default=str))
                return 0
            resultado = aplicar_lab(manifiesto, destino,
                                    reaplicar=args.reaplicar_todo)
            if args.json:
                print(json.dumps(resultado, indent=2, ensure_ascii=False, default=str))
            else:
                print("Pipeline aplicado en laboratorio")
                print(f"  servidor : PostgreSQL {resultado['version_servidor']}")
                print(f"  destino  : {resultado['destino']['host']}:"
                      f"{resultado['destino']['puerto']}/{resultado['destino']['base']}")
                print(f"  aplicadas: {len(resultado['aplicadas'])}")
                print(f"  omitidas : {len(resultado['omitidas'])}")
                for fila in resultado["registro"]:
                    print(f"    {fila['orden']:>4} {fila['fase']:<32} "
                          f"{fila['duracion_ms']} ms")
            return 0

        validacion = validar_manifiesto(manifiesto, exigir_checksums=True)
        plan = planificar(manifiesto, solo_laboratorio=True)
        if args.json:
            print(json.dumps(
                {"validacion": validacion, "plan": plan},
                indent=2, ensure_ascii=False))
        else:
            print(f"Manifiesto {manifiesto.get('manifiesto_id')} "
                  f"v{manifiesto.get('version')} estado={manifiesto.get('estado')}")
            print(f"Motor esperado: PostgreSQL >= "
                  f"{manifiesto.get('motor_esperado', {}).get('version_minima')}")
            print()
            print(_resumen_plan(plan))
            print()
            clasificacion = validacion["clasificacion"]
            print(f"Migraciones presentes : {len(clasificacion['presentes'])}")
            print(f"  incluidas           : {len(clasificacion['incluidas'])}")
            print(f"  excluidas           : {len(clasificacion['excluidas'])}")
            if validacion["advertencias"]:
                print("\nAdvertencias:")
                for aviso in validacion["advertencias"]:
                    print(f"  - {aviso}")
            if validacion["ok"]:
                print("\nVALIDACION ESTATICA: OK (sin conexion, sin escritura)")
            else:
                print("\nVALIDACION ESTATICA: FALLIDA")
                for error in validacion["errores"]:
                    print(f"  - {error}")
        return 0 if validacion["ok"] else 2

    except MigracionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
