"""5BR / Etapa I - Matriz de decisiones entre esquema ORIGINAL, reconstruccion 5BL y laboratorio migrado.

Que compara (por OBJETO, nunca por conteos)
-------------------------------------------
1. ``ORIGEN``      catalogo autorizado del esquema ``public`` real (41/27/24/1).
2. ``RECONSTRUCCION`` objetos que declara la reconstruccion 5BL (SQL propio + tablas del
   mapeo ORM). Se deriva del texto versionado, no de memoria ni de conjeturas.
3. ``LAB``         catalogo exportado del cluster 5BR: primero tras restaurar el
   original y despues de aplicar el pipeline SaaS.

Salidas
-------
* ``--fase reconstruccion`` : lista EXACTA de objetos originales ausentes de 5BL.
* ``--fase restaurado``     : fidelidad de la restauracion (debe ser 0 diferencias).
* ``--fase migrado``        : por cada objeto original, si sigue presente, si cambio
  owner/SECURITY DEFINER/search_path/trigger y con que clasificacion.

Clasificaciones (vocabulario cerrado del master prompt)
-------------------------------------------------------
``CONSERVAR``               el objeto original sobrevive con identidad y contrato iguales.
``ADAPTAR A TENANT``        sobrevive pero una migracion versionada fijo search_path,
                            retiro SECURITY DEFINER o lo ato al tenant; el cambio esta
                            declarado en 20261030000500 / 20261030000550.
``REEMPLAZAR JUSTIFICADO``  la firma cambia o el objeto se sustituye por un equivalente
                            tenant-aware versionado; se documenta el reemplazo.
``RIESGO SIN RESOLVER``     el objeto original desaparece o queda inservible sin decision.
``SOLO OBJETO NUEVO SAAS``  existe en el laboratorio y no en el origen: aporte del SaaS.

El informe es sanitizado: nombres, firmas, atributos y hashes. Nunca incluye DDL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUTA_CATALOGO_ORIGEN = Path(
    r"C:\Users\josia\Documents\fashionstore-esquema-5bq"
    r"\catalogo_20261031_115806.json"
)
RUTA_RECONSTRUCCION = ROOT / "database" / "saas" / "75_esquema_historico_reconstruido_5bl.sql"
DIR_APP = ROOT / "app" / "modules"

# Migraciones que pueden cambiar atributos de objetos historicos. Cualquier
# diferencia NO atribuible a ellas se trata como riesgo sin resolver.
MIGRACIONES_QUE_TOCAN_HISTORICO = {
    "20261030000500_saas_compatibilidad_5bl.sql": {
        "fn_registrar_bitacora", "fn_actualizar_actividad_carrito",
        "fn_validar_sucursal_detalle_carrito", "fn_validar_detalle_reserva_sucursal",
        "fn_controlar_actualizacion_inventario", "fn_normalizar_correo_usuario",
        "fn_completar_ip_bitacora", "fn_proteger_detalle_venta",
        "fn_proteger_detalle_orden_compra",
    },
    "20261030000550_saas_compatibilidad_esquema_real_5br.sql": {
        "fn_normalizar_sku", "fn_proteger_eliminacion_venta",
        "fn_recalcular_total_venta_trigger", "fn_validar_detalle_devolucion",
        "fn_validar_detalle_venta_sucursal", "fn_validar_producto_proveedor_orden",
    },
    "20261030000800_saas_personal_rbac_5bo.sql": {
        "fn_registrar_bitacora",
    },
}

OBJETOS_ADAPTADOS = {
    nombre
    for objetos in MIGRACIONES_QUE_TOCAN_HISTORICO.values()
    for nombre in objetos
}

TABLAS_REEMPLAZADAS_TENANT = {
    # La tabla original se conserva; lo que cambia es que gana empresa_id y
    # restricciones por empresa. Se listan aqui para explicar la matriz.
}


class ComparacionError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Normalizacion
# ---------------------------------------------------------------------------


def cargar_origen() -> dict:
    if not RUTA_CATALOGO_ORIGEN.exists():
        raise ComparacionError(f"No existe el catalogo autorizado: {RUTA_CATALOGO_ORIGEN}")
    return json.loads(RUTA_CATALOGO_ORIGEN.read_text(encoding="utf-8"))


def tablas_reconstruccion() -> set[str]:
    encontradas: set[str] = set()
    for ruta in DIR_APP.rglob("*.py"):
        texto = ruta.read_text(encoding="utf-8", errors="ignore")
        encontradas.update(re.findall(r'__tablename__\s*=\s*"([a-z0-9_]+)"', texto))
    return encontradas


def objetos_reconstruccion() -> dict:
    texto = RUTA_RECONSTRUCCION.read_text(encoding="utf-8")
    funciones = set(re.findall(
        r"CREATE OR REPLACE FUNCTION public\.([a-z0-9_]+)\s*\(", texto))
    procedimientos = set(re.findall(
        r"CREATE OR REPLACE PROCEDURE public\.([a-z0-9_]+)\s*\(", texto))
    triggers = set(re.findall(r"CREATE TRIGGER ([a-z0-9_]+)", texto))
    vistas = set(re.findall(r"CREATE OR REPLACE VIEW public\.([a-z0-9_]+)", texto))
    return {
        "tablas": tablas_reconstruccion(),
        "rutinas": funciones | procedimientos,
        "funciones": funciones,
        "procedimientos": procedimientos,
        "triggers": triggers,
        "vistas": vistas,
    }


def objetos_origen(catalogo: dict) -> dict:
    objetos = catalogo["objetos"]
    rutinas = {}
    for schema, nombre, tipo, secdef, propietario, configuracion, args in objetos["rutinas"]:
        rutinas[nombre] = {
            "tipo": tipo, "security_definer": bool(secdef),
            "propietario": propietario, "search_path": configuracion, "args": args,
        }
    triggers = {}
    for schema, tabla, nombre, funcion, secdef, habilitado in objetos["triggers"]:
        triggers[nombre] = {
            "tabla": tabla, "funcion": funcion,
            "security_definer": bool(secdef), "habilitado": habilitado,
        }
    return {
        "tablas": {f[1]: {"rls": bool(f[2]), "force_rls": bool(f[3])}
                   for f in objetos["tablas"]},
        "rutinas": rutinas,
        "triggers": triggers,
        "vistas": {f[1]: {"security_invoker_hint": f[2]} for f in objetos["vistas"]},
    }


def objetos_lab(catalogo: dict, esquema: str = "public") -> dict:
    objetos = catalogo["objetos"]
    rutinas = {}
    for schema, nombre, tipo, secdef, propietario, configuracion, args in objetos["rutinas"]:
        if schema != esquema:
            continue
        rutinas[nombre] = {
            "tipo": tipo, "security_definer": bool(secdef),
            "propietario": propietario, "search_path": configuracion, "args": args,
        }
    triggers = {}
    for schema, tabla, nombre, funcion, secdef, habilitado in objetos["triggers"]:
        if schema != esquema:
            continue
        triggers[nombre] = {"tabla": tabla, "funcion": funcion,
                            "security_definer": bool(secdef), "habilitado": habilitado}
    return {
        "tablas": {f[1]: {"rls": bool(f[2]), "force_rls": bool(f[3])}
                   for f in objetos["tablas"] if f[0] == esquema},
        "rutinas": rutinas,
        "triggers": triggers,
        "vistas": {f[1]: {"security_invoker_hint": f[2]}
                   for f in objetos["vistas"] if f[0] == esquema},
    }


# ---------------------------------------------------------------------------
# Comparaciones
# ---------------------------------------------------------------------------


def comparar_reconstruccion(origen: dict, reconstruccion: dict) -> dict:
    o = objetos_origen(origen)
    faltan_tablas = sorted(set(o["tablas"]) - reconstruccion["tablas"])
    faltan_rutinas = sorted(set(o["rutinas"]) - reconstruccion["rutinas"])
    faltan_triggers = sorted(set(o["triggers"]) - reconstruccion["triggers"])
    faltan_vistas = sorted(set(o["vistas"]) - reconstruccion["vistas"])
    return {
        "origen": {"tablas": len(o["tablas"]), "triggers": len(o["triggers"]),
                   "rutinas": len(o["rutinas"]), "vistas": len(o["vistas"])},
        "reconstruccion": {
            "tablas": len(reconstruccion["tablas"]),
            "triggers": len(reconstruccion["triggers"]),
            "rutinas": len(reconstruccion["rutinas"]),
            "vistas": len(reconstruccion["vistas"]),
        },
        "tablas_ausentes": faltan_tablas,
        "triggers_ausentes": faltan_triggers,
        "rutinas_ausentes": faltan_rutinas,
        "vistas_ausentes": faltan_vistas,
        "detalle_triggers_ausentes": {
            n: o["triggers"][n] for n in faltan_triggers},
        "detalle_rutinas_ausentes": {
            n: o["rutinas"][n] for n in faltan_rutinas},
        "coincide": not (faltan_tablas or faltan_rutinas or faltan_triggers
                         or faltan_vistas),
    }


def comparar_restauracion(origen: dict, lab: dict) -> dict:
    o = objetos_origen(origen)
    l = objetos_lab(lab)
    discrepancias: list[dict] = []
    for nombre, datos in o["rutinas"].items():
        if nombre not in l["rutinas"]:
            discrepancias.append({"objeto": f"rutina:{nombre}", "motivo": "ausente"})
            continue
        for atributo in ("security_definer", "search_path", "args", "propietario"):
            if datos[atributo] != l["rutinas"][nombre][atributo]:
                discrepancias.append({
                    "objeto": f"rutina:{nombre}", "atributo": atributo,
                    "origen": datos[atributo], "lab": l["rutinas"][nombre][atributo]})
    for nombre, datos in o["triggers"].items():
        if nombre not in l["triggers"]:
            discrepancias.append({"objeto": f"trigger:{nombre}", "motivo": "ausente"})
            continue
        for atributo in ("tabla", "funcion", "habilitado"):
            if datos[atributo] != l["triggers"][nombre][atributo]:
                discrepancias.append({
                    "objeto": f"trigger:{nombre}", "atributo": atributo,
                    "origen": datos[atributo], "lab": l["triggers"][nombre][atributo]})
    for nombre in o["tablas"]:
        if nombre not in l["tablas"]:
            discrepancias.append({"objeto": f"tabla:{nombre}", "motivo": "ausente"})
    return {
        "objetos_origen": len(o["tablas"]) + len(o["rutinas"]) + len(o["triggers"]),
        "discrepancias": discrepancias,
        "fidelidad_estructura": (1.0 if not discrepancias else
                                 round(1 - len(discrepancias) /
                                       max(1, len(o["tablas"]) + len(o["rutinas"])
                                           + len(o["triggers"])), 4)),
        "identica": not discrepancias,
    }


def clasificar(diferencia: dict) -> str:
    objeto = diferencia["objeto"]
    nombre = objeto.split(":", 1)[1].split("(", 1)[0]
    motivo = diferencia.get("motivo")
    atributo = diferencia.get("atributo")
    if motivo == "ausente":
        return ("REEMPLAZAR JUSTIFICADO" if nombre in OBJETOS_ADAPTADOS
                else "RIESGO SIN RESOLVER")
    if atributo in ("search_path", "security_definer"):
        return ("ADAPTAR A TENANT" if nombre in OBJETOS_ADAPTADOS
                else "RIESGO SIN RESOLVER")
    if atributo == "args":
        return "REEMPLAZAR JUSTIFICADO" if nombre in OBJETOS_ADAPTADOS else "RIESGO SIN RESOLVER"
    if atributo == "propietario":
        # 00500 y 00800 cambian el propietario a un NOLOGIN por diseno.
        return "ADAPTAR A TENANT" if nombre in OBJETOS_ADAPTADOS else "RIESGO SIN RESOLVER"
    return "RIESGO SIN RESOLVER"


def comparar_migrado(origen: dict, lab: dict) -> dict:
    o = objetos_origen(origen)
    l = objetos_lab(lab)

    diferencias: list[dict] = []
    conservados = 0
    for nombre, datos in o["rutinas"].items():
        if nombre not in l["rutinas"]:
            diferencias.append({"objeto": f"rutina:{nombre}", "motivo": "ausente"})
            continue
        actual = l["rutinas"][nombre]
        cambios = {k: {"origen": datos[k], "migrado": actual[k]}
                   for k in ("security_definer", "search_path", "propietario", "args")
                   if datos[k] != actual[k]}
        if cambios:
            for atributo, valores in cambios.items():
                diferencias.append({
                    "objeto": f"rutina:{nombre}", "atributo": atributo,
                    "origen": valores["origen"], "migrado": valores["migrado"]})
        else:
            conservados += 1

    for nombre, datos in o["triggers"].items():
        if nombre not in l["triggers"]:
            diferencias.append({"objeto": f"trigger:{nombre}", "motivo": "ausente"})
            continue
        actual = l["triggers"][nombre]
        cambios = {k: {"origen": datos[k], "migrado": actual[k]}
                   for k in ("tabla", "funcion", "habilitado")
                   if datos[k] != actual[k]}
        if cambios:
            for atributo, valores in cambios.items():
                diferencias.append({
                    "objeto": f"trigger:{nombre}", "atributo": atributo,
                    "origen": valores["origen"], "migrado": valores["migrado"]})
        else:
            conservados += 1

    for nombre in o["tablas"]:
        if nombre not in l["tablas"]:
            diferencias.append({"objeto": f"tabla:{nombre}", "motivo": "ausente"})
        else:
            conservados += 1

    for nombre in o["vistas"]:
        if nombre not in l["vistas"]:
            diferencias.append({"objeto": f"vista:{nombre}", "motivo": "ausente"})
        else:
            conservados += 1

    for diferencia in diferencias:
        diferencia["clasificacion"] = clasificar(diferencia)

    nuevos_saas = sorted(
        f"rutina:{n}" for n in set(l["rutinas"]) - set(o["rutinas"]))
    nuevos_saas += sorted(
        f"trigger:{n}" for n in set(l["triggers"]) - set(o["triggers"]))
    nuevos_saas += sorted(
        f"tabla:{n}" for n in set(l["tablas"]) - set(o["tablas"]))
    nuevos_saas += sorted(
        f"vista:{n}" for n in set(l["vistas"]) - set(o["vistas"]))

    total = len(o["tablas"]) + len(o["rutinas"]) + len(o["triggers"]) + len(o["vistas"])
    por_clase: dict[str, int] = {}
    for diferencia in diferencias:
        por_clase[diferencia["clasificacion"]] = por_clase.get(
            diferencia["clasificacion"], 0) + 1

    return {
        "objetos_originales": total,
        "conservados_sin_cambio": conservados,
        "diferencias": diferencias,
        "clasificacion_diferencias": por_clase,
        "riesgos_sin_resolver": [d for d in diferencias
                                 if d["clasificacion"] == "RIESGO SIN RESOLVER"],
        "objetos_nuevos_saas": nuevos_saas,
        "cobertura_original": round(
            (total - len([d for d in diferencias if d.get("motivo") == "ausente"]))
            / total, 4),
        "compatible": not [d for d in diferencias
                           if d["clasificacion"] == "RIESGO SIN RESOLVER"],
    }


# ---------------------------------------------------------------------------
# Salida
# ---------------------------------------------------------------------------


def _firma(documento: dict) -> str:
    base = json.dumps(documento, sort_keys=True, ensure_ascii=False,
                      default=str).encode("utf-8")
    return hashlib.sha256(base).hexdigest()[:32]


def escribir_markdown(resultado: dict, destino: Path) -> None:
    lineas = [
        "# Matriz 5BR - esquema ORIGINAL vs reconstruccion vs laboratorio migrado",
        "",
        f"Generado UTC: {resultado['fecha_utc']}",
        "",
        "## 1. Objetos ORIGINALES ausentes de la reconstruccion 5BL",
        "",
    ]
    rec = resultado.get("reconstruccion")
    if rec:
        lineas += [
            f"- Tablas: {rec['origen']['tablas']} origen / "
            f"{rec['reconstruccion']['tablas']} reconstruidas",
            f"- Triggers: {rec['origen']['triggers']} origen / "
            f"{rec['reconstruccion']['triggers']} reconstruidos",
            f"- Rutinas: {rec['origen']['rutinas']} origen / "
            f"{rec['reconstruccion']['rutinas']} reconstruidas",
            f"- Vistas: {rec['origen']['vistas']} origen / "
            f"{rec['reconstruccion']['vistas']} reconstruidas",
            "",
            "### Tabla ausente",
            "",
        ]
        lineas += [f"- `public.{t}`" for t in rec["tablas_ausentes"]] or ["- ninguna"]
        lineas += ["", "### Triggers ausentes", ""]
        for nombre in rec["triggers_ausentes"]:
            detalle = rec["detalle_triggers_ausentes"][nombre]
            lineas.append(f"- `{nombre}` sobre `{detalle['tabla']}` -> "
                          f"`{detalle['funcion']}`")
        lineas += ["", "### Rutinas ausentes", ""]
        for nombre in rec["rutinas_ausentes"]:
            detalle = rec["detalle_rutinas_ausentes"][nombre]
            lineas.append(f"- `public.{nombre}({detalle['args']})` "
                          f"({detalle['tipo']}, search_path={detalle['search_path']})")
        lineas.append("")

    rest = resultado.get("restauracion")
    if rest:
        lineas += [
            "## 2. Restauracion del esquema original en PG17",
            "",
            f"- Diferencias object-level: {len(rest['discrepancias'])}",
            f"- Fidelidad de estructura: {rest['fidelidad_estructura']:.2%}",
            "",
        ]

    mig = resultado.get("migrado")
    if mig:
        lineas += [
            "## 3. Esquema original DESPUES del pipeline SaaS",
            "",
            f"- Objetos originales: {mig['objetos_originales']}",
            f"- Conservados sin cambio: {mig['conservados_sin_cambio']}",
            f"- Cobertura (presentes): {mig['cobertura_original']:.2%}",
            f"- Compatible: {'SI' if mig['compatible'] else 'NO'}",
            "",
            "| Objeto | Atributo | Origen | Migrado | Clasificacion |",
            "|---|---|---|---|---|",
        ]
        for diferencia in mig["diferencias"]:
            lineas.append(
                f"| `{diferencia['objeto']}` | {diferencia.get('atributo', 'ausente')} "
                f"| {diferencia.get('origen', '-')} | {diferencia.get('migrado', '-')} "
                f"| {diferencia['clasificacion']} |")
        lineas += ["", f"- Objetos nuevos del SaaS: {len(mig['objetos_nuevos_saas'])}", ""]

    lineas += ["", "Nota: informe sanitizado. No contiene DDL ni datos.", ""]
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(lineas), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="5BR: matriz de objetos original vs 5BL vs laboratorio migrado")
    parser.add_argument("--fase", required=True,
                        choices=("reconstruccion", "restaurado", "migrado"))
    parser.add_argument("--catalogo-lab", default=None,
                        help="catalogo JSON del laboratorio (fases restaurado/migrado)")
    parser.add_argument("--salida", default=None, help="informe JSON")
    parser.add_argument("--markdown", default=None, help="matriz Markdown")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    origen = cargar_origen()
    resultado: dict = {
        "etapa": "5BR / Etapa I",
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origen": {"archivo": RUTA_CATALOGO_ORIGEN.name,
                   "conteos": origen.get("conteos"), "meta": origen.get("meta")},
    }

    if args.fase == "reconstruccion":
        reconstruccion = objetos_reconstruccion()
        resultado["reconstruccion"] = comparar_reconstruccion(origen, reconstruccion)
        veredicto = ("RECONSTRUCCION IDENTICA AL ORIGEN"
                     if resultado["reconstruccion"]["coincide"]
                     else "RECONSTRUCCION PARCIAL: objetos originales ausentes")
    else:
        if not args.catalogo_lab:
            raise ComparacionError("--catalogo-lab es obligatorio en esta fase")
        lab = json.loads(Path(args.catalogo_lab).read_text(encoding="utf-8"))
        resultado["laboratorio"] = {"archivo": Path(args.catalogo_lab).name,
                                    "conteos": lab.get("conteos"),
                                    "meta": lab.get("meta")}
        if args.fase == "restaurado":
            resultado["restauracion"] = comparar_restauracion(origen, lab)
            veredicto = ("RESTAURACION FIEL AL ORIGEN"
                         if resultado["restauracion"]["identica"]
                         else "RESTAURACION CON DIFERENCIAS")
        else:
            resultado["migrado"] = comparar_migrado(origen, lab)
            veredicto = ("COMPATIBLE" if resultado["migrado"]["compatible"]
                         else "INCOMPATIBLE: riesgos sin resolver")

    resultado["veredicto"] = veredicto
    resultado["firma"] = _firma({k: v for k, v in resultado.items() if k != "firma"})

    texto = json.dumps(resultado, indent=2, ensure_ascii=False, default=str)
    if args.salida:
        ruta = Path(args.salida)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(texto + "\n", encoding="utf-8")
        print(f"Informe escrito: {ruta}")
    if args.markdown:
        escribir_markdown(resultado, Path(args.markdown))
        print(f"Matriz escrita: {args.markdown}")
    if args.json or not args.salida:
        print(texto)

    if args.fase == "reconstruccion":
        return 0 if resultado["reconstruccion"]["coincide"] else 1
    if args.fase == "restaurado":
        return 0 if resultado["restauracion"]["identica"] else 1
    return 0 if resultado["migrado"]["compatible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
