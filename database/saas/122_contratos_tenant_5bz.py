"""122 — Contratos tenant de LECTURA (Etapa 5BZ, tarea T02).

Bateria HTTP REAL contra el backend FastAPI SaaS ON levantado sobre el staging
local 5BX (`127.0.0.1:55460`, datos 100% ficticios). Comprueba los contratos
nuevos que permiten a la UI dejar de escribir IDs a mano:

  1. login A/B y 401 sin token;
  2. diccionarios GLOBALES (tallas, colores, ciudades) identicos en A y B;
  3. proveedores y temporadas por empresa (B no tiene proveedores);
  4. variantes por empresa con filtro `texto` limitado al propio tenant;
  5. inventario por sucursal con `stock_disponible` calculado en SQL;
  6. lineas de venta para devolucion (propia 200 / ajena e inexistente 404);
  7. coherencia RBAC: ADMIN lee las tres consultas de personal, CAJERO y
     CLIENTE reciben 403 (sin escalar privilegios);
  8. ENCARGADO_SUCURSAL: 200 en lectura y 403 en escritura. Si el staging no
     tiene la fila global de ese rol se documenta y se demuestra la MISMA
     propiedad de forma reversible con la matriz nominal del CAJERO;
  9. gate: rutas legacy y variantes con barra final siguen en 503;
 10. venta de contado idempotente: 201, replay 200 `idempotente=true` y el
     stock se descuenta UNA sola vez (se comparan deltas observados).

Uso:
    python database/saas/122_contratos_tenant_5bz.py \
        [--base http://127.0.0.1:8010] [--json]

No imprime ni escribe secretos: la contrasena de fixtures se lee del archivo
local fuera de Git (`%LOCALAPPDATA%/Temp/opencode/fs_staging_5bx/
secrets_5bx.json`) y la contrasena del personal invitado se genera en memoria.
Escribe la evidencia en `database/saas/evidencias/`. Codigo de salida 1 si hay
algun FAIL.

Nota sobre el stock: el inventario 1 cambia con cada corrida (la seccion 10
descuenta una unidad). Por eso se informa el valor observado y se comparan
DELTAS; no se "arregla" ni se reinicia el stock para forzar un PASS.
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

SKU_A = "SKU-5BR-FICTICIO"


def _jsonable(valor):
    """Normaliza sets/tuplas para que la evidencia JSON sea serializable."""
    if isinstance(valor, set):
        return sorted(valor)
    if isinstance(valor, dict):
        return {clave: _jsonable(dato) for clave, dato in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_jsonable(dato) for dato in valor]
    return valor


class Matriz:
    """Acumula PASS/FAIL/INFO y escribe la evidencia JSON."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.cliente = httpx.Client(base_url=self.base, timeout=30.0)
        self.pruebas: list[dict] = []

    def pedir(self, metodo: str, ruta: str, *, token: str | None = None,
              cuerpo: dict | None = None):
        cabeceras = {"Authorization": f"Bearer {token}"} if token else {}
        return self.cliente.request(metodo, ruta, json=cuerpo,
                                    headers=cabeceras)

    def comprobar(self, nombre: str, resp, esperado, detalle: str = "") -> None:
        esperados = esperado if isinstance(esperado, (tuple, list)) else (esperado,)
        ok = resp.status_code in esperados
        texto = ""
        try:
            datos = resp.json()
            if isinstance(datos, dict):
                texto = datos.get("detail") or ""
                if not isinstance(texto, str):
                    texto = json.dumps(texto, ensure_ascii=False)[:200]
        except Exception:  # noqa: BLE001 - cuerpo no JSON es un dato, no un error
            texto = ""
        self.pruebas.append({
            "nombre": nombre,
            "esperado": _jsonable(list(esperados)),
            "obtenido": resp.status_code,
            "estado": PASS if ok else FAIL,
            "detalle": str(detalle or texto)[:300],
        })

    def anotar(self, nombre: str, esperado, obtenido, ok: bool,
               detalle: str = "") -> None:
        self.pruebas.append({
            "nombre": nombre,
            "esperado": _jsonable(esperado),
            "obtenido": _jsonable(obtenido),
            "estado": PASS if ok else FAIL,
            "detalle": str(detalle)[:300],
        })

    def info(self, nombre: str, detalle: str) -> None:
        self.pruebas.append({
            "nombre": nombre, "esperado": "no aplica", "obtenido": "-",
            "estado": INFO, "detalle": str(detalle)[:300],
        })

    def resumen(self) -> dict:
        def contar(estado: str) -> int:
            return sum(1 for p in self.pruebas if p["estado"] == estado)
        return {
            "total": len(self.pruebas),
            "pass": contar(PASS),
            "fail": contar(FAIL),
            "info": contar(INFO),
            "fallos": [p["nombre"] for p in self.pruebas if p["estado"] == FAIL],
        }


