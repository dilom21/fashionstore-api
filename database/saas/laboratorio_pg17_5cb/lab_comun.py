"""Laboratorio 5CB: clúster PostgreSQL 17 desechable y utilidades comunes.

Deriva del arnés del laboratorio 5CA (mismo control de procesos y de
credenciales) con tres diferencias deliberadas:

- El prefijo del directorio temporal y la base son propios de 5CB, de modo que
  nunca se reutiliza ni se destruye el clúster de otro laboratorio.
- El sufijo de los logins de servicio es ``_5cb`` para no chocar con roles
  homónimos que hayan quedado de una ejecución anterior.
- Este laboratorio NO usa ``060_lab_correcciones.sql``: 050a-050c se ensayan
  tal como se entregaron, para comprobar que no necesitan parches.

Restricciones respetadas:
- Nunca usa el servidor :5432 ni bases preexistentes.
- Nunca lee el .env ni la conexión de Supabase del backend.
- El clúster vive en un directorio temporal y se destruye al terminar.
- Las credenciales se generan al azar en memoria y no se escriben en archivos
  versionados; el pwfile de initdb se borra antes de cualquier prueba.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

PGBIN = Path(r"C:\Program Files\PostgreSQL\17\bin")
PGVERSION_MAJOR = 17
HOST = "127.0.0.1"
DIR_PREFIX = "fashionstore-5cb-lab-"
BASE = "fashionstore_lab_5cb"
HERE = Path(__file__).resolve().parent
SQLDIR = HERE / "sql"
EVIDENCE = HERE / "resultados"


class LabError(RuntimeError):
    """Fallo controlado del laboratorio (no oculta el motivo)."""


def paso(mensaje: str) -> None:
    """Traza con flush inmediato: los scripts se ejecutan desacoplados."""
    print(f"... {mensaje}", flush=True)


def exe(nombre: str) -> str:
    ruta = PGBIN / f"{nombre}.exe"
    if not ruta.is_file():
        raise LabError(f"Falta el binario {ruta}")
    return str(ruta)


def puerto_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def puerto_ocupado(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, port)) == 0


def _env(passwd: str | None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.pop("PGOPTIONS", None)
    env["PGSSLMODE"] = "disable"
    env["PGCONNECT_TIMEOUT"] = "10"
    env["PGCLIENTENCODING"] = "UTF8"
    if passwd is None:
        env.pop("PGPASSWORD", None)
    else:
        env["PGPASSWORD"] = passwd
    return env


def correr(cmd: list[str], *, passwd: str | None = None, timeout: int = 180,
           server_control: bool = False, logfile: Path | None = None) -> str:
    """Ejecuta un binario de PostgreSQL sin heredar pipes al servidor."""
    if logfile:
        proceso = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=_env(passwd), timeout=timeout,
        )
        if proceso.returncode != 0:
            raise LabError(f"Fallo {Path(cmd[0]).name}; ver {logfile}")
        return ""
    proceso = subprocess.run(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, env=_env(passwd), text=True, timeout=timeout,
    )
    if proceso.returncode != 0:
        raise LabError(f"Fallo {Path(cmd[0]).name}: {(proceso.stdout or '').strip()}")
    return proceso.stdout or ""


@dataclass
class Cluster:
    """Clúster efímero en %TEMP% con superusuario de contraseña aleatoria."""

    datadir: Path
    port: int
    passwd: str
    logfile: Path
    iniciado: bool = False
    detalles: dict[str, object] = field(default_factory=dict)
    _servidor: object = field(default=None, repr=False)
    _log: object = field(default=None, repr=False)

    @classmethod
    def crear(cls, base: str = BASE) -> "Cluster":
        directorio = Path(tempfile.mkdtemp(prefix=DIR_PREFIX))
        datadir = directorio / "data"
        logfile = directorio / "server.log"
        passwd = secrets.token_urlsafe(30)
        pwfile = directorio / "pw.txt"
        pwfile.write_text(passwd, encoding="ascii")
        puerto = puerto_libre()
        paso(f"initdb en {datadir} (puerto {puerto})")
        try:
            correr([
                exe("initdb"), "-D", str(datadir), "-U", "postgres",
                f"--pwfile={pwfile}", "--auth-host=scram-sha-256",
                "--auth-local=scram-sha-256", "-E", "UTF8", "--locale=C",
            ], timeout=300)
        finally:
            pwfile.unlink(missing_ok=True)
        paso("initdb terminado; arrancando servidor")
        cluster = cls(datadir=datadir, port=puerto, passwd=passwd,
                      logfile=logfile)
        try:
            cluster.arrancar()
            paso("servidor arriba; creando base de laboratorio")
            cluster.crear_base(base)
        except Exception:
            cluster.destruir()
            raise
        paso(f"base {base} creada")
        cluster.detalles["base"] = base
        return cluster

    def arrancar(self) -> None:
        if puerto_ocupado(self.port):
            raise LabError(f"El puerto {self.port} ya está ocupado")
        # pg_ctl -w se bloquea en este equipo; se arranca postgres.exe y se
        # sondea con psql, que da un resultado determinista.
        self._log = open(self.logfile, "a", encoding="utf-8", errors="replace")
        self._servidor = subprocess.Popen(
            [
                exe("postgres"), "-D", str(self.datadir), "-p", str(self.port),
                "-c", f"listen_addresses={HOST}", "-c", "ssl=off",
                "-c", "fsync=off", "-c", "full_page_writes=off",
            ],
            stdin=subprocess.DEVNULL, stdout=self._log, stderr=self._log,
            env=_env(None),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        for _ in range(120):
            try:
                version = self.escalar("SHOW server_version", db="postgres")
            except LabError:
                time.sleep(0.5)
                continue
            self.iniciado = True
            if int(version.split(".")[0]) != PGVERSION_MAJOR:
                self.parar()
                raise LabError(f"Versión inesperada del servidor: {version}")
            paso(f"servidor PostgreSQL {version} escuchando en {HOST}:{self.port}")
            return
        self.parar()
        raise LabError(f"El servidor no respondió; ver {self.logfile}")

    def parar(self) -> None:
        pid_file = self.datadir / "postmaster.pid"
        if not pid_file.exists():
            self.iniciado = False
            return
        pid = pid_file.read_text(encoding="ascii", errors="replace").splitlines()[0]
        try:
            correr([exe("pg_ctl"), "-D", str(self.datadir), "-m", "fast",
                    "-w", "-t", "30", "stop"], timeout=60, server_control=True)
        except Exception:  # noqa: BLE001 - se intenta una vía más directa
            try:
                correr([exe("pg_ctl"), "kill", "INT", pid], timeout=30,
                       server_control=True)
            except Exception:  # noqa: BLE001
                pass
        for _ in range(60):
            if not pid_file.exists() and not puerto_ocupado(self.port):
                break
            time.sleep(0.5)
        if self._servidor is not None:
            try:
                self._servidor.wait(timeout=15)
            except Exception:  # noqa: BLE001
                pass
            self._servidor = None
        if hasattr(self, "_log") and self._log is not None:
            self._log.close()
            self._log = None
        self.iniciado = False

    def destruir(self) -> None:
        self.parar()
        directorio = self.datadir.parent
        if directorio.name.startswith(DIR_PREFIX) and directorio.is_dir():
            shutil.rmtree(directorio, ignore_errors=True)

    # -- conexión ---------------------------------------------------------
    def psql_cmd(self, db: str) -> list[str]:
        return [
            exe("psql"), "-X", "-q", "-A", "-t", "-w", "-v", "ON_ERROR_STOP=1",
            "-h", HOST, "-p", str(self.port), "-U", "postgres", "-d", db,
        ]

    def sql(self, sentencia: str, db: str | None = None) -> str:
        base = db or str(self.detalles["base"])
        return correr(self.psql_cmd(base) + ["-c", sentencia],
                      passwd=self.passwd)

    def archivo(self, ruta: Path, db: str | None = None) -> str:
        base = db or str(self.detalles["base"])
        if not Path(ruta).is_file():
            raise LabError(f"No existe el SQL {ruta}")
        return correr(self.psql_cmd(base) + ["-f", str(ruta)],
                      passwd=self.passwd, timeout=600)

    def escalar(self, sentencia: str, db: str | None = None) -> str:
        filas = [ln for ln in self.sql(sentencia, db).splitlines() if ln.strip()]
        if len(filas) != 1:
            raise LabError(
                f"Se esperaba una fila y hubo {len(filas)}: {sentencia[:70]}")
        return filas[0].strip()

    def crear_base(self, nombre: str) -> None:
        if not nombre.startswith("fashionstore_lab_"):
            raise LabError("Solo se crean bases de laboratorio fashionstore_lab_*")
        self.sql(
            f'CREATE DATABASE "{nombre}" TEMPLATE template0 ENCODING UTF8',
            db="postgres",
        )

    def dsn(self, usuario: str, passwd: str, db: str | None = None) -> str:
        from urllib.parse import quote

        base = db or str(self.detalles["base"])
        return (
            f"postgresql://{quote(usuario)}:{quote(passwd)}@{HOST}:{self.port}/{base}"
            "?sslmode=disable"
        )


# -- JWT HS256 con la misma forma de claims que FastAPI -------------------
def b64url(dato: bytes) -> str:
    return base64.urlsafe_b64encode(dato).decode("ascii").rstrip("=")


def jwt_hs256(secreto: str, claims: dict, *, alg: str = "HS256") -> str:
    cabecera = b64url(json.dumps({"alg": alg, "typ": "JWT"},
                                 separators=(",", ":")).encode())
    cuerpo = b64url(json.dumps(claims, separators=(",", ":")).encode())
    firma = hmac_mod.new(secreto.encode(), f"{cabecera}.{cuerpo}".encode(),
                         hashlib.sha256).digest()
    return f"{cabecera}.{cuerpo}.{b64url(firma)}"


def claims_tenant(usuario_id: int, empresa_id: int, contexto: str,
                  *, minutos: int = 30, jti: str | None = None) -> dict:
    ahora = datetime.now(timezone.utc)
    return {
        "sub": str(usuario_id),
        "scope": "tenant",
        "empresa_id": str(empresa_id),
        "contexto": contexto,
        "iat": int(ahora.timestamp()),
        "jti": jti or str(uuid.uuid4()),
        "exp": int((ahora + timedelta(minutes=minutos)).timestamp()),
    }


class Evidencia:
    """Acumula PASS/FAIL sin ocultar el error que produjo cada fallo."""

    def __init__(self) -> None:
        self.pruebas: list[dict[str, object]] = []

    def registrar(self, codigo: str, titulo: str, ok: bool,
                  detalle: str = "") -> bool:
        self.pruebas.append({"codigo": codigo, "titulo": titulo,
                             "resultado": "PASS" if ok else "FAIL",
                             "detalle": detalle})
        marca = "PASS" if ok else "FAIL"
        print(f"[{marca}] {codigo} {titulo}" + (f" :: {detalle}" if detalle else ""),
              flush=True)
        return ok

    def espera(self, codigo: str, titulo: str, fn) -> bool:
        """Ejecuta una comprobación que debe tener éxito."""
        try:
            detalle = fn() or ""
            return self.registrar(codigo, titulo, True, str(detalle))
        except Exception as exc:  # noqa: BLE001 - el laboratorio registra todo
            return self.registrar(codigo, titulo, False,
                                  f"{type(exc).__name__}: {exc}"[:400])

    def rechazo(self, codigo: str, titulo: str, fn, *,
                sqlstate: str | None = None) -> bool:
        """Ejecuta una comprobación que DEBE fallar (fail-closed)."""
        try:
            detalle = fn()
        except Exception as exc:  # noqa: BLE001
            estado = getattr(getattr(exc, "orig", None), "sqlstate", None)
            estado = estado or getattr(exc, "sqlstate", None)
            if sqlstate and estado != sqlstate:
                return self.registrar(
                    codigo, titulo, False,
                    f"falló con sqlstate={estado} en lugar de {sqlstate}: "
                    f"{type(exc).__name__}: {exc}"[:400],
                )
            return self.registrar(codigo, titulo, True,
                                  f"rechazado con sqlstate={estado}")
        return self.registrar(codigo, titulo, False,
                              f"NO se rechazó: {str(detalle)[:200]}")

    @property
    def fallos(self) -> list[dict[str, object]]:
        return [p for p in self.pruebas if p["resultado"] == "FAIL"]

    def resumen(self) -> dict[str, object]:
        return {
            "total": len(self.pruebas),
            "pass": len(self.pruebas) - len(self.fallos),
            "fail": len(self.fallos),
            "pruebas": self.pruebas,
        }
