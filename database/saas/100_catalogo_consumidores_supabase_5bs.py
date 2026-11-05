"""5BS / Etapa J - Catalogo determinista de consumidores de Supabase / PostgREST.

Que hace
--------
Recorre, en SOLO LECTURA, el backend y los repositorios locales hermanos
(Angular y Flutter) buscando senales de consumo directo de la Data API de
Supabase: `supabase-js`, `createClient`, rutas `rest/v1`, PostgREST, los nombres
de variables `SUPABASE_*` y los roles SQL `anon` / `authenticated` /
`service_role`.

Que NO hace
-----------
* No abre conexiones de red ni se conecta a Supabase, Render o Vercel.
* No lee ni imprime valores de variables de entorno: solo NOMBRES.
* No imprime fragmentos de codigo ni DSN; solo ruta relativa, linea y patron.
* No modifica ningun archivo de los repositorios inspeccionados.

Salida
------
JSON sanitizado + Markdown legible. El veredicto `consumidores_directos` cuenta
solo hallazgos en codigo ejecutable (no comentarios ni documentacion), que es la
evidencia que permite decidir el contrato de permisos R1.

Uso
---
    python database/saas/100_catalogo_consumidores_supabase_5bs.py
    python database/saas/100_catalogo_consumidores_supabase_5bs.py --json salida.json --markdown salida.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCIAS = ROOT / "database" / "saas" / "evidencias"

# Repositorios locales. Los hermanos son de SOLO LECTURA y opcionales.
REPOS = [
    ("backend", ROOT),
    ("web_angular", ROOT.parent / "fashionstore-web"),
    ("mobile_flutter", ROOT.parent / "fashionstore_mobile"),
]

CARPETAS_EXCLUIDAS = {
    ".git", "node_modules", "dist", "build", ".angular", ".dart_tool",
    "__pycache__", ".pytest_cache", ".venv", "venv", "env", "coverage",
    "Pods", ".idea", ".vscode", "android", "ios", "linux", "macos", "windows",
    "web",  # Flutter web build assets, no codigo de negocio
}

EXTENSIONES = {
    ".py", ".ts", ".js", ".mjs", ".cjs", ".dart", ".json", ".yaml", ".yml",
    ".toml", ".sql", ".ps1", ".sh", ".html", ".gradle", ".kts", ".xml",
    ".env", ".ini", ".cfg", ".conf", ".md",
}

# (clave, patron). Los patrones buscan senales, nunca valores.
PATRONES = [
    ("supabase_js", re.compile(r"@supabase/supabase-js|supabase-js", re.I)),
    ("create_client", re.compile(r"\bcreateClient\s*\(", re.I)),
    ("supabase_client_dart", re.compile(r"\bsupabase_flutter\b|\bSupabase\.initialize\s*\(", re.I)),
    ("rest_v1", re.compile(r"/rest/v1\b|rest/v1", re.I)),
    ("postgrest", re.compile(r"\bpostgrest\b", re.I)),
    ("supabase_url_var", re.compile(r"\bSUPABASE_URL\b|\bSUPABASE_ANON_KEY\b|"
                                    r"\bSUPABASE_SERVICE_ROLE_KEY\b|\bSUPABASE_JWT_SECRET\b")),
    ("rol_plataforma", re.compile(r"\bservice_role\b|\banon\b|\bauthenticated\b")),
    ("dsn_postgres", re.compile(r"postgres(?:ql)?(?:\+psycopg2?)?://")),
    ("supabase_host", re.compile(r"[a-z0-9-]+\.supabase\.co", re.I)),
]

# Marcadores de linea no ejecutable.
COMENTARIO = re.compile(r"^\s*(#|//|\*|/\*|--|<!--|\"\"\"|''')")


def _es_comentario(linea: str) -> bool:
    return bool(COMENTARIO.match(linea))


def _clasificar(relativa: Path, linea: str) -> str:
    posix = relativa.as_posix()
    if "/evidencias/" in "/" + posix or posix.startswith("evidencias/"):
        return "evidencia"
    if posix.startswith("database/") and relativa.suffix.lower() != ".py":
        return "herramienta_interna"
    if posix.startswith("database/"):
        return "herramienta_interna"
    if relativa.suffix.lower() == ".md":
        return "documentacion"
    if _es_comentario(linea):
        return "comentario"
    return "codigo"


# Un consumidor directo exige senal de CLIENTE (no de SQL propio) en codigo de
# aplicacion. Las migraciones, laboratorios y evidencias del propio repositorio
# mencionan esos terminos por contrato, no por consumo.
PATRONES_CLIENTE = {"supabase_js", "create_client", "supabase_client_dart",
                    "rest_v1", "postgrest", "supabase_host"}
PREFIJOS_APLICACION = {"app/", "tests/", "src/", "lib/", "test/"}
MANIFIESTOS = {"package.json", "pubspec.yaml", "pubspec.lock", "requirements.txt"}


def _es_codigo_de_aplicacion(relativa: Path) -> bool:
    posix = relativa.as_posix()
    return (any(posix.startswith(p) for p in PREFIJOS_APLICACION)
            or posix in MANIFIESTOS)


def _clase_conexion_backend(raiz: Path) -> dict:
    """Describe la conexion del backend SIN revelar el valor del DSN."""
    archivo = raiz / ".env"
    datos = {
        "archivo_env_presente": archivo.exists(),
        "database_url_presente": False,
        "host_supabase": None,
        "puerto_pooler": None,
        "driver": None,
        "usuario_superusuario_postgres": None,
        "base_de_datos": None,
    }
    if not archivo.exists():
        return datos
    from urllib.parse import urlparse

    for linea in archivo.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*DATABASE_URL\s*=\s*(\S.*)$", linea)
        if not m:
            continue
        datos["database_url_presente"] = True
        valor = m.group(1).strip().strip('"').strip("'")
        datos["driver"] = valor.split("://", 1)[0]
        limpio = valor.replace("postgresql+psycopg://", "postgresql://") \
                      .replace("postgresql+psycopg2://", "postgresql://")
        try:
            parsed = urlparse(limpio)
        except ValueError:
            return datos
        host = (parsed.hostname or "").lower()
        datos["host_supabase"] = host.endswith(".supabase.co") or host.endswith(".supabase.com")
        datos["puerto_pooler"] = parsed.port in {6543, 5432} if parsed.port else None
        usuario = (parsed.username or "").lower()
        datos["usuario_superusuario_postgres"] = usuario.split(".")[0] == "postgres"
        datos["base_de_datos"] = (parsed.path or "").lstrip("/") or None
        break
    return datos


def _iter_archivos(raiz: Path):
    for actual, dirs, files in os.walk(raiz):
        dirs[:] = [d for d in dirs if d not in CARPETAS_EXCLUIDAS]
        for nombre in files:
            ruta = Path(actual) / nombre
            if ruta.suffix.lower() not in EXTENSIONES and nombre != ".env":
                continue
            try:
                if ruta.stat().st_size > 1_500_000:
                    continue
            except OSError:
                continue
            yield ruta


def _nombres_env(raiz: Path) -> list[dict]:
    """Solo NOMBRES de variables; jamas valores."""
    salida: list[dict] = []
    for candidato in (raiz / ".env", raiz / ".env.example", raiz / ".env.local"):
        if not candidato.exists():
            continue
        try:
            texto = candidato.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for linea in texto.splitlines():
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", linea)
            if m:
                salida.append({"archivo": candidato.name, "variable": m.group(1)})
    return salida


def escanear() -> dict:
    hallazgos: list[dict] = []
    repos_info: list[dict] = []
    for alias, raiz in REPOS:
        existe = raiz.exists()
        info = {
            "alias": alias,
            "ruta_relativa_al_workspace": (
                "." if raiz == ROOT else f"../{raiz.name}"
            ),
            "existe": existe,
            "archivos_analizados": 0,
            "hallazgos": 0,
        }
        if not existe:
            repos_info.append(info)
            continue
        for ruta in _iter_archivos(raiz):
            info["archivos_analizados"] += 1
            try:
                texto = ruta.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            relativa = ruta.relative_to(raiz)
            for numero, linea in enumerate(texto.splitlines(), start=1):
                for clave, patron in PATRONES:
                    if not patron.search(linea):
                        continue
                    hallazgos.append({
                        "repo": alias,
                        "archivo": relativa.as_posix(),
                        "linea": numero,
                        "patron": clave,
                        "clasificacion": _clasificar(relativa, linea),
                    })
        info["hallazgos"] = sum(1 for h in hallazgos if h["repo"] == alias)
        repos_info.append(info)

    env_vars = _nombres_env(ROOT)
    supabase_env = [v for v in env_vars if v["variable"].startswith("SUPABASE")]

    por_clasificacion: dict[str, int] = {}
    por_patron: dict[str, int] = {}
    for h in hallazgos:
        por_clasificacion[h["clasificacion"]] = por_clasificacion.get(h["clasificacion"], 0) + 1
        por_patron[h["patron"]] = por_patron.get(h["patron"], 0) + 1

    directos = [
        h for h in hallazgos
        if h["clasificacion"] == "codigo"
        and h["patron"] in PATRONES_CLIENTE
        and _es_codigo_de_aplicacion(Path(h["archivo"]))
    ]
    credenciales = [
        h for h in hallazgos
        if h["clasificacion"] == "codigo" and h["patron"] == "supabase_url_var"
    ]
    conexion = _clase_conexion_backend(ROOT)

    return {
        "generado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metodo": "estatico, solo lectura, sin red; nombres de variables, no valores",
        "repos": repos_info,
        "variables_env_backend": [v["variable"] for v in env_vars],
        "variables_env_supabase": [v["variable"] for v in supabase_env],
        "por_clasificacion": por_clasificacion,
        "por_patron": por_patron,
        "conexion_backend": conexion,
        "hallazgos": hallazgos,
        "consumidores_directos_confirmados": len(directos),
        "credenciales_supabase_en_codigo": len(credenciales),
        "veredicto": (
            "SIN_CONSUMIDOR_DIRECTO_OBSERVABLE"
            if not directos and not credenciales
            else "CONSUMIDOR_DIRECTO_POR_REVISAR"
        ),
    }


def escribir_markdown(datos: dict, ruta: Path) -> None:
    lineas = [
        "# EVIDENCIA 5BS - Catalogo de consumidores Supabase / PostgREST",
        "",
        f"Generado (UTC): {datos['generado_utc']}",
        "",
        f"Metodo: {datos['metodo']}",
        "",
        "## Repositorios inspeccionados",
        "",
        "| Alias | Ruta | Existe | Archivos | Hallazgos |",
        "|---|---|---|---|---|",
    ]
    for r in datos["repos"]:
        lineas.append(
            f"| {r['alias']} | `{r['ruta_relativa_al_workspace']}` | "
            f"{'si' if r['existe'] else 'no'} | {r['archivos_analizados']} | {r['hallazgos']} |"
        )
    lineas += [
        "",
        "## Veredicto",
        "",
        f"- Consumidores directos en codigo ejecutable: **{datos['consumidores_directos_confirmados']}**",
        f"- Credenciales Supabase declaradas en codigo: **{datos['credenciales_supabase_en_codigo']}**",
        f"- Variables de entorno `SUPABASE_*` en el backend: "
        f"**{len(datos['variables_env_supabase'])}**",
        f"- Resultado: **{datos['veredicto']}**",
        "",
        "## Canal real del backend hacia PostgreSQL",
        "",
        "| Propiedad (sin valores) | Resultado |",
        "|---|---|",
    ]
    conexion = datos.get("conexion_backend", {})
    for clave, etiqueta in (
        ("archivo_env_presente", "`.env` presente"),
        ("database_url_presente", "`DATABASE_URL` declarada"),
        ("host_supabase", "Host de Supabase en el DSN"),
        ("puerto_pooler", "Puerto 5432/6543 (directo o pooler)"),
        ("driver", "Driver"),
        ("usuario_superusuario_postgres", "Usuario con prefijo `postgres`"),
        ("base_de_datos", "Base de datos"),
    ):
        valor = conexion.get(clave)
        if clave == "driver" and valor:
            valor = f"`{valor}`"
        lineas.append(f"| {etiqueta} | {valor} |")
    codigo = [h for h in datos["hallazgos"] if h["clasificacion"] == "codigo"]
    lineas += [
        "## Hallazgos en codigo de aplicacion (no comentarios ni documentacion)",
        "",
    ]
    if codigo:
        lineas += ["| Repo | Archivo | Linea | Patron |", "|---|---|---|---|"]
        for h in codigo:
            lineas.append(f"| {h['repo']} | `{h['archivo']}` | {h['linea']} | {h['patron']} |")
    else:
        lineas.append("Ninguno.")
    lineas += [
        "",
        "## Distribucion",
        "",
        "| Clasificacion | Hallazgos |",
        "|---|---|",
    ]
    for clave, valor in sorted(datos["por_clasificacion"].items()):
        lineas.append(f"| {clave} | {valor} |")
    lineas += ["", "| Patron | Hallazgos |", "|---|---|"]
    for clave, valor in sorted(datos["por_patron"].items()):
        lineas.append(f"| {clave} | {valor} |")
    lineas += [
        "",
        "## Nota de custodia",
        "",
        "Este catalogo no contiene valores de variables, DSN, tokens ni fragmentos de",
        "codigo. Solo rutas relativas, numeros de linea y nombres de patron.",
        "",
        f"SHA-256 de este documento: pendiente de calculo externo.",
    ]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json")
    parser.add_argument("--markdown")
    args = parser.parse_args(argv)

    datos = escanear()
    sello = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    EVIDENCIAS.mkdir(parents=True, exist_ok=True)
    json_ruta = Path(args.json) if args.json else EVIDENCIAS / f"EVIDENCIA_CONSUMIDORES_5BS_{sello}.json"
    md_ruta = Path(args.markdown) if args.markdown else EVIDENCIAS / f"EVIDENCIA_CONSUMIDORES_5BS_{sello}.md"
    json_ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    escribir_markdown(datos, md_ruta)
    datos["salida"] = {"json": str(json_ruta), "markdown": str(md_ruta)}
    print(json.dumps({
        "consumidores_directos_confirmados": datos["consumidores_directos_confirmados"],
        "credenciales_supabase_en_codigo": datos["credenciales_supabase_en_codigo"],
        "variables_env_supabase": len(datos["variables_env_supabase"]),
        "repos": [{k: r[k] for k in ("alias", "existe", "archivos_analizados", "hallazgos")}
                  for r in datos["repos"]],
        "por_clasificacion": datos["por_clasificacion"],
        "veredicto": datos["veredicto"],
        "json": str(json_ruta),
        "markdown": str(md_ruta),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
