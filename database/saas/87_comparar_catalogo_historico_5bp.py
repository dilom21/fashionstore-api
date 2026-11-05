"""5BP / Etapa G - Comparador determinista entre catalogo historico y laboratorio.

Principio
---------
Este script **no inventa** objetos ni porcentajes de fidelidad. Solo compara dos
catalogos reales:

* ``origen``  : catalogo autorizado del esquema historico. Se obtiene con el
  procedimiento ya existente ``80_extraccion_esquema_autorizada_5bo.ps1`` +
  ``81_metadatos_catalogo_solo_lectura_5bo.sql`` (mas ``pg_dump --schema-only``)
  ejecutados por el estudiante con acceso de SOLO LECTURA al origen autorizado.
  El resultado se guarda FUERA de Git y no se envia a ningun servicio externo.
* ``destino`` : catalogo del laboratorio (reconstruccion 5BL + migraciones
  SaaS), exportado por este mismo script desde un PostgreSQL de loopback.

Si el catalogo de origen no existe, el veredicto es ``NO DISPONIBLE`` con codigo
de salida 3. Nunca se degrada a lista vacia ni a ``VALIDADA``.

Comparacion por OBJETO, no por cantidad
---------------------------------------
Cada objeto se identifica por firma estable (esquema + nombre + argumentos para
rutinas) y se comparan atributos: propietario, ``SECURITY DEFINER``,
``search_path``, ACL efectiva, columnas ``empresa_id``, RLS/FORCE RLS, triggers
habilitados, politicas y extensiones. Un conteo igual con objetos distintos es
una diferencia, no una coincidencia.

Uso
---
    # 1) Exportar el catalogo del laboratorio (loopback unicamente)
    python database/saas/87_comparar_catalogo_historico_5bp.py \
        --exportar-dsn "postgresql://postgres:***@127.0.0.1:55446/fashionstore_lab_pg17_5bp" \
        --salida-catalogo catalogo_lab_5bp.json

    # 2) Comparar con el catalogo autorizado (fuera del repositorio)
    python database/saas/87_comparar_catalogo_historico_5bp.py \
        --origen-catalogo D:\\autorizado\\catalogo_origen.json \
        --destino-catalogo catalogo_lab_5bp.json \
        --dump D:\\autorizado\\esquema_origen.sql \
        --json --salida database/saas/evidencias/EVIDENCIA_ESQUEMA_5BP_x.json

Codigos de salida: 0 = sin diferencias, 1 = diferencias, 3 = origen no disponible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
ESQUEMAS = ("public", "saas", "saas_admin")
ESQUEMAS_RLS = ("public", "saas")
ROLES_AUDITADOS = (
    "fs_saas_runtime", "saas_admin_runtime", "saas_admin_owner",
    "saas_personal_owner", "saas_rls_owner", "saas_actor_sql_owner",
)

CONSULTAS = {
    "tablas": """
        SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p') AND n.nspname = ANY(%(esquemas)s)
         ORDER BY 1, 2
    """,
    "vistas": """
        SELECT n.nspname, c.relname,
               COALESCE((SELECT option_value FROM pg_options_to_table(c.reloptions)
                          WHERE option_name = 'security_invoker'), '<no definido>')
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind = 'v' AND n.nspname = ANY(%(esquemas_rls)s)
         ORDER BY 1, 2
    """,
    "columnas_empresa_id": """
        SELECT table_schema, table_name
          FROM information_schema.columns
         WHERE column_name = 'empresa_id' AND table_schema = ANY(%(esquemas_rls)s)
         ORDER BY 1, 2
    """,
    "rutinas": """
        SELECT n.nspname, p.proname,
               CASE p.prokind WHEN 'f' THEN 'funcion' WHEN 'p' THEN 'procedimiento'
                              ELSE p.prokind::text END,
               p.prosecdef,
               pg_get_userbyid(p.proowner),
               COALESCE(array_to_string(p.proconfig, ','), '<sin search_path>'),
               pg_get_function_identity_arguments(p.oid)
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = ANY(%(esquemas)s)
         ORDER BY 1, 2, 7
    """,
    "triggers": """
        SELECT n.nspname, c.relname, t.tgname, p.proname, p.prosecdef, t.tgenabled
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_proc p ON p.oid = t.tgfoid
         WHERE NOT t.tgisinternal AND n.nspname = ANY(%(esquemas_rls)s)
         ORDER BY 1, 2, 3
    """,
    "politicas": """
        SELECT schemaname, tablename, policyname, cmd,
               array_to_string(roles, ','), COALESCE(qual, '<ninguno>'),
               COALESCE(with_check, '<ninguno>')
          FROM pg_policies
         WHERE schemaname = ANY(%(esquemas_rls)s)
         ORDER BY 1, 2, 3
    """,
    "propietarios": """
        SELECT n.nspname, c.relname, pg_get_userbyid(c.relowner)
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p', 'v', 'm') AND n.nspname = ANY(%(esquemas)s)
         ORDER BY 1, 2
    """,
    "roles": """
        SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolinherit
          FROM pg_roles
         WHERE rolname !~ '^pg_'
         ORDER BY rolname
    """,
    "extensiones": """
        SELECT e.extname, e.extversion, n.nspname
          FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
         ORDER BY 1
    """,
    "privilegios_rutinas": """
        SELECT r.rolname, n.nspname || '.' || p.proname,
               has_function_privilege(r.oid, p.oid, 'EXECUTE')
          FROM pg_roles r
          CROSS JOIN pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname IN ('saas', 'saas_admin')
           AND r.rolname = ANY(%(roles)s)
         ORDER BY 1, 2
    """,
    "conteos": """
        SELECT
          (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind='r' AND n.nspname='public'),
          (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
             JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE NOT t.tgisinternal AND n.nspname='public'),
          (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public'),
          (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind='v' AND n.nspname='public')
    """,
}


class ComparacionError(RuntimeError):
    pass


def _solo_loopback(dsn: str) -> None:
    parsed = urlparse(dsn)
    if (parsed.hostname or "") not in {"127.0.0.1", "localhost", "::1"}:
        raise ComparacionError(
            "Solo se permite exportar el catalogo de un laboratorio en loopback. "
            "El origen autorizado se extrae con 80/81 ejecutados por el estudiante, "
            "no con este script.")


def exportar_catalogo(dsn: str) -> dict:
    import psycopg

    _solo_loopback(dsn)
    parametros = {
        "esquemas": list(ESQUEMAS),
        "esquemas_rls": list(ESQUEMAS_RLS),
        "roles": list(ROLES_AUDITADOS),
    }
    datos: dict = {"meta": {}, "objetos": {}}
    with psycopg.connect(dsn) as conn:
        fila = conn.execute(
            "SELECT version(), current_database(), current_user").fetchone()
        datos["meta"] = {
            "version_servidor": fila[0].split(" on ")[0],
            "base": fila[1],
            "usuario": fila[2],
            "capturado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "procedimiento": "87_comparar_catalogo_historico_5bp.py (SELECT de catalogo)",
        }
        for nombre, consulta in CONSULTAS.items():
            filas = conn.execute(consulta, parametros).fetchall()
            if nombre == "conteos":
                datos["conteos"] = {
                    "tablas_public": filas[0][0], "triggers_public": filas[0][1],
                    "rutinas_public": filas[0][2], "vistas_public": filas[0][3],
                }
                continue
            datos["objetos"][nombre] = [list(f) for f in filas]
    datos["_firma"] = firma_catalogo(datos)
    return datos


def firma_catalogo(catalogo: dict) -> str:
    base = json.dumps(catalogo.get("objetos", {}), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Normalizacion a objetos con firma estable
# ---------------------------------------------------------------------------


def _objetos(catalogo: dict) -> dict[str, dict]:
    objetos: dict[str, dict] = {}
    datos = catalogo.get("objetos", {})

    for schema, tabla, rls, force in datos.get("tablas", []):
        objetos[f"tabla:{schema}.{tabla}"] = {
            "clase": "tabla", "rls": bool(rls), "force_rls": bool(force)}

    for schema, vista, invoker in datos.get("vistas", []):
        objetos[f"vista:{schema}.{vista}"] = {
            "clase": "vista", "security_invoker": invoker or "<no definido>"}

    for schema, tabla in datos.get("columnas_empresa_id", []):
        objetos[f"empresa_id:{schema}.{tabla}"] = {"clase": "columna_empresa_id"}

    for schema, nombre, tipo, secdef, propietario, configuracion, args in datos.get(
            "rutinas", []):
        objetos[f"rutina:{schema}.{nombre}({args})"] = {
            "clase": "rutina", "tipo": tipo, "security_definer": bool(secdef),
            "propietario": propietario, "search_path": configuracion}

    for schema, tabla, nombre, funcion, secdef, habilitado in datos.get(
            "triggers", []):
        objetos[f"trigger:{schema}.{tabla}.{nombre}"] = {
            "clase": "trigger", "funcion": funcion,
            "security_definer": bool(secdef), "habilitado": habilitado}

    for schema, tabla, nombre, cmd, roles, cual, with_check in datos.get(
            "politicas", []):
        objetos[f"politica:{schema}.{tabla}.{nombre}"] = {
            "clase": "politica", "cmd": cmd, "roles": roles,
            "using": _normalizar_expresion(cual),
            "with_check": _normalizar_expresion(with_check)}

    for schema, objeto, propietario in datos.get("propietarios", []):
        clave = f"propietario:{schema}.{objeto}"
        objetos.setdefault(clave, {"clase": "propietario_sin_tabla"})
        objetos[clave]["propietario"] = propietario

    for nombre, login, superusuario, bypassrls, inherit in datos.get("roles", []):
        objetos[f"rol:{nombre}"] = {
            "clase": "rol", "login": bool(login), "super": bool(superusuario),
            "bypassrls": bool(bypassrls), "inherit": bool(inherit)}

    for nombre, version, schema in datos.get("extensiones", []):
        objetos[f"extension:{nombre}"] = {
            "clase": "extension", "version": version, "schema": schema}

    for rol, rutina, puede in datos.get("privilegios_rutinas", []):
        objetos[f"execute:{rol}->{rutina}"] = {
            "clase": "privilegio_ejecucion", "puede_ejecutar": bool(puede)}

    return objetos


def _normalizar_expresion(texto: str) -> str:
    """Normaliza espacios y calificaciones para comparar semantica, no formato."""
    if texto in (None, "", "<ninguno>"):
        return "<ninguno>"
    limpio = re.sub(r"\s+", " ", str(texto)).strip()
    limpio = limpio.replace("::text", "").replace("(('", "(('")
    return limpio


RIESGO = {
    "rol": "CRITICO",
    "privilegio_ejecucion": "ALTO",
    "rutina": "ALTO",
    "trigger": "ALTO",
    "politica": "ALTO",
    "tabla": "MEDIO",
    "columna_empresa_id": "ALTO",
    "propietario": "ALTO",
    "vista": "MEDIO",
    "extension": "MEDIO",
}


def comparar(origen: dict, destino: dict) -> dict:
    obj_origen = _objetos(origen)
    obj_destino = _objetos(destino)

    solo_origen = sorted(set(obj_origen) - set(obj_destino))
    solo_destino = sorted(set(obj_destino) - set(obj_origen))
    distintos: list[dict] = []
    iguales = 0

    for clave in sorted(set(obj_origen) & set(obj_destino)):
        a, b = obj_origen[clave], obj_destino[clave]
        atributos = sorted(
            k for k in (set(a) | set(b))
            if k != "clase" and a.get(k) != b.get(k)
        )
        if atributos:
            distintos.append({
                "objeto": clave,
                "clase": a.get("clase"),
                "atributos": atributos,
                "origen": {k: a.get(k) for k in atributos},
                "destino": {k: b.get(k) for k in atributos},
                "riesgo": RIESGO.get(a.get("clase", ""), "MEDIO"),
            })
        else:
            iguales += 1

    conteos_origen = origen.get("conteos", {})
    conteos_destino = destino.get("conteos", {})
    resumen = {
        "objetos_origen": len(obj_origen),
        "objetos_destino": len(obj_destino),
        "iguales": iguales,
        "solo_origen": len(solo_origen),
        "solo_destino": len(solo_destino),
        "atributos_distintos": len(distintos),
        "conteos_origen": conteos_origen,
        "conteos_destino": conteos_destino,
    }
    sin_diferencias = not (solo_origen or solo_destino or distintos)
    return {
        "resumen": resumen,
        "solo_origen": solo_origen,
        "solo_destino": solo_destino,
        "distintos": distintos,
        "sin_diferencias": sin_diferencias,
        "fidelidad_por_clase": _fidelidad_por_clase(obj_origen, obj_destino),
    }


def _fidelidad_por_clase(origen: dict, destino: dict) -> dict:
    clases: dict[str, dict] = {}
    for clave, objeto in origen.items():
        clase = objeto.get("clase")
        registro = clases.setdefault(clase, {"origen": 0, "coincidentes": 0})
        registro["origen"] += 1
        if clave in destino:
            registro["coincidentes"] += 1
    for clase, registro in clases.items():
        registro["porcentaje"] = round(
            100.0 * registro["coincidentes"] / registro["origen"], 2
        ) if registro["origen"] else None
    return clases


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _instrucciones_origen() -> dict:
    return {
        "estado": "BLOQUEADO POR EVIDENCIA NO DISPONIBLE",
        "bloqueo": "B1",
        "que_falta": [
            "Catalogo del esquema historico desde un origen AUTORIZADO (solo lectura).",
            "pg_dump --schema-only del mismo origen (sin --no-owner ni --no-acl).",
        ],
        "como_obtenerlo": [
            "Ejecutar en el equipo del estudiante, con credenciales de solo lectura:",
            "  powershell -File database/saas/80_extraccion_esquema_autorizada_5bo.ps1 "
            "-Autorizado -HostOrigen <host> -BaseOrigen <db> -Salida <carpeta_fuera_de_git>",
            "  psql -h <host> -p <puerto> -U <usuario_solo_lectura> -d <db> "
            "-f database/saas/81_metadatos_catalogo_solo_lectura_5bo.sql "
            "> <carpeta_fuera_de_git>\\catalogo_origen.txt",
            "Convertir ese catalogo a JSON con el mismo formato que produce este "
            "script (--exportar-dsn sobre un laboratorio) o adjuntar el JSON "
            "equivalente generado por el procedimiento autorizado.",
            "NUNCA guardar el dump ni el catalogo dentro del repositorio ni enviarlo "
            "a modelos o servicios externos.",
        ],
        "por_que_no_se_inventa": (
            "La reconstruccion 5BL solo reproduce 14 de 27 triggers y 13 de 24 "
            "rutinas documentadas. Completar el resto por conjetura produciria una "
            "validacion falsa."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="5BP: comparador determinista de catalogo historico vs laboratorio")
    parser.add_argument("--exportar-dsn", default=None,
                        help="DSN de loopback para exportar el catalogo del laboratorio")
    parser.add_argument("--salida-catalogo", default=None,
                        help="ruta donde guardar el catalogo exportado")
    parser.add_argument("--origen-catalogo", default=None,
                        help="catalogo JSON del esquema historico AUTORIZADO")
    parser.add_argument("--destino-catalogo", default=None,
                        help="catalogo JSON del laboratorio")
    parser.add_argument("--origen-dump", default=None,
                        help="pg_dump --schema-only del origen (solo se registra hash)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--salida", default=None, help="escribe el informe JSON")
    args = parser.parse_args(argv)

    try:
        if args.exportar_dsn:
            catalogo = exportar_catalogo(args.exportar_dsn)
            destino = Path(args.salida_catalogo) if args.salida_catalogo else None
            if destino:
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_text(
                    json.dumps(catalogo, indent=2, ensure_ascii=False),
                    encoding="utf-8")
                print(f"Catalogo exportado: {destino}")
            else:
                print(json.dumps(catalogo, indent=2, ensure_ascii=False))
            return 0

        if not args.origen_catalogo:
            bloqueo = _instrucciones_origen()
            print(json.dumps(bloqueo, indent=2, ensure_ascii=False))
            return 3

        ruta_origen = Path(args.origen_catalogo)
        if not ruta_origen.exists():
            bloqueo = _instrucciones_origen()
            bloqueo["detalle"] = f"No existe el archivo indicado: {ruta_origen}"
            print(json.dumps(bloqueo, indent=2, ensure_ascii=False))
            return 3

        if not args.destino_catalogo:
            print("ERROR: falta --destino-catalogo (catalogo del laboratorio)",
                  file=sys.stderr)
            return 2

        origen = json.loads(ruta_origen.read_text(encoding="utf-8"))
        destino = json.loads(Path(args.destino_catalogo).read_text(encoding="utf-8"))
        resultado = comparar(origen, destino)

        informe = {
            "etapa": "5BP / Etapa G",
            "comparador": "87_comparar_catalogo_historico_5bp.py",
            "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "origen": {
                "archivo": str(ruta_origen),
                "meta": origen.get("meta", {}),
                "firma": origen.get("_firma"),
            },
            "destino": {
                "archivo": str(args.destino_catalogo),
                "meta": destino.get("meta", {}),
                "firma": destino.get("_firma"),
            },
            "dump_origen": None,
            "resultado": resultado,
            "veredicto": (
                "SIN DIFERENCIAS" if resultado["sin_diferencias"]
                else "CON DIFERENCIAS"),
        }

        if args.origen_dump:
            ruta_dump = Path(args.origen_dump)
            if not ruta_dump.exists():
                informe["dump_origen"] = {"estado": "NO DISPONIBLE"}
            else:
                informe["dump_origen"] = {
                    "archivo": str(ruta_dump),
                    "bytes": ruta_dump.stat().st_size,
                    "sha256": hashlib.sha256(ruta_dump.read_bytes()).hexdigest(),
                }

        texto = json.dumps(informe, indent=2, ensure_ascii=False)
        if args.salida:
            ruta_salida = Path(args.salida)
            ruta_salida.parent.mkdir(parents=True, exist_ok=True)
            ruta_salida.write_text(texto + "\n", encoding="utf-8")
            print(f"Informe escrito: {ruta_salida}")
        if args.json:
            print(texto)
        else:
            resumen = resultado["resumen"]
            print(f"Origen : {resumen['objetos_origen']} objetos")
            print(f"Destino: {resumen['objetos_destino']} objetos")
            print(f"Iguales: {resumen['iguales']} | solo origen: "
                  f"{resumen['solo_origen']} | solo destino: {resumen['solo_destino']} "
                  f"| atributos distintos: {resumen['atributos_distintos']}")
            print(f"Veredicto: {informe['veredicto']}")
            for diferencia in resultado["distintos"][:20]:
                print(f"  [{diferencia['riesgo']}] {diferencia['objeto']}: "
                      f"{', '.join(diferencia['atributos'])}")

        return 0 if resultado["sin_diferencias"] else 1

    except ComparacionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