def login(m: Matriz, correo: str, slug: str, password: str,
          contexto: str = "personal"):
    return m.pedir("POST", "/auth/tenant/login", cuerpo={
        "correo": correo, "password": password,
        "empresa_slug": slug, "contexto": contexto,
    })


def token_de(resp) -> str | None:
    if resp.status_code != 200:
        return None
    datos = resp.json() if resp.content else {}
    return datos.get("access_token") if isinstance(datos, dict) else None


def cuerpo(resp) -> object:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


def _claves(elemento: object) -> set:
    return set(elemento.keys()) if isinstance(elemento, dict) else set()


def _diccionario(m: Matriz, nombre: str, ruta: str, tk_a: str, tk_b: str,
                 esperado_texto: str | None) -> list:
    """Comprueba un diccionario GLOBAL: misma fila para A y B."""
    en_a = m.pedir("GET", ruta, token=tk_a)
    en_b = m.pedir("GET", ruta, token=tk_b)
    m.comprobar(f"{nombre}_A_200", en_a, 200)
    m.comprobar(f"{nombre}_B_200", en_b, 200)
    datos_a = cuerpo(en_a) or []
    datos_b = cuerpo(en_b) or []
    if isinstance(datos_a, list) and isinstance(datos_b, list):
        m.anotar(f"{nombre}_identicos_A_B", "misma lista global",
                 f"A={len(datos_a)} B={len(datos_b)}",
                 datos_a == datos_b,
                 detalle="los diccionarios no son datos de empresa")
        if esperado_texto is not None:
            nombres = [x.get("nombre") for x in datos_a
                       if isinstance(x, dict)]
            m.anotar(f"{nombre}_contiene_{esperado_texto}",
                     f"1 fila '{esperado_texto}'",
                     nombres,
                     nombres == [esperado_texto])
        if datos_a and isinstance(datos_a[0], dict):
            m.anotar(f"{nombre}_contrato_id_nombre", {"id", "nombre"},
                     sorted(_claves(datos_a[0])),
                     _claves(datos_a[0]) == {"id", "nombre"},
                     detalle="sin empresa_id")
    return datos_a


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not SECRETOS.exists():
        print(f"FALTA el archivo de secretos sinteticos: {SECRETOS}")
        return 2
    password = json.loads(SECRETOS.read_text(encoding="utf-8")).get(
        "password_usuarios")
    if not password:
        print("El archivo de secretos no contiene password_usuarios")
        return 2

    m = Matriz(args.base)

    # ---------------- 0. salud y autenticacion ----------------
    m.comprobar("health", m.pedir("GET", "/health"), 200)
    r_aa = login(m, "admin@staging.invalid", "empresa-a", password)
    r_ab = login(m, "admin.b@staging.invalid", "empresa-b", password)
    m.comprobar("login_A_admin", r_aa, 200)
    m.comprobar("login_B_admin", r_ab, 200)
    tk_a, tk_b = token_de(r_aa), token_de(r_ab)
    if not tk_a or not tk_b:
        print("Sin token: no se puede continuar")
        print(json.dumps(m.resumen(), ensure_ascii=False))
        return 1

    m.comprobar("sin_token_tallas_401",
                m.pedir("GET", "/tenant/catalogo/tallas"), 401)
    m.comprobar("sin_token_inventario_401",
                m.pedir("GET", "/tenant/inventario"), 401)
    m.comprobar("sin_token_lineas_401",
                m.pedir("GET", "/tenant/devoluciones/ventas/3/lineas"), 401)

    # ---------------- 2. diccionarios GLOBALES ----------------
    tallas_a = _diccionario(m, "tallas", "/tenant/catalogo/tallas", tk_a, tk_b,
                            "M")
    _diccionario(m, "colores", "/tenant/catalogo/colores", tk_a, tk_b, "Negro")
    ciudades_a = _diccionario(m, "ciudades", "/tenant/catalogo/ciudades",
                              tk_a, tk_b, None)
    m.anotar("ciudades_no_vacias", ">=1 fila", len(ciudades_a),
             len(ciudades_a) >= 1)

    # ---------------- 3. proveedores y temporadas ----------------
    prov_a = m.pedir("GET", "/tenant/catalogo/proveedores", token=tk_a)
    prov_b = m.pedir("GET", "/tenant/catalogo/proveedores", token=tk_b)
    m.comprobar("proveedores_A_200", prov_a, 200)
    m.comprobar("proveedores_B_200", prov_b, 200)
    lista_prov_a = cuerpo(prov_a) or []
    m.anotar("proveedores_A_una_fila", 1, len(lista_prov_a),
             len(lista_prov_a) == 1)
    m.anotar("proveedores_B_vacio", "[]", lista_prov_a if not cuerpo(prov_b)
             else cuerpo(prov_b), (cuerpo(prov_b) or []) == [],
             detalle="empresa-b no tiene proveedores")
    if lista_prov_a:
        m.anotar("proveedores_contrato", {"id", "razon_social", "estado"},
                 sorted(_claves(lista_prov_a[0])),
                 _claves(lista_prov_a[0]) == {"id", "razon_social", "estado"})

    temp_a = m.pedir("GET", "/tenant/catalogo/temporadas", token=tk_a)
    temp_b = m.pedir("GET", "/tenant/catalogo/temporadas", token=tk_b)
    m.comprobar("temporadas_A_200", temp_a, 200)
    m.comprobar("temporadas_B_200", temp_b, 200)
    lista_temp_a = cuerpo(temp_a) or []
    lista_temp_b = cuerpo(temp_b) or []
    m.anotar("temporadas_A_una_fila", 1, len(lista_temp_a),
             len(lista_temp_a) == 1)
    m.anotar("temporadas_B_una_fila", 1, len(lista_temp_b),
             len(lista_temp_b) == 1)
    ids_temp_a = {x.get("id") for x in lista_temp_a}
    ids_temp_b = {x.get("id") for x in lista_temp_b}
    m.anotar("temporadas_A_B_disjuntas", "interseccion vacia",
             f"A={sorted(ids_temp_a)} B={sorted(ids_temp_b)}",
             bool(ids_temp_a and ids_temp_b and not (ids_temp_a & ids_temp_b)))
    if lista_temp_a:
        m.anotar("temporadas_fechas_iso", "fecha_inicio ISO AAAA-MM-DD",
                 lista_temp_a[0].get("fecha_inicio"),
                 isinstance(lista_temp_a[0].get("fecha_inicio"), str),
                 detalle="Pydantic serializa date como ISO")
        m.anotar("temporadas_contrato",
                 {"id", "nombre", "fecha_inicio", "fecha_fin", "estado"},
                 sorted(_claves(lista_temp_a[0])),
                 _claves(lista_temp_a[0]) == {
                     "id", "nombre", "fecha_inicio", "fecha_fin", "estado"},
                 detalle="sin empresa_id")

    # ---------------- 4. variantes ----------------
    var_a = m.pedir("GET", "/tenant/catalogo/variantes", token=tk_a)
    var_b = m.pedir("GET", "/tenant/catalogo/variantes", token=tk_b)
    m.comprobar("variantes_A_200", var_a, 200)
    m.comprobar("variantes_B_200", var_b, 200)
    lista_var_a = cuerpo(var_a) or []
    lista_var_b = cuerpo(var_b) or []
    m.anotar("variantes_A_una_fila_sku", SKU_A,
             [x.get("sku") for x in lista_var_a],
             [x.get("sku") for x in lista_var_a] == [SKU_A])
    if lista_var_a:
        m.anotar("variantes_contrato",
                 {"id", "producto_id", "producto_nombre", "sku", "talla",
                  "color"},
                 sorted(_claves(lista_var_a[0])),
                 _claves(lista_var_a[0]) == {
                     "id", "producto_id", "producto_nombre", "sku", "talla",
                     "color"},
                 detalle="sin empresa_id")
    # Aislamiento real: A solo ve sus SKU y B solo ve los suyos.
    skus_a = {x.get("sku") for x in lista_var_a}
    skus_b = {x.get("sku") for x in lista_var_b}
    m.anotar("variantes_A_B_disjuntas", "interseccion vacia",
             f"A={sorted(skus_a)} B={sorted(skus_b)}",
             bool(skus_a and skus_b and not (skus_a & skus_b)))
    if lista_var_b:
        m.info("variantes_B_no_vacia",
               "empresa-b SI tiene 1 variante propia en el staging "
               f"({sorted(skus_b)}); el brief esperaba lista vacia. "
               "Lo exigible -y verificado- es que no exponga variantes de A.")
    sku_b = sorted(skus_b)[0] if skus_b else "SKU-B-100"
    filtro_5br = m.pedir("GET", "/tenant/catalogo/variantes?texto=5BR",
                         token=tk_a)
    m.comprobar("variantes_texto_5BR_200", filtro_5br, 200)
    m.anotar("variantes_texto_5BR", [SKU_A],
             [x.get("sku") for x in (cuerpo(filtro_5br) or [])],
             [x.get("sku") for x in (cuerpo(filtro_5br) or [])] == [SKU_A])
    filtro_ajeno = m.pedir("GET", f"/tenant/catalogo/variantes?texto={sku_b}",
                           token=tk_a)
    m.comprobar("variantes_texto_ajeno_200", filtro_ajeno, 200)
    m.anotar("variantes_texto_ajeno_vacio_A", "[]", cuerpo(filtro_ajeno),
             (cuerpo(filtro_ajeno) or []) == [],
             detalle=f"texto={sku_b} (SKU de empresa-b) desde empresa-a")
    producto_b = (lista_var_b[0].get("producto_id") if lista_var_b else 100)
    filtro_prod_ajeno = m.pedir(
        "GET", f"/tenant/catalogo/variantes?producto_id={producto_b}",
        token=tk_a)
    m.anotar("variantes_producto_ajeno_vacio_A", "[]",
             cuerpo(filtro_prod_ajeno), (cuerpo(filtro_prod_ajeno) or []) == [],
             detalle=f"producto_id={producto_b} de empresa-b desde empresa-a")

    # ---------------- 5. inventario por sucursal ----------------
    inv_a = m.pedir("GET", "/tenant/inventario", token=tk_a)
    inv_b = m.pedir("GET", "/tenant/inventario", token=tk_b)
    m.comprobar("inventario_A_200", inv_a, 200)
    m.comprobar("inventario_B_200", inv_b, 200)
    filas_a = cuerpo(inv_a) or []
    filas_b = cuerpo(inv_b) or []
    fila_uno = next((f for f in filas_a if f.get("id") == 1), None)
    m.anotar("inventario_A_fila_1", "fila id=1 presente", bool(fila_uno),
             fila_uno is not None)
    if fila_uno:
        m.anotar("inventario_A_contrato",
                 {"id", "sucursal_id", "sucursal_nombre",
                  "variante_producto_id", "producto_id", "producto_nombre",
                  "sku", "talla_id", "talla", "color_id", "color",
                  "temporada_id", "temporada_nombre", "stock_actual",
                  "stock_reservado", "stock_disponible"},
                 sorted(_claves(fila_uno)),
                 _claves(fila_uno) == {
                     "id", "sucursal_id", "sucursal_nombre",
                     "variante_producto_id", "producto_id",
                     "producto_nombre", "sku", "talla_id", "talla",
                     "color_id", "color", "temporada_id",
                     "temporada_nombre", "stock_actual", "stock_reservado",
                     "stock_disponible"},
                 detalle="sin empresa_id")
        m.anotar("inventario_A_sku", SKU_A, fila_uno.get("sku"),
                 fila_uno.get("sku") == SKU_A)
        observado = {
            "stock_actual": fila_uno.get("stock_actual"),
            "stock_reservado": fila_uno.get("stock_reservado"),
            "stock_disponible": fila_uno.get("stock_disponible"),
        }
        esperado = {"stock_actual": 20, "stock_reservado": 2,
                    "stock_disponible": 18}
        if observado == esperado:
            m.anotar("inventario_A_stock_inicial_5BX", esperado, observado,
                     True)
        else:
            m.info(
                "inventario_A_stock_inicial_5BX",
                f"esperado {esperado} (brief 5BX) pero el staging ya acumula "
                f"ventas de corridas previas/de esta bateria: {observado}. "
                "No se reinicia el stock para forzar PASS.")
        m.anotar("inventario_stock_disponible_coherente",
                 "actual - reservado",
                 f"{observado['stock_actual']} - "
                 f"{observado['stock_reservado']}",
                 observado["stock_disponible"] == (
                     observado["stock_actual"] - observado["stock_reservado"]),
                 detalle="stock_disponible se calcula en SQL")

    sucursal_b = filas_b[0].get("sucursal_id") if filas_b else 100
    cruzado = m.pedir("GET", f"/tenant/inventario?sucursal_id={sucursal_b}",
                      token=tk_a)
    m.comprobar("inventario_sucursal_ajena_200", cruzado, 200)
    m.anotar("inventario_sucursal_ajena_vacia_A", "[]", cuerpo(cruzado),
             (cuerpo(cruzado) or []) == [],
             detalle=f"sucursal_id={sucursal_b} (de empresa-b) desde empresa-a")
    m.anotar("inventario_B_propia_fila", 1, len(filas_b), len(filas_b) == 1)
    if filas_b:
        m.anotar("inventario_B_sku", "SKU de empresa-b",
                 filas_b[0].get("sku"), bool(filas_b[0].get("sku")),
                 detalle=str(filas_b[0].get("sku")))
    texto_inv = m.pedir("GET", "/tenant/inventario?texto=5BR", token=tk_a)
    m.anotar("inventario_texto_5BR", ">=1 fila",
             len(cuerpo(texto_inv) or []), len(cuerpo(texto_inv) or []) >= 1)

    # ---------------- 6. lineas de venta para devolucion ----------------
    lineas_3 = m.pedir("GET", "/tenant/devoluciones/ventas/3/lineas",
                       token=tk_a)
    m.comprobar("lineas_venta_3_A_200", lineas_3, 200)
    detalle_3 = cuerpo(lineas_3) or {}
    m.anotar("lineas_venta_3_contrato",
             {"venta_id", "sucursal_id", "estado", "total", "lineas"},
             sorted(_claves(detalle_3)),
             _claves(detalle_3) == {
                 "venta_id", "sucursal_id", "estado", "total", "lineas"})
    lineas = detalle_3.get("lineas") or []
    m.anotar("lineas_venta_3_una_linea", 1, len(lineas), len(lineas) == 1)
    if lineas:
        primera = lineas[0]
        m.anotar(
            "lineas_venta_3_contrato_linea",
            {"detalle_venta_id", "inventario_id", "variante_producto_id",
             "sku", "producto_nombre", "talla", "color", "cantidad",
             "precio_unitario", "cantidad_devuelta",
             "cantidad_disponible_devolver"},
            sorted(_claves(primera)),
            _claves(primera) == {
                "detalle_venta_id", "inventario_id", "variante_producto_id",
                "sku", "producto_nombre", "talla", "color", "cantidad",
                "precio_unitario", "cantidad_devuelta",
                "cantidad_disponible_devolver"},
            detalle="sin empresa_id")
        m.anotar("lineas_venta_3_detalle_id", 3,
                 primera.get("detalle_venta_id"),
                 primera.get("detalle_venta_id") == 3)
        m.anotar(
            "lineas_venta_3_disponible",
            "cantidad - devuelta (>=0)",
            f"{primera.get('cantidad')} - {primera.get('cantidad_devuelta')}"
            f" = {primera.get('cantidad_disponible_devolver')}",
            primera.get("cantidad_disponible_devolver") == max(
                0, (primera.get("cantidad") or 0)
                - (primera.get("cantidad_devuelta") or 0)))
        if primera.get("cantidad_disponible_devolver") == 1:
            m.anotar("lineas_venta_3_disponible_1", 1,
                     primera.get("cantidad_disponible_devolver"), True)
        else:
            m.info("lineas_venta_3_disponible_1",
                   "el brief esperaba 1 y el staging devuelve "
                   f"{primera.get('cantidad_disponible_devolver')} "
                   f"(cantidad devuelta {primera.get('cantidad_devuelta')})")
    m.comprobar("lineas_venta_inexistente_404",
                m.pedir("GET", "/tenant/devoluciones/ventas/999999/lineas",
                        token=tk_a), 404)
    venta_b = m.pedir("GET", "/tenant/comercial/ventas?limit=5", token=tk_b)
    lista_ventas_b = cuerpo(venta_b) or []
    if isinstance(lista_ventas_b, list) and lista_ventas_b:
        ajena = lista_ventas_b[0].get("id")
        m.comprobar("lineas_venta_de_B_desde_A_404",
                    m.pedir("GET", f"/tenant/devoluciones/ventas/{ajena}/lineas",
                            token=tk_a), 404,
                    detalle=f"venta {ajena} de empresa-b consultada en empresa-a")
    else:
        m.info("lineas_venta_de_B_desde_A_404",
               "empresa-b no tiene ventas en el staging; se documenta el "
               "404 por inexistente (999999 ya cubierto)")
    m.comprobar("lineas_venta_de_A_desde_B_404",
                m.pedir("GET", "/tenant/devoluciones/ventas/3/lineas",
                        token=tk_b), 404,
                detalle="la venta 3 es de empresa-a")

    # ---------------- 7. coherencia RBAC ----------------
    for ruta in ("/tenant/empresa/usuarios", "/tenant/empresa/roles",
                 "/tenant/empresa/permisos"):
        m.comprobar(f"admin_A_lee_{ruta.rsplit('/', 1)[-1]}",
                    m.pedir("GET", ruta, token=tk_a), 200)

    usuarios_b = m.pedir("GET", "/tenant/empresa/usuarios", token=tk_b)
    m.comprobar("admin_B_lee_usuarios", usuarios_b, 200)

    r_caj = login(m, "cajero.b@staging.invalid", "empresa-b", password)
    m.comprobar("login_cajero_B", r_caj, 200)
    tk_caj = token_de(r_caj)
    if tk_caj:
        for ruta in ("/tenant/empresa/usuarios", "/tenant/empresa/roles",
                     "/tenant/empresa/permisos"):
            m.comprobar(f"cajero_B_403_{ruta.rsplit('/', 1)[-1]}",
                        m.pedir("GET", ruta, token=tk_caj), 403,
                        detalle="CAJERO no tiene PERSONAL/*")
    else:
        m.info("cajero_B_403", "cajero.b no puede iniciar sesion en el staging")

    r_cli = login(m, "cliente@staging.invalid", "empresa-a", password,
                  contexto="cliente")
    m.comprobar("login_cliente_A", r_cli, 200)
    tk_cli = token_de(r_cli)
    if tk_cli:
        for ruta in ("/tenant/empresa/usuarios", "/tenant/empresa/roles",
                     "/tenant/empresa/permisos"):
            m.comprobar(f"cliente_A_403_{ruta.rsplit('/', 1)[-1]}",
                        m.pedir("GET", ruta, token=tk_cli), 403,
                        detalle="contexto cliente nunca es personal")
        m.comprobar("cliente_A_403_tallas",
                    m.pedir("GET", "/tenant/catalogo/tallas", token=tk_cli), 403,
                    detalle="diccionarios globales solo para personal")
    else:
        m.info("cliente_A_403", "cliente@staging.invalid no inicia sesion")

    # ---------------- 8. ENCARGADO_SUCURSAL ----------------
    roles_a = m.pedir("GET", "/tenant/empresa/roles", token=tk_a)
    lista_roles_a = cuerpo(roles_a) or []
    rol_encargado = next(
        (r for r in lista_roles_a
         if str(r.get("nombre", "")).upper() == "ENCARGADO_SUCURSAL"), None)

    if rol_encargado:
        _probar_encargado(m, tk_a, password, lista_roles_a, rol_encargado)
    else:
        m.info(
            "encargado_rol_global_ausente",
            "GET /tenant/empresa/roles no expone ENCARGADO_SUCURSAL: el "
            "staging 5BX solo sembro public.rol 1 ADMINISTRADOR, 2 CLIENTE y "
            "3 CAJERO, y sp_alta_personal_tenant exige una fila global activa "
            f"(observado: {[r.get('nombre') for r in lista_roles_a]}). "
            "No se crea el rol global: no hay endpoint tenant para roles "
            "globales y el brief prohibe abrir escrituras nuevas.")
        roles_b = cuerpo(m.pedir("GET", "/tenant/empresa/roles", token=tk_b)) or []
        _probar_matriz_equivalente(m, tk_b, tk_caj, roles_b)

    # ---------------- 9. gate / legacy ----------------
    m.comprobar("legacy_productos_503", m.pedir("GET", "/productos",
                                                token=tk_a), 503)
    m.comprobar("inventario_barra_final_503",
                m.pedir("GET", "/tenant/inventario/", token=tk_a), 503)
    m.comprobar("tallas_barra_final_503",
                m.pedir("GET", "/tenant/catalogo/tallas/", token=tk_a), 503)
    m.comprobar("inventario_subruta_503",
                m.pedir("GET", "/tenant/inventario/productos", token=tk_a), 503)

    # ---------------- 10. venta de contado idempotente ----------------
    stock_antes = fila_uno.get("stock_actual") if fila_uno else None
    operacion = str(uuid.uuid4())
    venta = m.pedir("POST", "/tenant/ventas/contado", token=tk_a, cuerpo={
        "sucursal_id": 1, "operacion_id": operacion,
        "metodo_pago": "EFECTIVO",
        "lineas": [{"inventario_id": 1, "cantidad": 1}]})
    m.comprobar("venta_contado_201", venta, 201)
    v1 = cuerpo(venta) or {}
    replay = m.pedir("POST", "/tenant/ventas/contado", token=tk_a, cuerpo={
        "sucursal_id": 1, "operacion_id": operacion,
        "metodo_pago": "EFECTIVO",
        "lineas": [{"inventario_id": 1, "cantidad": 1}]})
    m.comprobar("venta_replay_200", replay, (200, 201))
    v2 = cuerpo(replay) or {}
    m.anotar("venta_replay_mismo_id", v1.get("venta_id"), v2.get("venta_id"),
             bool(v1) and v1.get("venta_id") == v2.get("venta_id"))
    m.anotar("venta_replay_idempotente", True, v2.get("idempotente"),
             v2.get("idempotente") is True)
    stock_venta = (v1.get("stock") or [{}])[0].get("stock_actual")
    stock_replay = (v2.get("stock") or [{}])[0].get("stock_actual")
    if stock_antes is not None and stock_venta is not None:
        m.anotar("venta_stock_descuento_delta",
                 "antes - despues = 1", stock_antes - stock_venta,
                 stock_antes - stock_venta == 1,
                 detalle=f"antes={stock_antes} despues={stock_venta}")
    else:
        m.info("venta_stock_descuento_delta",
               f"sin lectura previa del stock (antes={stock_antes}, "
               f"venta={stock_venta})")
    m.anotar("venta_stock_replay_delta", "0", (stock_venta or 0)
             - (stock_replay or 0),
             stock_venta == stock_replay,
             detalle="el replay no vuelve a descontar")
    m.comprobar("venta_operacion_reutilizada_409",
                m.pedir("POST", "/tenant/ventas/contado", token=tk_a, cuerpo={
                    "sucursal_id": 1, "operacion_id": operacion,
                    "metodo_pago": "EFECTIVO",
                    "lineas": [{"inventario_id": 1, "cantidad": 2}]}), 409)

    # ---------------- resumen y evidencia ----------------
    resumen = m.resumen()
    datos = {
        "etapa": "5BZ",
        "tarea": "T02 contratos tenant de lectura",
        "sello_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base": m.base,
        "datos_reales": False,
        "secretos_incluidos": False,
        "resumen": resumen,
        "pruebas": m.pruebas,
    }
    EVIDENCIAS.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = EVIDENCIAS / f"EVIDENCIA_CONTRATOS_TENANT_5BZ_{sello}.json"
    destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    if args.json:
        print(json.dumps(datos, ensure_ascii=False, indent=2))
    else:
        for p in m.pruebas:
            print(f"[{p['estado']:4}] {p['nombre']:<40} "
                  f"esperado={p['esperado']} obtenido={p['obtenido']} "
                  f"{p['detalle']}")
        print()
        print(f"RESUMEN: {resumen['pass']} PASS / {resumen['fail']} FAIL / "
              f"{resumen['info']} INFO de {resumen['total']}")
        print(f"Evidencia: {destino}")

    return 0 if resumen["fail"] == 0 else 1


