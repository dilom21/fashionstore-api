"""124 - 5C0: alta tenant de proveedores y compra completa en empresa NUEVA.

Bateria HTTP REAL contra FastAPI SaaS ON y PostgreSQL 17 local
(`127.0.0.1:55460`, datos 100% sinteticos). No toca Supabase, Render, Vercel ni
produccion. La contrasena de fixtures se lee del archivo local fuera de Git y
NUNCA se imprime.

Escenario:
  1. login A/B; la empresa B no tiene proveedores;
  2. B crea un proveedor real con un producto de SU empresa (201);
  3. A no ve ese proveedor y no puede reutilizar su NIT (409);
  4. un producto de A usado por B -> 403 (cross-tenant);
  5. B crea, envia y recibe una orden de compra real (201/200/201);
  6. A no ve la orden de B (404);
  7. RBAC: CAJERO/CLIENTE/sin token -> 403/403/401;
  8. gate: GET /tenant/proveedores y barra final -> 503.

Uso:
    python database/saas/124_proveedores_tenant_5c0.py [--base URL] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

EVIDENCIAS = Path(__file__).resolve().parent / "evidencias"
SECRETOS = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "opencode" / \
    "fs_staging_5bx" / "secrets_5bx.json"

PASS, FAIL, INFO = "PASS", "FAIL", "INFO"


class Matriz:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.cliente = httpx.Client(base_url=self.base, timeout=30.0)
        self.pruebas: list[dict] = []

    def pedir(self, metodo: str, ruta: str, *, token: str | None = None,
              cuerpo: dict | None = None):
        cabeceras = {"Authorization": f"Bearer {token}"} if token else {}
        return self.cliente.request(metodo, ruta, json=cuerpo,
                                    headers=cabeceras)

    def comprobar(self, nombre: str, resp, esperado) -> None:
        esperados = esperado if isinstance(esperado, (tuple, list)) else (esperado,)
        ok = resp.status_code in esperados
        texto = ""
        try:
            datos = resp.json()
            if isinstance(datos, dict):
                detalle = datos.get("detail") or ""
                texto = detalle if isinstance(detalle, str) else json.dumps(
                    detalle, ensure_ascii=False)[:160]
        except Exception:  # noqa: BLE001
            texto = ""
        self.pruebas.append({
            "nombre": nombre,
            "esperado": list(esperados),
            "obtenido": resp.status_code,
            "estado": PASS if ok else FAIL,
            "detalle": texto[:300],
        })

    def anotar(self, nombre: str, esperado, obtenido, ok: bool,
               detalle: str = "") -> None:
        self.pruebas.append({
            "nombre": nombre,
            "esperado": esperado,
            "obtenido": obtenido,
            "estado": PASS if ok else FAIL,
            "detalle": detalle[:300],
        })

    def info(self, nombre: str, detalle: str) -> None:
        self.pruebas.append({
            "nombre": nombre, "esperado": "no aplica", "obtenido": "-",
            "estado": INFO, "detalle": detalle[:300],
        })

    def resumen(self) -> dict:
        contar = lambda e: sum(1 for p in self.pruebas if p["estado"] == e)  # noqa: E731
        return {
            "total": len(self.pruebas),
            "pass": contar(PASS),
            "fail": contar(FAIL),
            "info": contar(INFO),
            "fallos": [p["nombre"] for p in self.pruebas if p["estado"] == FAIL],
        }


def cuerpo(resp):
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


def login(m: Matriz, correo: str, slug: str, password: str,
          contexto: str = "personal"):
    return m.pedir("POST", "/auth/tenant/login", cuerpo={
        "correo": correo, "password": password,
        "empresa_slug": slug, "contexto": contexto,
    })


def token_de(resp) -> str | None:
    if resp.status_code != 200:
        return None
    datos = cuerpo(resp) or {}
    return datos.get("access_token") if isinstance(datos, dict) else None


def ejecutar(base: str) -> dict:
    secretos = json.loads(SECRETOS.read_text(encoding="utf-8"))
    password = secretos["password_usuarios"]
    m = Matriz(base)
    sufijo = uuid.uuid4().hex[:10]
    nit = f"NIT-5C0-{sufijo}"

    # ---------------- 1. login A/B ----------------
    tk_a = token_de(login(m, "admin@staging.invalid", "empresa-a", password))
    r_b = login(m, "admin.b@staging.invalid", "empresa-b", password)
    tk_b = token_de(r_b)
    m.anotar("login_admin_A", "token", bool(tk_a), bool(tk_a))
    m.anotar("login_admin_B", "token", bool(tk_b), bool(tk_b))
    if not tk_a or not tk_b:
        m.info("abortado", "sin token de administrador A/B")
        return _cerrar(m, {})

    # ---------------- 2. catalogos reales de B ----------------
    productos_b = cuerpo(m.pedir("GET", "/tenant/catalogo/productos?limit=100",
                                 token=tk_b)) or []
    variantes_b = cuerpo(m.pedir("GET", "/tenant/catalogo/variantes?limit=200",
                                 token=tk_b)) or []
    temporadas_b = cuerpo(m.pedir("GET", "/tenant/catalogo/temporadas",
                                  token=tk_b)) or []
    sucursales_b = cuerpo(m.pedir("GET", "/tenant/empresa/sucursales",
                                  token=tk_b)) or []
    proveedores_b = cuerpo(m.pedir("GET", "/tenant/catalogo/proveedores",
                                   token=tk_b)) or []
    proveedores_a = cuerpo(m.pedir("GET", "/tenant/catalogo/proveedores",
                                   token=tk_a)) or []

    m.anotar("B_tiene_producto_propio", ">=1", len(productos_b),
             len(productos_b) >= 1)
    if not productos_b or not variantes_b or not temporadas_b:
        m.info("abortado", "B no tiene producto/variante/temporada para comprar")
        return _cerrar(m, {})
    producto_b = productos_b[0]
    variante_b = next(
        (v for v in variantes_b if v.get("producto_id") == producto_b["id"]),
        variantes_b[0],
    )
    temporada_b = temporadas_b[0]
    sucursal_b = next((s for s in sucursales_b if s.get("estado")), None)
    if sucursal_b is None:
        m.info("abortado", "B no tiene sucursal activa")
        return _cerrar(m, {})
    proveedores_b_antes = {p["id"] for p in proveedores_b}
    proveedores_a_antes = {p["id"] for p in proveedores_a}

    # ---------------- 3. alta real del proveedor en B ----------------
    alta = m.pedir("POST", "/tenant/proveedores", token=tk_b, cuerpo={
        "razon_social": f"Proveedor Sintetico 5C0 {sufijo}",
        "nit": nit,
        "correo": f"proveedor.{sufijo}@staging.invalid",
        "telefono": "70000000",
        "direccion": "Calle Sintetica 100",
        "producto_ids": [producto_b["id"]],
    })
    m.comprobar("B_alta_proveedor_201", alta, 201)
    proveedor = cuerpo(alta) or {}
    nuevo_id = proveedor.get("id")
    m.anotar("B_proveedor_id_real", "entero > 0", nuevo_id,
             isinstance(nuevo_id, int) and nuevo_id > 0)
    m.anotar("B_proveedor_total_productos", 1,
             proveedor.get("total_productos"),
             proveedor.get("total_productos") == 1)
    if not nuevo_id:
        return _cerrar(m, {"proveedor_id": nuevo_id})

    # ---------------- 4. aislamiento y conflictos ----------------
    lista_b = cuerpo(m.pedir("GET", "/tenant/catalogo/proveedores",
                             token=tk_b)) or []
    lista_a = cuerpo(m.pedir("GET", "/tenant/catalogo/proveedores",
                             token=tk_a)) or []
    m.anotar("B_ve_su_proveedor", "incluido", len(lista_b),
             any(p["id"] == nuevo_id for p in lista_b))
    m.anotar("A_no_ve_proveedor_de_B", "excluido", len(lista_a),
             all(p["id"] != nuevo_id for p in lista_a)
             and proveedores_a_antes == {p["id"] for p in lista_a}
             and proveedores_b_antes.issubset({p["id"] for p in lista_b}))

    m.comprobar(
        "B_nit_duplicado_409",
        m.pedir("POST", "/tenant/proveedores", token=tk_b, cuerpo={
            "razon_social": "Duplicado 5C0", "nit": nit}),
        409,
    )
    m.comprobar(
        "B_producto_de_A_403",
        m.pedir("POST", "/tenant/proveedores", token=tk_b, cuerpo={
            "razon_social": "Cruzado 5C0",
            "nit": f"NIT-X-{sufijo}", "producto_ids": [1]}),
        (403, 422),
    )
    reemplazo = m.pedir(
        "PUT", f"/tenant/proveedores/{nuevo_id}/productos", token=tk_b,
        cuerpo={"producto_ids": [producto_b["id"], producto_b["id"]]},
    )
    m.comprobar("B_reemplazo_productos_200", reemplazo, 200)
    m.anotar("B_reemplazo_total", 1,
             (cuerpo(reemplazo) or {}).get("total_productos"),
             (cuerpo(reemplazo) or {}).get("total_productos") == 1)

    # ---------------- 5. compra completa en B ----------------
    orden = m.pedir("POST", "/tenant/ordenes-compra", token=tk_b, cuerpo={
        "proveedor_id": nuevo_id,
        "sucursal_id": sucursal_b["id"],
        "lineas": [{
            "variante_producto_id": variante_b["id"],
            "temporada_id": temporada_b["id"],
            "cantidad": 2,
            "costo_unitario": "10.50",
        }],
    })
    m.comprobar("B_orden_compra_201", orden, 201)
    datos_orden = cuerpo(orden) or {}
    orden_id = datos_orden.get("orden_compra_id")
    m.anotar("B_orden_estado_BORRADOR", "BORRADOR", datos_orden.get("estado"),
             datos_orden.get("estado") == "BORRADOR")
    if not orden_id:
        return _cerrar(m, {"proveedor_id": nuevo_id, "orden_id": None})

    m.comprobar("B_enviar_orden_200",
                m.pedir("POST", f"/tenant/ordenes-compra/{orden_id}/enviar",
                        token=tk_b), 200)
    detalle = cuerpo(m.pedir("GET", f"/tenant/ordenes-compra/{orden_id}",
                             token=tk_b)) or {}
    lineas = detalle.get("lineas") or []
    m.anotar("B_orden_detalle_lineas", 1, len(lineas), len(lineas) == 1)
    m.comprobar("A_no_ve_orden_de_B_404",
                m.pedir("GET", f"/tenant/ordenes-compra/{orden_id}",
                        token=tk_a), 404)
    if lineas:
        recibo = m.pedir(
            "POST", f"/tenant/ordenes-compra/{orden_id}/recepciones",
            token=tk_b, cuerpo={
                "operacion_id": str(uuid.uuid4()),
                "lineas": [{
                    "detalle_orden_compra_id": lineas[0]["id"],
                    "cantidad": 2,
                }],
            })
        m.comprobar("B_recepcion_compra_201", recibo, 201)
        datos_recibo = cuerpo(recibo) or {}
        m.anotar("B_orden_estado_RECIBIDA", "RECIBIDA",
                 datos_recibo.get("estado_orden"),
                 datos_recibo.get("estado_orden") == "RECIBIDA")
        stock = (datos_recibo.get("stock") or [{}])[0]
        m.anotar("B_stock_actual_entero", "int >= 0",
                 stock.get("stock_actual"),
                 isinstance(stock.get("stock_actual"), int)
                 and stock.get("stock_actual") >= 0)
    else:
        m.info("B_recepcion_compra", "la orden no expone lineas para recibir")

    # ---------------- 6. RBAC y gate ----------------
    r_caj = login(m, "cajero.b@staging.invalid", "empresa-b", password)
    tk_caj = token_de(r_caj)
    m.anotar("login_cajero_B", "token", bool(tk_caj), bool(tk_caj))
    if tk_caj:
        m.comprobar(
            "cajero_B_403_alta_proveedor",
            m.pedir("POST", "/tenant/proveedores", token=tk_caj, cuerpo={
                "razon_social": "No debe 5C0",
                "nit": f"NIT-C-{sufijo}"}),
            403,
        )
    m.comprobar(
        "cliente_A_403_alta_proveedor",
        m.pedir("POST", "/tenant/proveedores",
                token=token_de(login(m, "cliente@staging.invalid",
                                     "empresa-a", password, "cliente")),
                cuerpo={"razon_social": "No debe 5C0"}),
        (401, 403),
    )
    m.comprobar("sin_token_401_alta_proveedor",
                m.pedir("POST", "/tenant/proveedores",
                        cuerpo={"razon_social": "No debe 5C0"}),
                (401, 403))
    m.comprobar("gate_GET_proveedores_503",
                m.pedir("GET", "/tenant/proveedores", token=tk_b), 503)
    m.comprobar("gate_POST_barra_final_503",
                m.pedir("POST", "/tenant/proveedores/", token=tk_b,
                        cuerpo={"razon_social": "No debe 5C0"}), 503)
    m.comprobar("gate_PUT_productos_barra_final_503",
                m.pedir("PUT", f"/tenant/proveedores/{nuevo_id}/productos/",
                        token=tk_b, cuerpo={"producto_ids": []}), 503)

    return _cerrar(m, {
        "proveedor_id": nuevo_id,
        "orden_id": orden_id,
        "producto_id": producto_b["id"],
        "variante_producto_id": variante_b["id"],
        "temporada_id": temporada_b["id"],
        "sucursal_id": sucursal_b["id"],
    })


def _cerrar(m: Matriz, contexto: dict) -> dict:
    resumen = m.resumen()
    datos = {
        "etapa": "5C0",
        "tarea": "alta tenant de proveedores + compra en empresa nueva",
        "sello_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base": m.base,
        "datos_reales": False,
        "secretos_incluidos": False,
        "contexto": contexto,
        "resumen": resumen,
        "pruebas": m.pruebas,
    }
    EVIDENCIAS.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = EVIDENCIAS / f"EVIDENCIA_PROVEEDORES_5C0_{sello}.json"
    destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    datos["_destino"] = str(destino)
    return datos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    datos = ejecutar(args.base)
    resumen = datos["resumen"]
    if args.json:
        print(json.dumps(datos, ensure_ascii=False, indent=2))
    else:
        for p in datos["pruebas"]:
            print(f"[{p['estado']:4}] {p['nombre']:<40} "
                  f"esperado={p['esperado']} obtenido={p['obtenido']} "
                  f"{p['detalle']}")
        print()
        print(f"RESUMEN: {resumen['pass']} PASS / {resumen['fail']} FAIL / "
              f"{resumen['info']} INFO de {resumen['total']}")
        print(f"Evidencia: {datos['_destino']}")
    return 0 if resumen["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
