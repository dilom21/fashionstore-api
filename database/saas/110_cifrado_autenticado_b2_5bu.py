"""5BU / Etapa L - Cifrado autenticado (AEAD) para respaldos B2.

Que corrige
-----------
La Etapa J cifro el respaldo sintetico con AES-256-CBC + PBKDF2 y publico un
SHA-256 del archivo cifrado. Ese diseno **no autentica** el paquete: quien pueda
alterar el .enc puede recalcular el hash externo. Este modulo implementa el
formato **v2** con AES-256-GCM, donde el tag de 128 bits se verifica ANTES de
que ningun byte llegue a `pg_restore`.

Formato v2 (`FSB2AEAD`)
-----------------------
    magic       8 bytes  b"FSB2AEAD"
    version     1 byte   0x02
    suite       1 byte   0x01 = AES-256-GCM
    len_header  4 bytes  uint32 big-endian
    header      N bytes  JSON canonico UTF-8  -> AUTENTICADO como AAD
    ciphertext  M bytes  AES-256-GCM
    tag        16 bytes  tag GCM (autentica cabecera + ciphertext)

La cabecera incluye `suite`, `kdf`, `iteraciones`, `salt_b64`, `nonce_b64`,
`bytes_plano`, `sha256_plano`, `fecha_utc` y metadatos. Todo el bloque
magic+version+suite+len+header se pasa como *associated data*, por lo que
cambiar una iteracion, el salt, el nonce o el tamano invalida el tag.

Propiedades de seguridad
------------------------
* KDF: PBKDF2-HMAC-SHA256, 600 000 iteraciones por defecto (minimo aceptado
  200 000), salt aleatorio de 16 bytes por archivo.
* Nonce de 96 bits aleatorio por archivo; clave derivada por archivo.
* Cifrado en streaming (bloques de 1 MiB): no carga el volcado completo en RAM.
* Descifrado en dos fases: `verificar()` autentica descartando el plaintext;
  `descifrar()` escribe a un temporal protegido y solo lo publica tras el tag.
* Sin fallback a CBC: cualquier archivo sin la firma v2 se rechaza explicitamente.
* La frase de paso nunca se acepta por argumento de proceso ni se escribe en
  logs, evidencias o nombres de archivo.

Uso
---
    python database/saas/110_cifrado_autenticado_b2_5bu.py estado
    python database/saas/110_cifrado_autenticado_b2_5bu.py cifrar base.dump base.dump.fsb2
    python database/saas/110_cifrado_autenticado_b2_5bu.py verificar base.dump.fsb2
    python database/saas/110_cifrado_autenticado_b2_5bu.py descifrar base.dump.fsb2 base.dump
    python database/saas/110_cifrado_autenticado_b2_5bu.py preflight \
        --origen-host <host> --origen-puerto 5432 --destino <carpeta> \
        --puerto-restore 55452 --autorizacion database/saas/104_solicitud_autorizacion_datos_5bs.md

Sin `--passfile`, la frase se pide de forma interactiva (`getpass`).
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import socket
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

try:  # dependencia declarada en requirements.txt (cryptography==46.0.3)
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError as exc:  # pragma: no cover - falla cerrado y explicita
    raise SystemExit(
        "Falta la dependencia 'cryptography' (AEAD). "
        "Instale la version fijada en requirements.txt: cryptography==46.0.3"
    ) from exc

ROOT = Path(__file__).resolve().parents[2]

MAGIC = b"FSB2AEAD"
VERSION = 2
SUITE_AES_256_GCM = 1
SUITES = {SUITE_AES_256_GCM: "AES-256-GCM"}
KDF_NOMBRE = "PBKDF2-HMAC-SHA256"
ITERACIONES_DEFECTO = 600_000
ITERACIONES_MINIMAS = 200_000
SALT_BYTES = 16
NONCE_BYTES = 12
TAG_BYTES = 16
BLOQUE = 1024 * 1024
LEN_HEADER_MAX = 64 * 1024
FRASE_MINIMA = 16

CABECERA_MAGICA = MAGIC + bytes([VERSION, SUITE_AES_256_GCM])
PUERTOS_PROTEGIDOS = {5432, 55432, 55433, 55440, 55441, 55442, 55443,
                      55444, 55445, 55446, 55447, 55448}
CARPETAS_SINCRONIZADAS = ("onedrive", "dropbox", "google drive", "googledrive",
                          "icloud", "sharepoint")


class CifradoError(RuntimeError):
    """Error de cifrado/descifrado con mensaje apto para el operador."""


class FormatoNoSoportado(CifradoError):
    """El archivo no es un respaldo B2 v2 (p. ej. legado CBC)."""


class AutenticacionFallida(CifradoError):
    """Tag GCM invalido: frase incorrecta o archivo alterado."""


class DestinoExistente(CifradoError):
    """Se niega a sobrescribir una salida existente."""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _frase(passphrase: str | bytes) -> bytes:
    if isinstance(passphrase, bytes):
        material = passphrase
    else:
        material = str(passphrase).encode("utf-8")
    if len(material) < FRASE_MINIMA:
        raise CifradoError(
            f"La frase de paso debe tener al menos {FRASE_MINIMA} bytes")
    return material


def derivar_clave(passphrase: str | bytes, salt: bytes,
                  iteraciones: int = ITERACIONES_DEFECTO) -> bytes:
    """Deriva 32 bytes con PBKDF2-HMAC-SHA256 y parametros explicitos."""
    if iteraciones < ITERACIONES_MINIMAS:
        raise CifradoError(
            f"iteraciones insuficientes: minimo {ITERACIONES_MINIMAS}")
    if len(salt) < SALT_BYTES:
        raise CifradoError("salt demasiado corto")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=iteraciones)
    return kdf.derive(_frase(passphrase))


def _b64e(datos: bytes) -> str:
    return base64.b64encode(datos).decode("ascii")


def _b64d(texto: str) -> bytes:
    try:
        return base64.b64decode(texto.encode("ascii"), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise FormatoNoSoportado("cabecera con base64 invalido") from exc


def _json_canonico(datos: dict) -> bytes:
    return json.dumps(datos, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as fh:
        for bloque in iter(lambda: fh.read(BLOQUE), b""):
            h.update(bloque)
    return h.hexdigest()


def _sha256_bytes(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def _permisos_restringidos(ruta: Path) -> bool:
    """ACL best-effort: solo el usuario actual. Nunca falla el flujo."""
    usuario = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not usuario:
        return False
    try:
        import subprocess
        res = subprocess.run(
            ["icacls", str(ruta), "/inheritance:r", "/grant:r", f"{usuario}:F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _puerto_libre(host: str, puerto: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex((host, puerto)) != 0


# ---------------------------------------------------------------------------
# Cabecera
# ---------------------------------------------------------------------------


def _leer_cabecera(fh) -> tuple[dict, bytes]:
    """Lee y valida la cabecera v2. Devuelve (cabecera, aad)."""
    cabecera_magica = fh.read(len(MAGIC))
    if cabecera_magica != MAGIC:
        if (cabecera_magica.startswith(b"FSB2")
                or cabecera_magica.startswith(b"Salted__")):
            raise FormatoNoSoportado(
                "Respaldo en formato legado (v1 / AES-256-CBC) no soportado. "
                "No se permite fallback silencioso: recifre con el formato v2.")
        raise FormatoNoSoportado(
            "Firma de archivo desconocida: no es un respaldo FashionStore B2 v2.")
    version = fh.read(1)
    suite = fh.read(1)
    if len(version) != 1 or len(suite) != 1:
        raise FormatoNoSoportado("archivo truncado en la cabecera")
    if version[0] != VERSION:
        raise FormatoNoSoportado(
            f"version de formato {version[0]} no soportada (se exige v{VERSION})")
    if suite[0] not in SUITES:
        raise FormatoNoSoportado(f"suite {suite[0]} desconocida")
    crudo_largo = fh.read(4)
    if len(crudo_largo) != 4:
        raise FormatoNoSoportado("archivo truncado en el tamano de cabecera")
    largo = struct.unpack(">I", crudo_largo)[0]
    if largo <= 0 or largo > LEN_HEADER_MAX:
        raise FormatoNoSoportado("tamano de cabecera fuera de rango")
    crudo_cab = fh.read(largo)
    if len(crudo_cab) != largo:
        raise FormatoNoSoportado("cabecera incompleta")
    try:
        cab = json.loads(crudo_cab.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise FormatoNoSoportado("cabecera JSON ilegible") from exc
    _validar_cabecera(cab, suite[0])
    aad = MAGIC + version + suite + crudo_largo + crudo_cab
    return cab, aad


def _validar_cabecera(cab: dict, suite: int) -> None:
    if cab.get("formato") != "FashionStore-B2-AEAD":
        raise FormatoNoSoportado("cabecera sin identificador de formato esperado")
    if int(cab.get("version", -1)) != VERSION:
        raise FormatoNoSoportado("cabecera con version inconsistente")
    if cab.get("suite") != SUITES[suite]:
        raise FormatoNoSoportado("cabecera y suite del contenedor no coinciden")
    if cab.get("kdf") != KDF_NOMBRE:
        raise FormatoNoSoportado("KDF no soportado")
    if int(cab.get("iteraciones", 0)) < ITERACIONES_MINIMAS:
        raise FormatoNoSoportado("iteraciones por debajo del minimo aceptado")
    salt = _b64d(cab.get("salt_b64", ""))
    nonce = _b64d(cab.get("nonce_b64", ""))
    if len(salt) < SALT_BYTES:
        raise FormatoNoSoportado("salt invalido")
    if len(nonce) != NONCE_BYTES:
        raise FormatoNoSoportado("nonce invalido")
    if not re.fullmatch(r"[0-9a-f]{64}", str(cab.get("sha256_plano", ""))):
        raise FormatoNoSoportado("sha256_plano invalido")
    if int(cab.get("bytes_plano", -1)) < 0:
        raise FormatoNoSoportado("bytes_plano invalido")


# ---------------------------------------------------------------------------
# Cifrado
# ---------------------------------------------------------------------------


def cifrar_archivo(origen: Path | str, destino: Path | str,
                   passphrase: str | bytes, *,
                   iteraciones: int = ITERACIONES_DEFECTO,
                   metadatos: dict | None = None) -> dict:
    """Cifra `origen` a `destino` con AES-256-GCM y cabecera autenticada."""
    origen = Path(origen)
    destino = Path(destino)
    if not origen.is_file():
        raise CifradoError(f"no existe el archivo de entrada: {origen.name}")
    if destino.exists():
        raise DestinoExistente(f"la salida ya existe: {destino.name}")
    frase = _frase(passphrase)

    tamano = origen.stat().st_size
    sha_plano = _sha256_archivo(origen)
    salt = secrets.token_bytes(SALT_BYTES)
    nonce = secrets.token_bytes(NONCE_BYTES)
    clave = derivar_clave(frase, salt, iteraciones)
    cab = {
        "formato": "FashionStore-B2-AEAD",
        "version": VERSION,
        "suite": SUITES[SUITE_AES_256_GCM],
        "kdf": KDF_NOMBRE,
        "iteraciones": int(iteraciones),
        "salt_b64": _b64e(salt),
        "nonce_b64": _b64e(nonce),
        "bytes_plano": tamano,
        "sha256_plano": sha_plano,
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nombre_original": origen.name,
        "metadatos": metadatos or {},
    }
    crudo_cab = _json_canonico(cab)
    if len(crudo_cab) > LEN_HEADER_MAX:
        raise CifradoError("cabecera demasiado grande (reduzca metadatos)")
    aad = (MAGIC + bytes([VERSION, SUITE_AES_256_GCM])
           + struct.pack(">I", len(crudo_cab)) + crudo_cab)

    cifrador = Cipher(algorithms.AES(clave), modes.GCM(nonce)).encryptor()
    cifrador.authenticate_additional_data(aad)

    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(f".{destino.name}.{secrets.token_hex(4)}.tmp")
    try:
        with origen.open("rb") as fin, temporal.open("wb") as fout:
            fout.write(aad)
            for bloque in iter(lambda: fin.read(BLOQUE), b""):
                fout.write(cifrador.update(bloque))
            fout.write(cifrador.finalize())
            fout.write(cifrador.tag)
        os.replace(temporal, destino)
        _permisos_restringidos(destino)
    except BaseException:
        temporal.unlink(missing_ok=True)
        raise

    return {
        "archivo": str(destino),
        "formato": f"v{VERSION}",
        "suite": SUITES[SUITE_AES_256_GCM],
        "kdf": KDF_NOMBRE,
        "iteraciones": int(iteraciones),
        "bytes_plano": tamano,
        "bytes_cifrado": destino.stat().st_size,
        "sha256_plano": sha_plano,
        "sha256_cifrado": _sha256_archivo(destino),
        "nonce_reutilizado": False,
    }


# ---------------------------------------------------------------------------
# Descifrado / verificacion
# ---------------------------------------------------------------------------


def _procesar_autenticado(ruta: Path, passphrase: str | bytes, consumir) -> dict:
    """Descifra en streaming y entrega bloques SOLO si el tag es valido.

    `consumir` recibe cada bloque de plaintext. Si el tag GCM falla, se lanza
    `AutenticacionFallida` y el llamador debe descartar lo escrito.
    """
    ruta = Path(ruta)
    if not ruta.is_file():
        raise CifradoError(f"no existe el respaldo cifrado: {ruta.name}")
    total = ruta.stat().st_size
    with ruta.open("rb") as fh:
        cab, aad = _leer_cabecera(fh)
        inicio = fh.tell()
        largo_ct = total - inicio - TAG_BYTES
        if largo_ct < 0:
            raise FormatoNoSoportado("archivo truncado: falta el tag de autenticidad")
        fh.seek(total - TAG_BYTES)
        tag = fh.read(TAG_BYTES)
        if len(tag) != TAG_BYTES:
            raise FormatoNoSoportado("tag de autenticidad incompleto")
        fh.seek(inicio)

        clave = derivar_clave(passphrase, _b64d(cab["salt_b64"]),
                              int(cab["iteraciones"]))
        descifrador = Cipher(
            algorithms.AES(clave),
            modes.GCM(_b64d(cab["nonce_b64"]), tag)).decryptor()
        descifrador.authenticate_additional_data(aad)

        digest = hashlib.sha256()
        restante = largo_ct
        try:
            while restante > 0:
                bloque = fh.read(min(BLOQUE, restante))
                if not bloque:
                    raise FormatoNoSoportado("archivo truncado en el ciphertext")
                restante -= len(bloque)
                plano = descifrador.update(bloque)
                if plano:
                    digest.update(plano)
                    consumir(plano)
            cola = descifrador.finalize()
        except InvalidTag:
            raise AutenticacionFallida(
                "Autenticacion fallida: frase de paso incorrecta o archivo "
                "alterado (tag AES-256-GCM invalido). No se restauro nada."
            ) from None
        if cola:
            digest.update(cola)
            consumir(cola)

    if digest.hexdigest() != cab["sha256_plano"]:
        raise AutenticacionFallida(
            "Autenticacion fallida: el contenido no coincide con el hash "
            "autenticado de la cabecera.")
    return {
        "cabecera": {k: cab[k] for k in ("version", "suite", "kdf", "iteraciones",
                                         "bytes_plano", "sha256_plano",
                                         "fecha_utc")},
        "bytes_plano": cab["bytes_plano"],
        "sha256_plano": digest.hexdigest(),
    }


def verificar(ruta: Path | str, passphrase: str | bytes) -> dict:
    """Autentica el paquete descartando el plaintext (no escribe nada)."""
    return _procesar_autenticado(Path(ruta), passphrase, lambda _bloque: None)


def descifrar(ruta: Path | str, salida: Path | str, passphrase: str | bytes, *,
              permitir_sobrescribir: bool = False) -> dict:
    """Descifra a `salida` publicando el archivo SOLO tras validar el tag."""
    ruta = Path(ruta)
    salida = Path(salida)
    if salida.exists() and not permitir_sobrescribir:
        raise DestinoExistente(f"la salida ya existe: {salida.name}")
    salida.parent.mkdir(parents=True, exist_ok=True)
    temporal = salida.with_name(f".{salida.name}.{secrets.token_hex(4)}.tmp")
    try:
        with temporal.open("wb") as fout:
            info = _procesar_autenticado(ruta, passphrase, fout.write)
        os.replace(temporal, salida)
        _permisos_restringidos(salida)
    except BaseException:
        temporal.unlink(missing_ok=True)
        raise
    info["archivo"] = str(salida)
    return info


# ---------------------------------------------------------------------------
# Preflight PRE-B2 (fail-closed)
# ---------------------------------------------------------------------------


def _autorizacion_rellena(texto: str) -> tuple[bool, list[str]]:
    faltan = []
    autorizo = re.search(r"Autorizo\s*/\s*No autorizo\s*:\s*(.+)", texto)
    if not autorizo or "<" in autorizo.group(1):
        faltan.append("linea 'Autorizo / No autorizo:' sin rellenar")
    firma = re.search(r"Firma\s*/\s*fecha\s*:\s*(.+)", texto)
    if not firma or "<" in firma.group(1):
        faltan.append("linea 'Firma / fecha:' sin rellenar")
    for campo in ("Alcance autorizado", "Custodio del respaldo", "TTL del respaldo"):
        m = re.search(campo + r"\s*:\s*(.+)", texto)
        if not m or "<" in m.group(1):
            faltan.append(f"campo '{campo}' sin rellenar")
    return (not faltan), faltan


def preflight_preb2(*, origen_host: str, origen_puerto: int, destino: Path | str,
                    puerto_restore: int, autorizacion: Path | str,
                    formato: str = "v2") -> dict:
    """Puerta tecnica previa: impide iniciar una extraccion real mal preparada."""
    bloqueos: list[str] = []
    advertencias: list[str] = []

    if formato.lower() not in {"v2", "fsb2aead"}:
        bloqueos.append("formato distinto de v2: el esquema CBC legado no se usa "
                        "para datos reales")
    if not str(origen_host or "").strip():
        bloqueos.append("origen sin host explicito")
    puerto_origen = int(origen_puerto)
    if not 1 <= puerto_origen <= 65535:
        bloqueos.append("puerto de origen fuera de rango")
    if (str(origen_host) in {"127.0.0.1", "localhost", "::1"}
            and 55432 <= puerto_origen <= 55455):
        bloqueos.append("el origen apunta a un laboratorio local: B2 exige el "
                        "origen autorizado, no un lab desechable")

    ruta_aut = Path(autorizacion) if autorizacion else None
    if not ruta_aut or not ruta_aut.is_file():
        bloqueos.append("no existe el documento de autorizacion B2")
    else:
        ok, faltan = _autorizacion_rellena(
            ruta_aut.read_text(encoding="utf-8", errors="replace"))
        if not ok:
            bloqueos.append("autorizacion B2 incompleta: " + "; ".join(faltan))

    dest = Path(destino).resolve()
    if dest == ROOT or ROOT in dest.parents:
        bloqueos.append("destino dentro del repositorio Git")
    if any(parte.lower() in CARPETAS_SINCRONIZADAS for parte in dest.parts):
        bloqueos.append("destino en carpeta sincronizada con la nube")
    if not dest.parent.exists():
        advertencias.append("la carpeta padre del destino aun no existe")

    if int(puerto_restore) in PUERTOS_PROTEGIDOS:
        bloqueos.append(f"puerto de restauracion {puerto_restore} esta protegido")
    if int(puerto_restore) == int(origen_puerto):
        bloqueos.append("el puerto de restauracion coincide con el de origen")
    if not _puerto_libre("127.0.0.1", int(puerto_restore)):
        bloqueos.append(f"el puerto {puerto_restore} ya esta en uso")

    return {
        "ok": not bloqueos,
        "bloqueos": bloqueos,
        "advertencias": advertencias,
        "formato": "v2/AES-256-GCM",
        "sin_fallback_cbc": True,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def estado() -> dict:
    import cryptography
    return {
        "cifrado_b2": "v2 / AES-256-GCM + PBKDF2-HMAC-SHA256",
        "cryptography": getattr(cryptography, "__version__", "?"),
        "iteraciones_defecto": ITERACIONES_DEFECTO,
        "iteraciones_minimas": ITERACIONES_MINIMAS,
        "salt_bytes": SALT_BYTES,
        "nonce_bytes": NONCE_BYTES,
        "tag_bytes": TAG_BYTES,
        "fallback_cbc": False,
    }


def _resolver_frase(args) -> str:
    if args.passfile:
        ruta = Path(args.passfile)
        if not ruta.is_file():
            raise CifradoError("no existe el archivo de frase de paso")
        return ruta.read_text(encoding="utf-8").rstrip("\r\n")
    return getpass.getpass("Frase de paso del respaldo (no se muestra): ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cifrado autenticado B2 (AES-256-GCM) - Etapa 5BU")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("estado", help="capacidades criptograficas disponibles")

    p_cif = sub.add_parser("cifrar", help="cifra un dump con AEAD v2")
    p_cif.add_argument("origen")
    p_cif.add_argument("destino")
    p_cif.add_argument("--passfile")
    p_cif.add_argument("--iteraciones", type=int, default=ITERACIONES_DEFECTO)
    p_cif.add_argument("--meta", action="append", default=[],
                       help="metadato k=v (se autentica en la cabecera)")

    p_ver = sub.add_parser("verificar", help="autentica sin escribir plaintext")
    p_ver.add_argument("archivo")
    p_ver.add_argument("--passfile")

    p_des = sub.add_parser("descifrar", help="descifra solo tras validar el tag")
    p_des.add_argument("archivo")
    p_des.add_argument("salida")
    p_des.add_argument("--passfile")
    p_des.add_argument("--forzar", action="store_true")

    p_pre = sub.add_parser("preflight", help="puerta PRE-B2 (fail-closed)")
    p_pre.add_argument("--origen-host", default="")
    p_pre.add_argument("--origen-puerto", type=int, default=5432)
    p_pre.add_argument("--destino", default="")
    p_pre.add_argument("--puerto-restore", type=int, default=55452)
    p_pre.add_argument("--autorizacion", default="")
    p_pre.add_argument("--formato", default="v2")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "estado":
            print(json.dumps(estado(), indent=2, ensure_ascii=False))
            return 0
        if args.cmd == "cifrar":
            metadatos = {}
            for par in args.meta:
                if "=" in par:
                    k, v = par.split("=", 1)
                    metadatos[k.strip()] = v.strip()
            info = cifrar_archivo(args.origen, args.destino, _resolver_frase(args),
                                  iteraciones=args.iteraciones,
                                  metadatos=metadatos)
            print(json.dumps(info, indent=2, ensure_ascii=False))
            return 0
        if args.cmd == "verificar":
            info = verificar(args.archivo, _resolver_frase(args))
            print(json.dumps({**info, "autenticado": True}, indent=2,
                             ensure_ascii=False))
            return 0
        if args.cmd == "descifrar":
            info = descifrar(args.archivo, args.salida, _resolver_frase(args),
                             permitir_sobrescribir=args.forzar)
            print(json.dumps({**info, "autenticado": True}, indent=2,
                             ensure_ascii=False))
            return 0
        if args.cmd == "preflight":
            res = preflight_preb2(
                origen_host=args.origen_host, origen_puerto=args.origen_puerto,
                destino=args.destino, puerto_restore=args.puerto_restore,
                autorizacion=args.autorizacion, formato=args.formato)
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0 if res["ok"] else 1
    except CifradoError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