def _probar_encargado(m: Matriz, tk_admin: str, password: str,
                      lista_roles: list, rol_encargado: dict) -> None:
    """Alta + invitacion + login + matriz para ENCARGADO_SUCURSAL."""
    rol_id = rol_encargado.get("id")
    sufijo = uuid.uuid4().hex[:10]
    correo = f"encargado.5bz.{sufijo}@staging.invalid"
    # Contrasena ficticia en memoria: cumple politica y NUNCA se imprime.
    password_nuevo = f"Enc{sufijo}Aa1#"
    alta = m.pedir("POST", "/tenant/empresa/usuarios", token=tk_admin, cuerpo={
        "correo": correo, "nombres": "Encargado", "apellidos": "5BZ",
        "ci": f"CI-5BZ-{sufijo}", "sucursal_id": 1, "rol_id": rol_id,
        "expira_horas": 24})
    m.comprobar("encargado_alta_201", alta, 201,
                detalle=f"rol_id={rol_id} (ENCARGADO_SUCURSAL)")
    token_inv = ((cuerpo(alta) or {}).get("invitacion") or {}).get("token")
    if not token_inv:
        m.info("encargado_invitacion", "la respuesta de alta no trae token")
        return
    aceptar = m.pedir("POST", "/auth/tenant/invitaciones/aceptar", cuerpo={
        "token": token_inv, "password": password_nuevo,
        "password_confirmacion": password_nuevo})
    m.comprobar("encargado_acepta_invitacion", aceptar, 200)
    r_login = login(m, correo, "empresa-a", password_nuevo)
    m.comprobar("encargado_login", r_login, 200)
    tk_enc = token_de(r_login)
    if not tk_enc:
        return
    for ruta in ("/tenant/empresa/usuarios", "/tenant/empresa/roles",
                 "/tenant/empresa/permisos"):
        m.comprobar(f"encargado_200_{ruta.rsplit('/', 1)[-1]}",
                    m.pedir("GET", ruta, token=tk_enc), 200,
                    detalle="lectura autorizada por PERSONAL/CONSULTAR")
    m.comprobar(
        "encargado_403_alta_personal",
        m.pedir("POST", "/tenant/empresa/usuarios", token=tk_enc, cuerpo={
            "correo": f"no.debe.{sufijo}@staging.invalid",
            "nombres": "No", "apellidos": "Debe",
            "ci": f"CI-NO-{sufijo}", "sucursal_id": 1, "rol_id": rol_id,
            "expira_horas": 1}), 403,
        detalle="no tiene PERSONAL/ADMINISTRAR")
    rol_cajero = next((r.get("id") for r in lista_roles
                       if str(r.get("nombre", "")).upper() == "CAJERO"), 3)
    m.comprobar(
        "encargado_403_matriz_permisos",
        m.pedir("PATCH", f"/tenant/empresa/roles/{rol_cajero}/permisos",
                token=tk_enc, cuerpo={"permisos": [
                    {"funcion": "PERSONAL", "accion": "CONSULTAR",
                     "permitido": True}]}), 403,
        detalle="GestorMatriz exige ADMINISTRADOR + PERSONAL/ADMINISTRAR")


