"""5BQ / Etapa H - Validador offline del insumo autorizado del esquema historico.

Que hace y que NO hace
-----------------------
Valida, **sin conectarse a ningun servidor**, los dos artefactos que exige el
cierre de B1:

* ``pg_dump --schema-only`` del origen autorizado (que sea schema-only de
  verdad y que proceda de PostgreSQL 17.x, nunca 18 ni 16).
* El catalogo JSON que emite ``92_catalogo_json_solo_lectura_5bq.sql`` o
  ``87_comparar_catalogo_historico_5bp.py --exportar-dsn``, con el contrato
  exacto que consume 87.

Ademas clasifica el informe de 87 en diferencias de **plataforma** (roles y
extensiones gestionados por Supabase, que no existen ni deben existir en un
cluster PG17 limpio) y de **aplicacion** (lo que realmente decide B1).

No se conecta a nada, no ejecuta SQL, no crea objetos y no decide B1: valida el
insumo y deja el veredicto al comparador 87.

Codigos de salida
-----------------
0 = insumo apto y coherente con la referencia externa.
4 = insumo apto pero con discrepancias frente a la referencia externa
    (41 tablas / 27 triggers / 24 rutinas / 1 vista / PG 17.6). Requiere
    revision humana; no es un fallo del validador.
5 = insumo NO apto (fail-closed): se detiene la parte dependiente de B1.
2 = error de uso.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Referencia externa verificada el 2026-10-31 mediante consulta de metadatos de
# SOLO LECTURA al proyecto Supabase `fashionstore`. Es un conteo, NO un dump:
# sirve como control de coherencia, no como sustituto del esquema.
REFERENCIA_EXTERNA = {
    "fuente": (
        "consulta de metadatos de solo lectura, proyecto Supabase fashionstore, "
        "2026-10-31"
    ),
    "version_servidor": "PostgreSQL 17.6",
    "tablas_public": 41,
    "triggers_public": 27,
    "rutinas_public": 24,
    "vistas_public": 1,
    "politicas_public": 0,
}

ARIDAD_OBJETOS = {
    "tablas": 4,
    "vistas": 3,
    "columnas_empresa_id": 2,
    "rutinas": 7,
    "triggers": 6,
    "politicas": 7,
    "propietarios": 3,
    "roles": 5,
    "extensiones": 3,
    "privilegios_rutinas": 3,
}

# Roles que administra la plataforma Supabase. Existen en el origen y no en un
# PostgreSQL 17 limpio: su ausencia no es una diferencia del esquema historico.
ROLES_PLATAFORMA = {
    "anon", "authenticated", "service_role", "supabase_admin",
    "supabase_auth_admin", "supabase_storage_admin", "supabase_read_only_user",
    "supabase_replication_admin", "supabase_functions_admin", "dashboard_user",
    "authenticator", "pgbouncer", "pg_monitor", "pg_read_all_data",
    "pg_write_all_data", "pg_read_all_settings", "pg_read_all_stats",
    "pg_stat_scan_tables", "pg_signal_backend", "postgres",
}

# Extensiones que instala la plataforma o que son parte del nucleo.
EXTENSIONES_PLATAFORMA = {
    "plpgsql", "pgcrypto", "uuid-ossp", "pg_stat_statements", "pg_graphql",
    "supabase_vault", "pg_net", "pgsodium", "pgjwt", "pg_cron", "pg_partman",
    "pg_trgm", "btree_gin", "btree_gist", "citext", "unaccent",
}

ESQUEMAS_PLATAFORMA = ("auth", "storage", "extensions", "vault", "realtime",
                       "graphql", "graphql_public", "pgbouncer", "supabase_migrations")

PATRON_DATOS = re.compile(
    r"^(COPY\s+\S+.*\sFROM\s+stdin;|INSERT\s+INTO\s|UPDATE\s+\S+\s+SET\s|"
    r"DELETE\s+FROM\s|TRUNCATE\s+\S+)",
    re.IGNORECASE | re.MULTILINE,
)
PATRON_DOLLAR = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


class InsumoInvalido(RuntimeError):
    """El archivo no puede usarse como evidencia de B1."""


def sha256_archivo(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as manejador:
        for bloque in iter(lambda: manejador.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _tiene_bom(ruta: Path) -> bool:
    try:
        return ruta.read_bytes()[:3] == b"\xef\xbb\xbf"
    except OSError:
        return False


def _leer_texto(ruta: Path) -> str:
    crudo = ruta.read_bytes()
    for bom, nombre in ((b"\xff\xfe", "UTF-16LE"), (b"\xfe\xff", "UTF-16BE")):
        if crudo.startswith(bom):
            raise InsumoInvalido(
                f"{ruta.name} esta en {nombre}: probable redireccion de PowerShell "
                "con '>'. Regenerelo con 'Out-File -Encoding utf8' o con --file.")
    try:
        return crudo.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InsumoInvalido(
            f"{ruta.name} no es UTF-8 valido ({exc.reason}). Un dump de pg_dump "
            "debe ser UTF-8.") from exc


def _quitar_cuerpos(texto: str) -> str:
    """Reemplaza bloques dollar-quoted por un marcador.

    Sin esto, el cuerpo de una funcion que contiene una linea 'INSERT INTO ...'
    se confundiria con datos exportados y el validador rechazaria un dump
    schema-only legitimo.
    """
    salida: list[str] = []
    indice = 0
    while True:
        coincidencia = PATRON_DOLLAR.search(texto, indice)
        if not coincidencia:
            salida.append(texto[indice:])
            break
        etiqueta = coincidencia.group(0)
        inicio_cuerpo = coincidencia.end()
        fin_cuerpo = texto.find(etiqueta, inicio_cuerpo)
        if fin_cuerpo == -1:
            salida.append(texto[indice:])
            break
        salida.append(texto[indice:coincidencia.start()])
        salida.append("\n<marcador:cuerpo_omitido>\n")
        indice = fin_cuerpo + len(etiqueta)
    return "".join(salida)


def validar_dump(ruta: Path) -> dict:
    """Valida un pg_dump --schema-only. Devuelve apto/errores/avisos/datos."""
    errores: list[str] = []
    avisos: list[str] = []

    if not ruta.exists():
        raise InsumoInvalido(f"No existe el archivo indicado: {ruta}")
    if ruta.stat().st_size == 0:
        raise InsumoInvalido(f"{ruta.name} esta vacio.")

    texto = _leer_texto(ruta)
    sin_cuerpos = _quitar_cuerpos(texto)

    version_origen = None
    version_dump = None
    coincidencia = re.search(
        r"^-- Dumped from database version (\S+)", texto, re.MULTILINE)
    if coincidencia:
        version_origen = coincidencia.group(1)
    coincidencia = re.search(
        r"^-- Dumped by pg_dump version (\S+)", texto, re.MULTILINE)
    if coincidencia:
        version_dump = coincidencia.group(1)

    if version_origen is None and version_dump is None:
        errores.append(
            "No se encontro la cabecera de pg_dump ('-- Dumped from database "
            "version'). El archivo no es un pg_dump de texto plano; si es "
            "formato custom/tar, use pg_restore -l o repita con -Fp.")
    else:
        if version_origen is None:
            errores.append("Cabecera sin version del servidor de origen.")
        elif not version_origen.startswith("17."):
            errores.append(
                f"El origen declara PostgreSQL {version_origen}. El esquema "
                "historico verificado es 17.6; un origen 16.x o 18.x no es el "
                "insumo correcto para B1.")
        if version_dump is not None and not version_dump.startswith("17."):
            avisos.append(
                f"pg_dump {version_dump} genero el archivo. Se pidio PG17; "
                "un pg_dump de otra mayor puede reordenar u omitir matices de "
                "version. Regenerar con binarios 17.x.")

    datos = PATRON_DATOS.findall(sin_cuerpos)
    if datos:
        muestra = sorted({d.split()[0].upper() for d in datos})
        errores.append(
            "El archivo contiene sentencias de datos ("
            + ", ".join(muestra)
            + "). Se exige --schema-only sin filas: no se admite copia con "
              "datos en esta etapa.")

    if re.search(r"^\\connect\s", texto, re.MULTILINE):
        avisos.append(
            "El dump contiene '\\connect': cubre mas de una base o fue tomado "
            "con --create. Revisar el alcance antes de restaurar.")

    if _tiene_bom(ruta):
        avisos.append(
            "El archivo tiene BOM UTF-8. psql y pg_restore lo toleran, pero "
            "conviene regenerarlo sin BOM para evitar diferencias de hash.")

    conteos = {
        "create_table": len(re.findall(r"^CREATE TABLE ", sin_cuerpos, re.MULTILINE)),
        "create_trigger": len(re.findall(r"^CREATE TRIGGER ", sin_cuerpos, re.MULTILINE)),
        "create_rutina": len(re.findall(
            r"^CREATE (?:OR REPLACE )?(?:FUNCTION|PROCEDURE) ", sin_cuerpos, re.MULTILINE)),
        "create_vista": len(re.findall(
            r"^CREATE (?:OR REPLACE )?(?:VIEW|MATERIALIZED VIEW) ", sin_cuerpos, re.MULTILINE)),
        "create_policy": len(re.findall(r"^CREATE POLICY ", sin_cuerpos, re.MULTILINE)),
        "create_extension": len(re.findall(r"^CREATE EXTENSION ", sin_cuerpos, re.MULTILINE)),
    }

    esquemas_plataforma = sorted({
        esquema for esquema in ESQUEMAS_PLATAFORMA
        if re.search(rf"\b{re.escape(esquema)}\.", sin_cuerpos)
    })
    if esquemas_plataforma:
        avisos.append(
            "El dump referencia esquemas gestionados por Supabase ("
            + ", ".join(esquemas_plataforma)
            + "). Para restaurar en un PG17 limpio habra que crear esas "
              "extensiones/roles; no se duplican automaticamente.")

    return {
        "archivo": str(ruta),
        "bytes": ruta.stat().st_size,
        "sha256": sha256_archivo(ruta),
        "apto": not errores,
        "errores": errores,
        "avisos": avisos,
        "datos": {
            "version_origen": version_origen,
            "version_pg_dump": version_dump,
            "conteos_heuristicos": conteos,
            "nota_conteos": (
                "Conteo por sentencia CREATE. Es orientativo: pg_dump puede "
                "emitir tablas particionadas como CREATE TABLE ... PARTITION OF "
                "y no distingue funciones de agregados. El conteo autoritativo "
                "es el del catalogo JSON."
            ),
            "esquemas_plataforma_referenciados": esquemas_plataforma,
        },
    }


def firma_catalogo(objetos: dict) -> str:
    """Misma firma que 87_comparar_catalogo_historico_5bp.py."""
    base = json.dumps(objetos, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


def validar_catalogo(ruta: Path, firma_rechazada: str | None = None) -> dict:
    """Valida el contrato del catalogo JSON que consume 87."""
    errores: list[str] = []
    avisos: list[str] = []
    hallazgos: list[str] = []

    if not ruta.exists():
        raise InsumoInvalido(f"No existe el archivo indicado: {ruta}")

    texto = _leer_texto(ruta).strip()
    if _tiene_bom(ruta):
        errores.append(
            "El catalogo tiene BOM UTF-8 y 87 lo lee con encoding=\"utf-8\": "
            "fallara con 'Unexpected UTF-8 BOM'. Regenerelo sin BOM, por "
            "ejemplo: $t = Get-Content -Raw <archivo>; "
            "[IO.File]::WriteAllText(<archivo>, $t, "
            "(New-Object Text.UTF8Encoding($false)))")
    if texto.startswith("catalogo"):
        # Salida de psql con encabezado: no es JSON valido.
        raise InsumoInvalido(
            f"{ruta.name} parece salida de psql con encabezado de columna. "
            "Use --tuples-only --no-align o copie solo el valor de la celda.")
    try:
        catalogo = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise InsumoInvalido(
            f"{ruta.name} no es JSON valido ({exc.msg}, linea {exc.lineno}). "
            "Asegurese de copiar el valor completo de la celda 'catalogo'.") from exc

    if not isinstance(catalogo, dict):
        raise InsumoInvalido("El catalogo debe ser un objeto JSON.")

    meta = catalogo.get("meta")
    objetos = catalogo.get("objetos")
    if not isinstance(meta, dict):
        errores.append("Falta el bloque 'meta'.")
        meta = {}
    if not isinstance(objetos, dict):
        errores.append("Falta el bloque 'objetos'.")
        objetos = {}

    for clave, aridad in ARIDAD_OBJETOS.items():
        if clave not in objetos:
            errores.append(f"Falta la clave objetos.{clave}.")
            continue
        filas = objetos[clave]
        if not isinstance(filas, list):
            errores.append(f"objetos.{clave} debe ser una lista.")
            continue
        malformadas = [
            indice for indice, fila in enumerate(filas)
            if not isinstance(fila, list) or len(fila) != aridad
        ]
        if malformadas:
            errores.append(
                f"objetos.{clave}: {len(malformadas)} fila(s) no son listas de "
                f"{aridad} columnas (indices {malformadas[:5]}). El contrato de "
                "87 exige aridad fija.")

    version = str(meta.get("version_servidor", ""))
    if not version:
        errores.append("meta.version_servidor ausente.")
    elif not version.startswith("PostgreSQL 17"):
        errores.append(
            f"meta.version_servidor = {version!r}. El origen verificado es "
            "PostgreSQL 17.6; se exige 17.x para el cierre de B1.")

    base = str(meta.get("base", ""))
    if re.search(r"lab|5bp|5bl|5bq|local|test", base, re.IGNORECASE):
        errores.append(
            f"meta.base = {base!r} parece un laboratorio local. Una "
            "autocomparacion sintetica no es evidencia de B1.")

    firma_calculada = firma_catalogo(objetos) if objetos else None
    firma_declarada = catalogo.get("_firma")
    if firma_declarada and firma_calculada and firma_declarada != firma_calculada:
        errores.append(
            f"_firma declarada ({firma_declarada}) no coincide con la "
            f"recalculada ({firma_calculada}): el archivo fue editado o esta "
            "truncado. Un catalogo alterado no es evidencia.")
    if firma_rechazada and firma_calculada == firma_rechazada.lower():
        errores.append(
            f"La firma {firma_calculada} coincide con la firma excluida: es el "
            "mismo catalogo (autocomparacion), no el origen.")

    conteos = catalogo.get("conteos")
    if not isinstance(conteos, dict):
        errores.append("Falta el bloque 'conteos'.")
        conteos = {}

    derivados = {
        "tablas_public": sum(1 for fila in objetos.get("tablas", [])
                             if isinstance(fila, list) and fila and fila[0] == "public"),
        "triggers_public": sum(1 for fila in objetos.get("triggers", [])
                               if isinstance(fila, list) and fila and fila[0] == "public"),
        "rutinas_public": sum(1 for fila in objetos.get("rutinas", [])
                              if isinstance(fila, list) and fila and fila[0] == "public"),
        "vistas_public": sum(1 for fila in objetos.get("vistas", [])
                             if isinstance(fila, list) and fila and fila[0] == "public"),
    }
    for clave, valor in derivados.items():
        declarado = conteos.get(clave)
        if declarado is not None and declarado != valor:
            avisos.append(
                f"conteos.{clave} = {declarado} no coincide con los objetos "
                f"listados ({valor}). Revisar el archivo antes de comparar.")

    for clave in ("tablas_public", "triggers_public", "rutinas_public", "vistas_public"):
        esperado = REFERENCIA_EXTERNA[clave]
        obtenido = conteos.get(clave, derivados.get(clave))
        if obtenido is None:
            hallazgos.append(f"{clave}: ausente en el catalogo.")
        elif obtenido != esperado:
            hallazgos.append(
                f"{clave}: catalogo={obtenido}, referencia externa={esperado}.")

    politicas_public = sum(
        1 for fila in objetos.get("politicas", [])
        if isinstance(fila, list) and fila and fila[0] == "public")

    return {
        "archivo": str(ruta),
        "bytes": ruta.stat().st_size,
        "sha256": sha256_archivo(ruta),
        "apto": not errores,
        "errores": errores,
        "avisos": avisos,
        "hallazgos_vs_referencia": hallazgos,
        "datos": {
            "meta": meta,
            "firma_calculada": firma_calculada,
            "firma_declarada": firma_declarada,
            "conteos_declarados": conteos,
            "conteos_derivados": derivados,
            "politicas_public_listadas": politicas_public,
            "objetos_por_clave": {
                clave: len(objetos.get(clave, [])) if isinstance(objetos.get(clave), list) else None
                for clave in ARIDAD_OBJETOS
            },
            "referencia_externa": REFERENCIA_EXTERNA,
        },
    }


def _es_plataforma(clave: str) -> bool:
    if ":" not in clave:
        return False
    clase, _, identificador = clave.partition(":")
    identificador = identificador.strip()
    if clase == "rol":
        return (identificador in ROLES_PLATAFORMA
                or identificador.startswith("pg_")
                or identificador.startswith("supabase"))
    if clase == "extension":
        return identificador in EXTENSIONES_PLATAFORMA
    if clase == "privilegio_ejecucion":
        rol = identificador.split("->", 1)[0]
        return rol in ROLES_PLATAFORMA or rol.startswith("supabase")
    return False


def clasificar_informe(ruta: Path) -> dict:
    """Separa el informe de 87 en diferencias de plataforma y de aplicacion."""
    if not ruta.exists():
        raise InsumoInvalido(f"No existe el informe de 87: {ruta}")
    informe = json.loads(_leer_texto(ruta))
    resultado = informe.get("resultado")
    if not isinstance(resultado, dict):
        raise InsumoInvalido(
            f"{ruta.name} no tiene el bloque 'resultado'. No parece un informe "
            "de 87_comparar_catalogo_historico_5bp.py.")

    def _partir(claves: list[str]) -> dict:
        plataforma = sorted(c for c in claves if _es_plataforma(c))
        aplicacion = sorted(c for c in claves if not _es_plataforma(c))
        return {"plataforma": plataforma, "aplicacion": aplicacion}

    distintos = resultado.get("distintos", [])
    claves_distintos = [d.get("objeto", "") for d in distintos
                        if isinstance(d, dict)]
    solo_origen = resultado.get("solo_origen", [])
    solo_destino = resultado.get("solo_destino", [])

    agrupado = {
        "solo_origen": _partir(list(solo_origen)),
        "solo_destino": _partir(list(solo_destino)),
        "atributos_distintos": _partir(claves_distintos),
    }
    resumen = {clave: {grupo: len(valores) for grupo, valores in grupos.items()}
               for clave, grupos in agrupado.items()}

    aplicacion_total = sum(
        resumen[clave]["aplicacion"] for clave in resumen)
    return {
        "informe": str(ruta),
        "veredicto_87": informe.get("veredicto"),
        "resumen_por_clase": resumen,
        "diferencias_de_aplicacion": aplicacion_total,
        "diferencias_de_plataforma": sum(
            resumen[clave]["plataforma"] for clave in resumen),
        "detalle": agrupado,
        "nota": (
            "Clasificacion por listas estaticas y documentadas de roles y "
            "extensiones gestionados por Supabase. No altera el veredicto de 87 "
            "ni declara B1 cerrada: solo separa el ruido de plataforma del "
            "esquema historico de la aplicacion."
        ),
    }


def plan_comparacion(catalogo: Path, dump: Path | None) -> list[str]:
    comando = [
        "python database/saas/87_comparar_catalogo_historico_5bp.py",
        f'--origen-catalogo "{catalogo}"',
        "--destino-catalogo <catalogo_del_laboratorio_PG17>.json",
        "--json",
        "--salida database/saas/evidencias/EVIDENCIA_ESQUEMA_5BQ_<sello>.json",
    ]
    if dump is not None:
        comando.insert(3, f'--origen-dump "{dump}"')
    return [" ".join(comando)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="5BQ: valida offline el insumo autorizado del esquema historico")
    parser.add_argument("--dump", default=None, help="pg_dump --schema-only del origen")
    parser.add_argument("--catalogo", default=None, help="catalogo JSON del origen")
    parser.add_argument("--firma-rechazada", default=None,
                        help="firma que debe rechazarse (autocomparacion del laboratorio)")
    parser.add_argument("--informe-87", default=None,
                        help="informe JSON de 87 para clasificar plataforma/aplicacion")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--salida", default=None, help="escribe el resultado en JSON")
    args = parser.parse_args(argv)

    if not any([args.dump, args.catalogo, args.informe_87]):
        print("ERROR: indique --dump, --catalogo o --informe-87", file=sys.stderr)
        return 2

    informe = {
        "etapa": "5BQ / Etapa H",
        "validador": "94_validar_insumo_esquema_5bq.py",
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "referencia_externa": REFERENCIA_EXTERNA,
        "dump": None,
        "catalogo": None,
        "informe_87": None,
        "plan_comparacion": None,
    }
    invalido = False
    con_hallazgos = False

    try:
        if args.dump:
            informe["dump"] = validar_dump(Path(args.dump))
            invalido = invalido or not informe["dump"]["apto"]
        if args.catalogo:
            informe["catalogo"] = validar_catalogo(
                Path(args.catalogo), args.firma_rechazada)
            invalido = invalido or not informe["catalogo"]["apto"]
            con_hallazgos = con_hallazgos or bool(
                informe["catalogo"]["hallazgos_vs_referencia"])
            if informe["catalogo"]["apto"]:
                informe["plan_comparacion"] = plan_comparacion(
                    Path(args.catalogo),
                    Path(args.dump) if args.dump else None)
        if args.informe_87:
            informe["informe_87"] = clasificar_informe(Path(args.informe_87))
    except InsumoInvalido as exc:
        informe["error"] = str(exc)
        invalido = True

    if invalido:
        codigo = 5
        informe["veredicto"] = "INSUMO NO APTO (B1 sigue bloqueada)"
    elif con_hallazgos:
        codigo = 4
        informe["veredicto"] = "INSUMO APTO CON DISCREPANCIAS FRENTE A LA REFERENCIA"
    else:
        codigo = 0
        informe["veredicto"] = "INSUMO APTO"
    informe["codigo_salida"] = codigo

    texto = json.dumps(informe, indent=2, ensure_ascii=False)
    if args.salida:
        destino = Path(args.salida)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto + "\n", encoding="utf-8")
        print(f"Informe escrito: {destino}")
    if args.json:
        print(texto)

    print(f"VEREDICTO: {informe['veredicto']}")
    if "error" in informe:
        print(f"  ERROR: {informe['error']}")
    for bloque in ("dump", "catalogo"):
        datos = informe.get(bloque)
        if not datos:
            continue
        for error in datos["errores"]:
            print(f"  [{bloque}] ERROR: {error}")
        for aviso in datos["avisos"]:
            print(f"  [{bloque}] AVISO: {aviso}")
    if informe.get("catalogo"):
        for hallazgo in informe["catalogo"]["hallazgos_vs_referencia"]:
            print(f"  [catalogo] REFERENCIA: {hallazgo}")
    if informe.get("informe_87"):
        resumen = informe["informe_87"]["resumen_por_clase"]
        print(f"  [87] diferencias de aplicacion: "
              f"{informe['informe_87']['diferencias_de_aplicacion']} | "
              f"de plataforma: {informe['informe_87']['diferencias_de_plataforma']}")
        print(f"  [87] detalle: {json.dumps(resumen, ensure_ascii=False)}")
    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
