"""5BU / Etapa L - Ensayo B2 con cifrado AUTENTICADO y datos FICTICIOS.

Que demuestra
-------------
1. Que el formato v2 (AES-256-GCM) cifra un `pg_dump -Fc` ficticio y lo
   descifra sin perdida (sha256 plano == sha256 autenticado en cabecera).
2. Que una frase de paso incorrecta, un bit alterado del ciphertext, del tag o
   de la cabecera, un encabezado intercambiado, un archivo truncado o un
   paquete legado (v1/CBC) se rechazan **antes** de crear o tocar la base
   destino: `_restaurar_seguro()` autentica primero y solo despues crea la BD.
3. Que un paquete AUTENTICO restaura en PostgreSQL 17 desechable con cero
   diferencias en objetos, filas, secuencias, RLS, triggers, rutinas y vistas.
4. Que el retorno (restaurar el mismo paquete en otra base) tambien coincide.

Que NO demuestra
----------------
* B2: exige datos reales y autorizacion humana firmada (`104`). Aqui todo es
  ficticio y B2 sigue **NO AUTORIZADO**.
* Aislamiento multi-cluster de produccion: el ensayo usa un cluster NUEVO con
  bases separadas (origen, restore, retorno) dentro del mismo motor desechable.
* Volumen ni tiempos del proyecto real.

Reglas de seguridad
-------------------
* Cluster NUEVO en `fs_lab_5bu_aead`, puerto libre de 55452-55455 (nunca
  55432-55448 ni 5432). No lee ni detiene laboratorios previos.
* Frase de paso en memoria: NO se escribe `.pass`, ni en argumentos, ni en logs.
* Respaldos y temporales fuera de Git; plaintext eliminado tras cada prueba.
* Evidencia nueva con sello unico; no sobrescribe reportes ni evidencias previas.

Uso
---
    python database/saas/111_ensayo_b2_aead_5bu.py --ensayo --confirm-desechable
    python database/saas/111_ensayo_b2_aead_5bu.py --estado
    python database/saas/111_ensayo_b2_aead_5bu.py --limpiar --confirm-desechable
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCIAS = ROOT / "database" / "saas" / "evidencias"
LOCAL = Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
OPENCODE_TMP = LOCAL / "Temp" / "opencode"

PG_BIN_DEFECTO = OPENCODE_TMP / "fs_pg17_5bp" / "pg17" / "pgsql" / "bin"
RAIZ = OPENCODE_TMP / "fs_lab_5bu_aead"
RESPALDOS = RAIZ / "respaldos"
ATAQUES = RAIZ / "ataques"
TEMPORAL = RAIZ / "temporal"

HOST = "127.0.0.1"
BASE_ORIGEN = "fs_5bu_origen_ficticio"
BASE_RESTORE = "fs_5bu_restore"
BASE_RETORNO = "fs_5bu_retorno"
BASE_ATAQUE = "fs_5bu_ataque"

CANDIDATOS_PUERTO = (55452, 55453, 55454, 55455)
PUERTOS_PROHIBIDOS = {5432, 55432, 55433, 55440, 55441, 55442, 55443,
                      55444, 55445, 55446, 55447, 55448, 55449, 55450, 55451}

TABLAS = ("saas.empresa", "public.producto", "public.venta",
          "public.detalle_venta")

MODULO_CIFRADO = ROOT / "database" / "saas" / "110_cifrado_autenticado_b2_5bu.py"


def _cargar_modulo():
    spec = importlib.util.spec_from_file_location("cifrado_b2_5bu", MODULO_CIFRADO)
    modulo = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


aead = _cargar_modulo()


class EnsayoError(RuntimeError):
    pass


def _sello() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as fh:
        for bloque in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def _bin(nombre: str, pg_bin: Path) -> Path:
    exe = pg_bin / f"{nombre}.exe"
    if not exe.exists():
        raise EnsayoError(f"No se encontro {exe}")
    return exe


def _ejecutar(args, *, env=None, check=True, capturar=True):
    res = subprocess.run(
        args,
        stdout=subprocess.PIPE if capturar else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if capturar else subprocess.DEVNULL,
        text=True if capturar else None,
        encoding="utf-8" if capturar else None,
        errors="replace" if capturar else None,
        env={**os.environ, **(env or {})})
    if check and res.returncode != 0:
        detalle = (res.stdout or "") if capturar else ""
        errores = [l for l in detalle.splitlines()
                   if "ERROR" in l or "error:" in l][:4]
        raise EnsayoError(
            f"Fallo {Path(args[0]).name} rc={res.returncode}: "
            + (" | ".join(errores) if errores else detalle[-500:]))
    return res


def _psql(pg_bin: Path, admin: dict, sql: str) -> str:
    env = {"PGHOST": HOST, "PGPORT": str(admin["puerto"]),
           "PGUSER": admin["usuario"], "PGDATABASE": admin["base"],
           "PGPASSWORD": admin["password"], "PGSSLMODE": "disable"}
    res = _ejecutar([str(_bin("psql", pg_bin)), "-v", "ON_ERROR_STOP=1",
                     "-X", "-q", "-t", "-A", "-c", sql], env=env)
    return (res.stdout or "").strip()


def _puerto_libre(puerto: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex((HOST, puerto)) != 0


def _elegir_puerto() -> int:
    for puerto in CANDIDATOS_PUERTO:
        if puerto in PUERTOS_PROHIBIDOS:
            continue
        if _puerto_libre(puerto):
            return puerto
    raise EnsayoError("Ningun puerto libre en 55452-55455")


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------


def _iniciar_cluster(pg_bin: Path, puerto: int, password_admin: str) -> None:
    pgdata = RAIZ / "pgdata"
    pgdata.mkdir(parents=True, exist_ok=True)
    (RAIZ / "DISECHABLE_5BU").write_text(
        "Cluster desechable Etapa 5BU. Seguro de destruir con --limpiar.\n",
        encoding="utf-8")
    pwfile = RAIZ / ".initdb_pw"
    pwfile.write_text(password_admin, encoding="utf-8")
    try:
        _ejecutar([str(_bin("initdb", pg_bin)), "-D", str(pgdata), "-U", "postgres",
                   f"--pwfile={pwfile}", "--auth-local=trust",
                   "--auth-host=scram-sha-256", "--encoding=UTF8"])
    finally:
        pwfile.unlink(missing_ok=True)
    _ejecutar([str(_bin("pg_ctl", pg_bin)), "-D", str(pgdata),
               "-l", str(RAIZ / "postgres.log"),
               "-o", f"-p {puerto} -c listen_addresses={HOST}", "-w", "start"],
              capturar=False)


def _detener_cluster(pg_bin: Path) -> None:
    pgdata = RAIZ / "pgdata"
    if pgdata.exists():
        _ejecutar([str(_bin("pg_ctl", pg_bin)), "-D", str(pgdata),
                   "-w", "-m", "fast", "stop"], check=False, capturar=False)


SEED_SQL = """
CREATE SCHEMA saas;
CREATE TABLE saas.empresa (
  id bigint PRIMARY KEY,
  nombre text NOT NULL
);
CREATE TABLE public.producto (
  id bigserial PRIMARY KEY,
  empresa_id bigint NOT NULL REFERENCES saas.empresa(id),
  nombre text NOT NULL,
  precio numeric(10,2) NOT NULL CHECK (precio >= 0),
  stock integer NOT NULL DEFAULT 0,
  actualizado_en timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.venta (
  id bigserial PRIMARY KEY,
  empresa_id bigint NOT NULL REFERENCES saas.empresa(id),
  total numeric(12,2) NOT NULL
);
CREATE TABLE public.detalle_venta (
  id bigserial PRIMARY KEY,
  venta_id bigint NOT NULL REFERENCES public.venta(id),
  producto_id bigint NOT NULL REFERENCES public.producto(id),
  cantidad integer NOT NULL CHECK (cantidad > 0),
  subtotal numeric(12,2) NOT NULL
);
CREATE FUNCTION public.fn_total_venta(p_venta bigint) RETURNS numeric
  LANGUAGE sql STABLE AS $$
    SELECT COALESCE(SUM(subtotal), 0) FROM public.detalle_venta
     WHERE venta_id = p_venta
$$;
CREATE FUNCTION public.fn_marca_actualizado() RETURNS trigger
  LANGUAGE plpgsql AS $$
  BEGIN
    NEW.actualizado_en := now();
    RETURN NEW;
  END
$$;
CREATE TRIGGER trg_producto_actualizado
  BEFORE UPDATE ON public.producto
  FOR EACH ROW EXECUTE FUNCTION public.fn_marca_actualizado();
CREATE VIEW public.v_producto_empresa AS
  SELECT p.id, p.nombre, e.nombre AS empresa
    FROM public.producto p JOIN saas.empresa e ON e.id = p.empresa_id;
ALTER TABLE public.producto ENABLE ROW LEVEL SECURITY;
CREATE POLICY pol_producto_visible ON public.producto
  USING (true) WITH CHECK (true);
INSERT INTO saas.empresa (id, nombre) VALUES
  (1, 'Empresa Ficticia Alfa'),
  (2, 'Empresa Ficticia Beta');
INSERT INTO public.producto (empresa_id, nombre, precio, stock) VALUES
  (1, 'Camisa ficticia A', 19.90, 10),
  (1, 'Pantalon ficticio A', 29.50, 5),
  (2, 'Blusa ficticia B', 15.00, 7);
INSERT INTO public.venta (empresa_id, total) VALUES (1, 49.40), (2, 15.00);
INSERT INTO public.detalle_venta (venta_id, producto_id, cantidad, subtotal) VALUES
  (1, 1, 1, 19.90),
  (1, 2, 1, 29.50),
  (2, 3, 1, 15.00);
"""


def _inventario(pg_bin: Path, admin: dict) -> dict:
    sql = """
    SELECT json_build_object(
      'version', (SELECT current_setting('server_version')),
      'tablas', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname IN ('public','saas') AND c.relkind='r'),
      'tablas_rls', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname IN ('public','saas') AND c.relkind='r' AND c.relrowsecurity),
      'triggers', (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                 WHERE NOT t.tgisinternal),
      'rutinas', (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                 WHERE n.nspname IN ('public','saas')),
      'vistas', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname IN ('public','saas') AND c.relkind IN ('v','m')),
      'politicas', (SELECT count(*) FROM pg_policies
                    WHERE schemaname IN ('public','saas'))
    )::text
    """
    return json.loads(_psql(pg_bin, admin, sql))


def _filas(pg_bin: Path, admin: dict) -> dict:
    salida = {}
    for tabla in TABLAS:
        salida[tabla] = int(_psql(pg_bin, admin, f"SELECT count(*) FROM {tabla}"))
    return salida


def _secuencias(pg_bin: Path, admin: dict) -> dict:
    sql = ("SELECT json_object_agg(sequencename, COALESCE(last_value,-1)) "
           "FROM pg_sequences WHERE schemaname IN ('public','saas')")
    return json.loads(_psql(pg_bin, admin, sql) or "{}")


def _base_existe(pg_bin: Path, admin: dict, nombre: str) -> bool:
    valor = _psql(pg_bin, admin,
                  f"SELECT count(*) FROM pg_database WHERE datname = '{nombre}'")
    return int(valor) > 0


# ---------------------------------------------------------------------------
# Restauracion segura (autentica antes de tocar la base)
# ---------------------------------------------------------------------------


def _restaurar_seguro(*, archivo: Path, base: str, frase: str, pg_bin: Path,
                      admin: dict) -> dict:
    """Verifica el tag AEAD y SOLO despues crea la base y ejecuta pg_restore."""
    # 1) Autenticidad: no escribe plaintext ni toca PostgreSQL.
    info = aead.verificar(archivo, frase)

    # 2) Destino: base nueva con nombre inequivocamente desechable.
    if not base.startswith("fs_5bu_"):
        raise EnsayoError("nombre de base destino no desechable")
    if _base_existe(pg_bin, admin, base):
        raise EnsayoError(f"la base destino {base} ya existe")
    _psql(pg_bin, admin, f'CREATE DATABASE "{base}" TEMPLATE template0')

    # 3) Descifrado a temporal protegido (se publica solo tras el tag) y restore.
    TEMPORAL.mkdir(parents=True, exist_ok=True)
    temporal = TEMPORAL / f"{base}_{secrets.token_hex(4)}.dump"
    try:
        aead.descifrar(archivo, temporal, frase)
        admin_destino = {**admin, "base": base}
        res = subprocess.run([
            str(_bin("pg_restore", pg_bin)), "--no-password", "--exit-on-error",
            "-h", HOST, "-p", str(admin["puerto"]), "-U", admin["usuario"],
            "-d", base, str(temporal),
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace",
            env={**os.environ, "PGPASSWORD": admin["password"],
                 "PGSSLMODE": "disable"})
        if res.returncode != 0:
            errores = [l for l in (res.stdout or "").splitlines()
                       if "ERROR" in l][:4]
            raise EnsayoError("pg_restore rc=" + str(res.returncode) + ": "
                              + (" | ".join(errores) or "ver salida"))
    finally:
        temporal.unlink(missing_ok=True)

    return {
        "base": base,
        "autenticado": True,
        "bytes_plano": info["bytes_plano"],
        "sha256_plano": info["sha256_plano"],
        "restore_ok": True,
    }


# ---------------------------------------------------------------------------
# Ataques
# ---------------------------------------------------------------------------


def _partes(blob: bytes):
    largo = struct.unpack(">I", blob[10:14])[0]
    cab = json.loads(blob[14:14 + largo].decode("utf-8"))
    return blob[:14 + largo], cab, blob[14 + largo:]


def _reensamblar(cab: dict, resto: bytes) -> bytes:
    crudo = aead._json_canonico(cab)
    return (b"FSB2AEAD" + bytes([2, 1]) + struct.pack(">I", len(crudo))
            + crudo + resto)


def _paquetes_ataque(paquete_a: Path, paquete_b: Path) -> dict:
    ATAQUES.mkdir(parents=True, exist_ok=True)
    blob_a = paquete_a.read_bytes()
    prefijo_a, cab_a, resto_a = _partes(blob_a)
    _prefijo_b, _cab_b, resto_b = _partes(paquete_b.read_bytes())

    def escribir(nombre: str, datos: bytes) -> Path:
        ruta = ATAQUES / f"{nombre}.fsb2"
        ruta.write_bytes(datos)
        return ruta

    flip_ct = bytearray(blob_a)
    flip_ct[len(flip_ct) - 100] ^= 0x01

    flip_tag = bytearray(blob_a)
    flip_tag[-1] ^= 0x80

    cab_iter = dict(cab_a)
    cab_iter["iteraciones"] = 200_001

    return {
        "frase_incorrecta": paquete_a,
        "ciphertext_bit_flip": escribir("ciphertext_bit_flip", bytes(flip_ct)),
        "tag_bit_flip": escribir("tag_bit_flip", bytes(flip_tag)),
        "cabecera_iteraciones": escribir("cabecera_iteraciones",
                                         _reensamblar(cab_iter, resto_a)),
        "cabecera_intercambiada": escribir("cabecera_intercambiada",
                                           prefijo_a + resto_b),
        "truncado": escribir("truncado", blob_a[:-10]),
        "formato_legado_v1": escribir("formato_legado_v1",
                                      b"Salted__" + b"\x00" * 96),
        "firma_desconocida": escribir("firma_desconocida",
                                      b"NOESUNRESPALDO" + b"\x00" * 64),
    }


def _esperado(nombre: str) -> str:
    return ("FormatoNoSoportado" if nombre in {"formato_legado_v1",
                                               "firma_desconocida"}
            else "AutenticacionFallida")


# ---------------------------------------------------------------------------
# Ensayo
# ---------------------------------------------------------------------------


def ensayo(pg_bin: Path) -> dict:
    inicio = time.time()
    if not (pg_bin / "initdb.exe").exists():
        raise EnsayoError(f"No se encontro PostgreSQL 17 en {pg_bin}")
    if RAIZ.exists() and any(RAIZ.iterdir()):
        raise EnsayoError(f"{RAIZ} no esta vacio: use --limpiar antes de reensayar")

    puerto = _elegir_puerto()
    password_admin = secrets.token_urlsafe(24)
    for carpeta in (RESPALDOS, ATAQUES, TEMPORAL):
        carpeta.mkdir(parents=True, exist_ok=True)

    _iniciar_cluster(pg_bin, puerto, password_admin)
    admin = {"usuario": "postgres", "password": password_admin,
             "base": "postgres", "puerto": puerto}

    version_motor = _psql(pg_bin, admin, "SHOW server_version")
    if not version_motor.startswith("17."):
        raise EnsayoError(f"se exige PostgreSQL 17.x; encontrado {version_motor}")

    # 1) Origen ficticio
    _psql(pg_bin, admin, f'CREATE DATABASE "{BASE_ORIGEN}" TEMPLATE template0')
    origen_admin = {**admin, "base": BASE_ORIGEN}
    sql_seed = RAIZ / "semilla_ficticia_5bu.sql"
    sql_seed.write_text(SEED_SQL, encoding="utf-8")
    _ejecutar([str(_bin("psql", pg_bin)), "-v", "ON_ERROR_STOP=1", "-X", "-q",
               "-f", str(sql_seed)],
              env={"PGPASSWORD": password_admin, "PGHOST": HOST,
                   "PGPORT": str(puerto), "PGUSER": "postgres",
                   "PGDATABASE": BASE_ORIGEN, "PGSSLMODE": "disable"})

    inv_origen = _inventario(pg_bin, origen_admin)
    filas_origen = _filas(pg_bin, origen_admin)
    seq_origen = _secuencias(pg_bin, origen_admin)

    # 2) Dos dumps -Fc (el segundo sirve para el ataque de cabecera intercambiada)
    sello = _sello()
    dump_a = RAIZ / f"origen_{sello}_a.dump"
    dump_b = RAIZ / f"origen_{sello}_b.dump"
    env_dump = {"PGPASSWORD": password_admin, "PGSSLMODE": "disable"}
    for destino in (dump_a, dump_b):
        _ejecutar([str(_bin("pg_dump", pg_bin)), "-Fc", "-Z", "6", "--no-password",
                   "-h", HOST, "-p", str(puerto), "-U", "postgres",
                   "-d", BASE_ORIGEN, "-f", str(destino)], env=env_dump)

    # 3) Cifrado AEAD v2 (frase en memoria, nunca en disco ni argv)
    frase = secrets.token_urlsafe(32)
    paquete_a = RESPALDOS / f"ficticio_{sello}_a.fsb2"
    paquete_b = RESPALDOS / f"ficticio_{sello}_b.fsb2"
    info_cifrado = aead.cifrar_archivo(dump_a, paquete_a, frase,
                                       metadatos={"etapa": "5BU",
                                                  "datos": "ficticios"})
    aead.cifrar_archivo(dump_b, paquete_b, frase,
                        metadatos={"etapa": "5BU", "datos": "ficticios"})
    sha_dump = _sha256(dump_a)

    # 4) Ataques: todos deben abortar ANTES de crear la base destino
    rutas_ataque = _paquetes_ataque(paquete_a, paquete_b)
    ataques = []
    for indice, (nombre, ruta) in enumerate(rutas_ataque.items(), start=1):
        base = f"{BASE_ATAQUE}_{indice}"
        frase_usada = (secrets.token_urlsafe(32) if nombre == "frase_incorrecta"
                       else frase)
        detectado = False
        excepcion = None
        mensaje = ""
        try:
            _restaurar_seguro(archivo=ruta, base=base, frase=frase_usada,
                              pg_bin=pg_bin, admin=admin)
        except aead.CifradoError as exc:
            detectado = True
            excepcion = type(exc).__name__
            mensaje = str(exc)[:200]
        base_creada = _base_existe(pg_bin, admin, base)
        residual = sorted(p.name for p in TEMPORAL.glob("*.dump"))
        ataques.append({
            "ataque": nombre,
            "esperado": _esperado(nombre),
            "detectado": detectado,
            "excepcion": excepcion,
            "mensaje": mensaje,
            "base_destino_creada": base_creada,
            "plaintext_residual": residual,
        })

    # 5) Restauracion autentica
    res_restore = _restaurar_seguro(archivo=paquete_a, base=BASE_RESTORE,
                                    frase=frase, pg_bin=pg_bin, admin=admin)
    restore_admin = {**admin, "base": BASE_RESTORE}
    inv_restore = _inventario(pg_bin, restore_admin)
    filas_restore = _filas(pg_bin, restore_admin)
    seq_restore = _secuencias(pg_bin, restore_admin)

    # 6) Retorno: mismo paquete en otra base
    _restaurar_seguro(archivo=paquete_a, base=BASE_RETORNO, frase=frase,
                      pg_bin=pg_bin, admin=admin)
    retorno_admin = {**admin, "base": BASE_RETORNO}
    inv_retorno = _inventario(pg_bin, retorno_admin)
    filas_retorno = _filas(pg_bin, retorno_admin)

    # 7) Diferencias
    claves = ("tablas", "tablas_rls", "triggers", "rutinas", "vistas", "politicas")
    dif_inv = {k: {"origen": inv_origen[k], "restaurado": inv_restore[k]}
               for k in claves if inv_origen[k] != inv_restore[k]}
    dif_filas = {t: {"origen": filas_origen[t], "restaurado": filas_restore[t]}
                 for t in TABLAS if filas_origen[t] != filas_restore[t]}
    dif_seq = {k: {"origen": seq_origen.get(k), "restaurado": seq_restore.get(k)}
               for k in set(seq_origen) | set(seq_restore)
               if seq_origen.get(k) != seq_restore.get(k)}
    dif_retorno_inv = {k: {"origen": inv_origen[k], "retorno": inv_retorno[k]}
                       for k in claves if inv_origen[k] != inv_retorno[k]}
    dif_retorno_filas = {t: {"origen": filas_origen[t], "retorno": filas_retorno[t]}
                         for t in TABLAS if filas_origen[t] != filas_retorno[t]}

    # 8) Higiene
    plaintext_final = sorted(p.name for p in TEMPORAL.glob("*"))
    passfiles = sorted(p.name for p in RAIZ.rglob("*.pass"))
    for base in (BASE_RESTORE, BASE_RETORNO):
        _psql(pg_bin, admin, f'DROP DATABASE IF EXISTS "{base}"')
    for base in [f"{BASE_ATAQUE}_{i}" for i in range(1, len(rutas_ataque) + 1)]:
        if _base_existe(pg_bin, admin, base):
            _psql(pg_bin, admin, f'DROP DATABASE IF EXISTS "{base}"')
    for dump in (dump_a, dump_b):
        dump.unlink(missing_ok=True)

    evidencia = {
        "etapa": "5BU / Etapa L - cifrado autenticado B2 con datos FICTICIOS",
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duracion_segundos": round(time.time() - inicio, 1),
        "b2_estado": "NO AUTORIZADO - instrumento tecnicamente preparado",
        "entorno": {
            "motor": f"PostgreSQL {version_motor}",
            "cifrado": f"v2 / {info_cifrado['suite']} + {info_cifrado['kdf']}",
            "cryptography": aead.estado()["cryptography"],
            "iteraciones": info_cifrado["iteraciones"],
            "cluster": str(RAIZ / "pgdata"),
            "puerto": puerto,
            "marcador_desechable": (RAIZ / "DISECHABLE_5BU").exists(),
            "datos": "100% ficticios (semilla local, sin PII)",
        },
        "respaldo": {
            "archivo": str(paquete_a),
            "formato": "FSB2AEAD v2 / AES-256-GCM (tag 128 bits)",
            "bytes_plano": info_cifrado["bytes_plano"],
            "bytes_cifrado": info_cifrado["bytes_cifrado"],
            "sha256_plano": sha_dump,
            "sha256_cifrado": info_cifrado["sha256_cifrado"],
            "sha256_coincide_con_dump": sha_dump == info_cifrado["sha256_plano"],
            "cabecera_autenticada_aad": True,
            "fallback_cbc": False,
            "contrasena_en_disco": False,
        },
        "ataques": ataques,
        "ataques_detectados": sum(1 for a in ataques if a["detectado"]),
        "ataques_total": len(ataques),
        "bases_creadas_por_ataques": sum(
            1 for a in ataques if a["base_destino_creada"]),
        "restauracion": {
            "ok": not dif_inv and not dif_filas and not dif_seq,
            "autenticado": res_restore["autenticado"],
            "inventario_origen": inv_origen,
            "inventario_restaurado": inv_restore,
            "filas_origen": filas_origen,
            "filas_restaurado": filas_restore,
            "diferencias_objetos": dif_inv,
            "diferencias_filas": dif_filas,
            "diferencias_secuencias": dif_seq,
            "secuencias_origen": seq_origen,
            "secuencias_restaurado": seq_restore,
        },
        "retorno": {
            "ok": not dif_retorno_inv and not dif_retorno_filas,
            "diferencias_objetos": dif_retorno_inv,
            "diferencias_filas": dif_retorno_filas,
        },
        "higiene": {
            "plaintext_residual": plaintext_final,
            "passfiles_en_disco": passfiles,
            "dumps_planos_eliminados": True,
            "fuera_de_git": True,
        },
        "no_demuestra": [
            "B2 real: exige datos reales y autorizacion firmada",
            "volumen ni tiempos del proyecto Supabase",
            "aislamiento de produccion (el ensayo usa un cluster desechable)",
        ],
    }

    ruta_json = EVIDENCIAS / f"EVIDENCIA_B2_AEAD_5BU_{sello}.json"
    ruta_txt = EVIDENCIAS / f"EVIDENCIA_B2_AEAD_5BU_{sello}.txt"
    ruta_json.write_text(json.dumps(evidencia, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    ruta_txt.write_text(_texto(evidencia, ruta_json), encoding="utf-8")
    evidencia["evidencias"] = {"json": str(ruta_json), "texto": str(ruta_txt)}

    _detener_cluster(pg_bin)
    return evidencia


def _texto(ev: dict, ruta_json: Path) -> str:
    lineas = [
        "EVIDENCIA 5BU - CIFRADO AUTENTICADO B2 (DATOS FICTICIOS)",
        f"Fecha UTC: {ev['fecha_utc']}",
        f"Motor: {ev['entorno']['motor']} en 127.0.0.1:{ev['entorno']['puerto']}",
        f"Cifrado: {ev['entorno']['cifrado']} - cryptography {ev['entorno']['cryptography']}",
        f"Iteraciones PBKDF2: {ev['entorno']['iteraciones']}",
        "Sin secretos: la frase de paso vive solo en memoria; no hay .pass ni DSN.",
        "-" * 78,
        f"Respaldo plano   : {ev['respaldo']['bytes_plano']} bytes",
        f"Respaldo cifrado : {ev['respaldo']['bytes_cifrado']} bytes",
        f"SHA-256 plano    : {ev['respaldo']['sha256_plano']}",
        f"SHA-256 cifrado  : {ev['respaldo']['sha256_cifrado']}",
        f"Cabecera AAD     : {ev['respaldo']['cabecera_autenticada_aad']}",
        "-" * 78,
        f"Ataques detectados: {ev['ataques_detectados']}/{ev['ataques_total']}",
        f"Bases creadas por ataques: {ev['bases_creadas_por_ataques']}",
    ]
    for a in ev["ataques"]:
        lineas.append(
            f"  - {a['ataque']:<24} detectado={a['detectado']} "
            f"excepcion={a['excepcion']} base_creada={a['base_destino_creada']}")
    lineas += [
        "-" * 78,
        f"Restauracion: {'OK' if ev['restauracion']['ok'] else 'CON DIFERENCIAS'}",
        f"  objetos origen/restaurado: {ev['restauracion']['inventario_origen']} / "
        f"{ev['restauracion']['inventario_restaurado']}",
        f"  filas origen/restaurado  : {ev['restauracion']['filas_origen']} / "
        f"{ev['restauracion']['filas_restaurado']}",
        f"  diferencias              : objetos={ev['restauracion']['diferencias_objetos'] or 'ninguna'} "
        f"filas={ev['restauracion']['diferencias_filas'] or 'ninguna'} "
        f"secuencias={ev['restauracion']['diferencias_secuencias'] or 'ninguna'}",
        f"Retorno: {'OK' if ev['retorno']['ok'] else 'CON DIFERENCIAS'}",
        f"Plaintext residual: {ev['higiene']['plaintext_residual'] or 'ninguno'}",
        f"Passfiles en disco: {ev['higiene']['passfiles_en_disco'] or 'ninguno'}",
        "-" * 78,
        "ESTADO B2: NO AUTORIZADO. Instrumento tecnicamente preparado con datos",
        "ficticios. La extraccion real exige autorizacion humana firmada (104).",
        f"JSON: {ruta_json}",
    ]
    return "\n".join(lineas) + "\n"


def estado() -> dict:
    pgdata = RAIZ / "pgdata"
    return {
        "cluster": str(RAIZ),
        "pgdata_existe": pgdata.exists(),
        "puertos_libres": [p for p in CANDIDATOS_PUERTO
                           if p not in PUERTOS_PROHIBIDOS and _puerto_libre(p)],
        "respaldos": ([p.name for p in RESPALDOS.iterdir()]
                      if RESPALDOS.exists() else []),
        "pg_bin": str(PG_BIN_DEFECTO),
        "pg_bin_presente": (PG_BIN_DEFECTO / "initdb.exe").exists(),
    }


def limpiar(pg_bin: Path | None) -> dict:
    borrados = []
    if pg_bin and (RAIZ / "pgdata").exists():
        _detener_cluster(pg_bin)
    for ruta in (TEMPORAL, ATAQUES, RESPALDOS):
        if ruta.exists():
            shutil.rmtree(ruta, ignore_errors=True)
            borrados.append(str(ruta))
    if RAIZ.exists():
        shutil.rmtree(RAIZ, ignore_errors=True)
        borrados.append(str(RAIZ))
    return {"borrados": borrados}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ensayo", action="store_true")
    parser.add_argument("--estado", action="store_true")
    parser.add_argument("--limpiar", action="store_true")
    parser.add_argument("--confirm-desechable", action="store_true")
    parser.add_argument("--pg-bin", default=str(PG_BIN_DEFECTO))
    args = parser.parse_args(argv)
    pg_bin = Path(args.pg_bin)
    if args.estado:
        print(json.dumps(estado(), indent=2, ensure_ascii=False))
        return 0
    if args.limpiar:
        if not args.confirm_desechable:
            print("Falta --confirm-desechable", file=sys.stderr)
            return 2
        print(json.dumps(limpiar(pg_bin), indent=2, ensure_ascii=False))
        return 0
    if not args.ensayo:
        parser.print_help()
        return 2
    if not args.confirm_desechable:
        print("Falta --confirm-desechable: crea un cluster PostgreSQL 17 completo",
              file=sys.stderr)
        return 2
    ev = ensayo(pg_bin)
    print(json.dumps({
        "ataques_detectados": ev["ataques_detectados"],
        "ataques_total": ev["ataques_total"],
        "bases_creadas_por_ataques": ev["bases_creadas_por_ataques"],
        "restauracion_ok": ev["restauracion"]["ok"],
        "retorno_ok": ev["retorno"]["ok"],
        "plaintext_residual": ev["higiene"]["plaintext_residual"],
        "sha256_cifrado": ev["respaldo"]["sha256_cifrado"],
        "evidencias": ev["evidencias"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