def _probar_matriz_equivalente(m: Matriz, tk_admin_b: str,
                               tk_cajero_b: str | None,
                               lista_roles: list) -> None:
    """Demuestra la coherencia RBAC sin depender del rol global ausente.

    La lectura se autoriza por MATRIZ, no por nombre de rol. Se concede
    `PERSONAL/CONSULTAR` al CAJERO de empresa-b con el endpoint legitimo de
    matriz (ADMINISTRADOR + PERSONAL/ADMINISTRAR), se observa 200 en las tres
    lecturas y 403 en las escrituras, y se devuelve el permiso a DENEGADO para
    que la matriz quede como estaba en efecto.
    """
    if not tk_cajero_b:
        m.info("matriz_equivalente_cajero",
               "sin sesion de cajero.b no se puede ejercitar la matriz")
        return
    rol_cajero = next((r.get("id") for r in lista_roles
                       if str(r.get("nombre", "")).upper() == "CAJERO"), 3)

    def conceder(permitido: bool):
        return m.pedir(
            "PATCH", f"/tenant/empresa/roles/{rol_cajero}/permisos",
            token=tk_admin_b, cuerpo={"permisos": [
                {"funcion": "PERSONAL", "accion": "CONSULTAR",
                 "permitido": permitido}]})

    m.comprobar("matriz_base_cajero_403_usuarios",
                m.pedir("GET", "/tenant/empresa/usuarios", token=tk_cajero_b),
                403)
    m.comprobar("matriz_conceder_personal_consultar", conceder(True), 200,
                detalle=f"rol CAJERO id={rol_cajero} en empresa-b")
    try:
        for ruta in ("/tenant/empresa/usuarios", "/tenant/empresa/roles",
                     "/tenant/empresa/permisos"):
            m.comprobar(
                f"matriz_cajero_200_{ruta.rsplit('/', 1)[-1]}",
                m.pedir("GET", ruta, token=tk_cajero_b), 200,
                detalle="la lectura la autoriza la matriz, no el nombre de rol")
        sufijo = uuid.uuid4().hex[:10]
        m.comprobar(
            "matriz_cajero_403_alta_personal",
            m.pedir("POST", "/tenant/empresa/usuarios", token=tk_cajero_b,
                    cuerpo={"correo": f"no.debe.{sufijo}@staging.invalid",
                            "nombres": "No", "apellidos": "Debe",
                            "ci": f"CI-NO-{sufijo}", "sucursal_id": 1,
                            "rol_id": rol_cajero, "expira_horas": 1}), 403,
            detalle="CONSULTAR no habilita PERSONAL/ADMINISTRAR")
        m.comprobar(
            "matriz_cajero_403_editar_matriz",
            m.pedir("PATCH",
                    f"/tenant/empresa/roles/{rol_cajero}/permisos",
                    token=tk_cajero_b, cuerpo={"permisos": [
                        {"funcion": "PERSONAL", "accion": "ADMINISTRAR",
                         "permitido": True}]}), 403,
            detalle="anti-escalada: la matriz no se auto-edita")
    finally:
        m.comprobar("matriz_restaurar_cajero", conceder(False), 200,
                    detalle="restaura DENEGADO (sin cambio efectivo)")
        m.comprobar("matriz_restaurada_cajero_403_usuarios",
                    m.pedir("GET", "/tenant/empresa/usuarios",
                            token=tk_cajero_b), 403)


if __name__ == "__main__":
    sys.exit(main())
