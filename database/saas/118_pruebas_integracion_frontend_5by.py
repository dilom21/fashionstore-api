"""118 — Pruebas de integracion frontend Angular <-> SaaS tenant (Etapa 5BY).

Ejecuta, contra un backend FastAPI SaaS ON levantado sobre el staging local
5BX (datos 100% ficticios), la misma secuencia de llamadas que realizan los
servicios Angular nuevos:

  saas-auth.service / saas-session.service / saas-permisos
  saas-empresa.service / saas-sucursales.service / saas-personal.service
  saas-roles.service / saas-catalogo.service / saas-ventas.service
  saas-reservas.service / saas-compras.service / saas-devoluciones.service

Uso:
    python database/saas/118_pruebas_integracion_frontend_5by.py \
        --base http://127.0.0.1:8010 [--json]

No imprime ni escribe secretos: la unica credencial usada es la contrasena
sintetica de fixtures, leida del archivo de secretos local fuera de Git.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

EVIDENCIAS = Path(__file__).resolve().parent / "evidencias"
SECRETOS = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "opencode" / \
    "fs_staging_5bx" / "secrets_5bx.json"

PASS, FAIL, INFO = "PASS", "FAIL", "INFO"


class Matriz:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.cliente = httpx.Client(base_url=self.base, timeout=20.0)
        self.pruebas: list[dict] = []

    def pedir(self, metodo: str, ruta: str, *, token: str | None = None,
              cuerpo: dict | None = None):
        cabeceras = {"Authorization": f"Bearer {token}"} if token else {}
        return self.cliente.request(metodo, ruta, json=cuerpo,
                                    headers=cabeceras)

    def comprobar(self, nombre: str, resp, esperado, detalle: str = "") -> None:
        esperados = esperado if isinstance(esperado, (tuple, list)) else (esperado,)
        ok = resp.status_code in esperados
        obtenido = resp.status_code
        texto = ""
        try:
            datos = resp.json()
            texto = datos.get("detail") if isinstance(datos, dict) else ""
            if not isinstance(texto, str):
                texto = json.dumps(texto, ensure_ascii=False)[:200]
        except Exception:  # noqa: BLE001 - cuerpo no JSON es un dato, no un error
            texto = ""
        self.pruebas.append({
            "nombre": nombre,
            "esperado": list(esperados),
            "obtenido": obtenido,
            "estado": PASS if ok else FAIL,
            "detalle": (detalle or (texto or ""))[:300],
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not SECRETOS.exists():
        print(f"FALTA el archivo de secretos sinteticos: {SECRETOS}")
        return 2
    secretos = json.loads(SECRETOS.read_text(encoding="utf-8"))
    password = secretos.get("password_usuarios")
    if not password:
        print("El archivo de secretos no contiene password_usuarios")
        return 2

    m = Matriz(args.base)

    # ---------- salud ----------
    m.comprobar("health", m.pedir("GET", "/health"), 200)

    # ---------- autenticacion ----------
    r_aa = login(m, "admin@staging.invalid", "empresa-a", password)
    m.comprobar("login_A_admin", r_aa, 200)
    tk_a = token_de(r_aa)
    r_ab = login(m, "admin.b@staging.invalid", "empresa-b", password)
    m.comprobar("login_B_admin", r_ab, 200)
    tk_b = token_de(r_ab)

    m.comprobar("login_cruzado_A_en_B",
                login(m, "admin@staging.invalid", "empresa-b", password), 401)
    m.comprobar("login_password_incorrecta",
                login(m, "admin@staging.invalid", "empresa-a", "incorrecta-5by"), 401)
    m.comprobar("login_empresa_suspendida",
                login(m, "suspendida@staging.invalid", "empresa-c", password), 401)
    m.comprobar("login_empresa_inactiva",
                login(m, "inactiva@staging.invalid", "empresa-d", password), 401)

    extra = m.pedir("POST", "/auth/tenant/login", cuerpo={
        "correo": "admin@staging.invalid", "password": password,
        "empresa_slug": "empresa-a", "contexto": "personal",
        "empresa_id": 2,
    })
    m.comprobar("login_campo_extra_422", extra, 422,
                detalle="extra=forbid impide fijar empresa desde el cliente")

    if not tk_a or not tk_b:
        print("Sin token: no se puede continuar")
        print(json.dumps(m.resumen(), ensure_ascii=False))
        return 1

    # ---------- identidad ----------
    me_a = m.pedir("GET", "/auth/tenant/me", token=tk_a)
    m.comprobar("me_A", me_a, 200)
    cuerpo_me = me_a.json() if me_a.status_code == 200 else {}
    m.anotar("me_A_campos_contrato",
             "usuario_id,empresa_slug,rol,contexto,plan_codigo",
             sorted(cuerpo_me.keys()) if isinstance(cuerpo_me, dict) else "-",
             isinstance(cuerpo_me, dict)
             and {"usuario_id", "empresa_slug", "rol", "contexto",
                  "plan_codigo"} <= set(cuerpo_me))
    me_b = m.pedir("GET", "/auth/tenant/me", token=tk_b)
    m.comprobar("me_B", me_b, 200)
    m.anotar("me_A_B_empresas_distintas", "slug distinto",
             f"{cuerpo_me.get('empresa_slug')}/{(me_b.json() or {}).get('empresa_slug')}",
             cuerpo_me.get("empresa_slug") != (me_b.json() or {}).get("empresa_slug"))
    m.comprobar("me_sin_token", m.pedir("GET", "/auth/tenant/me"), 401)
    m.comprobar("me_token_alterado",
                m.pedir("GET", "/auth/tenant/me", token=tk_a[:-4] + "aaaa"), 401)

    # ---------- empresa / plan ----------
    empresa_a = m.pedir("GET", "/tenant/empresa/me", token=tk_a)
    m.comprobar("empresa_me_A", empresa_a, 200)
    datos_empresa = empresa_a.json() if empresa_a.status_code == 200 else {}
    suscripcion = (datos_empresa or {}).get("suscripcion") or {}
    m.anotar("empresa_A_plan_PROFESIONAL", "PROFESIONAL",
             suscripcion.get("codigo"), suscripcion.get("codigo") == "PROFESIONAL")
    m.comprobar("plan_A", m.pedir("GET", "/tenant/empresa/plan", token=tk_a), 200)
    plan_b = m.pedir("GET", "/tenant/empresa/plan", token=tk_b)
    m.comprobar("plan_B", plan_b, 200)
    m.anotar("plan_B_limite_2", 2, (plan_b.json() or {}).get("max_sucursales_activas"),
             (plan_b.json() or {}).get("max_sucursales_activas") == 2)
    m.comprobar("suscripcion_A",
                m.pedir("GET", "/tenant/empresa/suscripcion", token=tk_a), 200)
    m.comprobar("cambio_plan_503",
                m.pedir("PATCH", "/tenant/empresa/plan", token=tk_a,
                        cuerpo={"codigo": "BASICO"}), 503,
                detalle="solo superadmin; el gate lo bloquea")

    # ---------- permisos / roles / personal ----------
    permisos = m.pedir("GET", "/tenant/empresa/permisos", token=tk_a)
    m.comprobar("permisos_A", permisos, 200)
    cuerpo_perm = permisos.json() if permisos.status_code == 200 else {}
    catalogo_perm = (cuerpo_perm or {}).get("catalogo") or []
    m.anotar("permisos_catalogo_no_vacio", ">=1", len(catalogo_perm),
             len(catalogo_perm) >= 1)
    m.comprobar("roles_A", m.pedir("GET", "/tenant/empresa/roles", token=tk_a), 200)

    usuarios_a = m.pedir("GET", "/tenant/empresa/usuarios", token=tk_a)
    usuarios_b = m.pedir("GET", "/tenant/empresa/usuarios", token=tk_b)
    m.comprobar("usuarios_A", usuarios_a, 200)
    m.comprobar("usuarios_B", usuarios_b, 200)
    ids_a = {u.get("usuario_id") for u in (usuarios_a.json() or [])} \
        if usuarios_a.status_code == 200 else set()
    ids_b = {u.get("usuario_id") for u in (usuarios_b.json() or [])} \
        if usuarios_b.status_code == 200 else set()
    m.anotar("usuarios_A_B_disjuntos", "interseccion vacia",
             f"A={sorted(ids_a)} B={sorted(ids_b)}",
             bool(ids_a and ids_b and not (ids_a & ids_b)))

    r_cli = login(m, "cliente@staging.invalid", "empresa-a", password,
                  contexto="cliente")
    m.comprobar("login_cliente_A", r_cli, 200)
    tk_cli = token_de(r_cli)
    if tk_cli:
        m.comprobar("cliente_no_administra_usuarios",
                    m.pedir("GET", "/tenant/empresa/usuarios", token=tk_cli), 403)

    # un rol no administrador debe recibir 403 en el listado de personal
    cajero = next((u for u in (usuarios_b.json() or [])
                   if u.get("rol") == "CAJERO" and u.get("membresia_activa")),
                  None)
    if cajero:
        r_caj = login(m, cajero["correo"], "empresa-b", password)
        tk_caj = token_de(r_caj)
        if tk_caj:
            m.comprobar("cajero_no_lista_personal",
                        m.pedir("GET", "/tenant/empresa/usuarios", token=tk_caj),
                        403)
    else:
        m.info("cajero_no_lista_personal",
               "empresa-b no tiene membresia CAJERO activa en el staging")

    # ---------- catalogo ----------
    cat_a = m.pedir("GET", "/tenant/catalogo/categorias", token=tk_a)
    cat_b = m.pedir("GET", "/tenant/catalogo/categorias", token=tk_b)
    m.comprobar("catalogo_categorias_A", cat_a, 200)
    m.comprobar("catalogo_categorias_B", cat_b, 200)
    ids_cat_a = {c.get("id") for c in (cat_a.json() or [])} if cat_a.status_code == 200 else set()
    ids_cat_b = {c.get("id") for c in (cat_b.json() or [])} if cat_b.status_code == 200 else set()
    m.anotar("categorias_A_B_disjuntas", "interseccion vacia",
             f"A={sorted(ids_cat_a)} B={sorted(ids_cat_b)}",
             bool(ids_cat_a and ids_cat_b and not (ids_cat_a & ids_cat_b)))

    prod_a = m.pedir("GET", "/tenant/catalogo/productos?limit=50&offset=0", token=tk_a)
    prod_b = m.pedir("GET", "/tenant/catalogo/productos?limit=50&offset=0", token=tk_b)
    m.comprobar("catalogo_productos_A", prod_a, 200)
    m.comprobar("catalogo_productos_B", prod_b, 200)
    ids_p_a = {p.get("id") for p in (prod_a.json() or [])} if prod_a.status_code == 200 else set()
    ids_p_b = {p.get("id") for p in (prod_b.json() or [])} if prod_b.status_code == 200 else set()
    m.anotar("productos_A_B_disjuntos", "interseccion vacia",
             f"A={sorted(ids_p_a)} B={sorted(ids_p_b)}",
             bool(ids_p_a and ids_p_b and not (ids_p_a & ids_p_b)))
    if ids_p_b:
        ajeno = sorted(ids_p_b)[0]
        m.comprobar("producto_ajeno_404",
                    m.pedir("GET", f"/tenant/catalogo/productos/{ajeno}", token=tk_a),
                    404)
        m.comprobar("variantes_producto",
                    m.pedir("GET", f"/tenant/catalogo/productos/{ajeno}/variantes",
                            token=tk_a), 404)

    # ---------- sucursales y limite del plan ----------
    suc_b = m.pedir("GET", "/tenant/empresa/sucursales", token=tk_b)
    m.comprobar("sucursales_B_lista", suc_b, 200)
    activas_b = [s for s in (suc_b.json() or []) if s.get("estado")] \
        if suc_b.status_code == 200 else []
    sufijo = uuid.uuid4().hex[:6]
    if len(activas_b) < 2:
        creada = m.pedir("POST", "/tenant/empresa/sucursales", token=tk_b, cuerpo={
            "ciudad_id": 1, "nombre": f"Sucursal B P0 {sufijo}",
            "direccion": "Calle de staging 5BY"})
        m.comprobar("sucursal_B_segunda_201", creada, 201)
        nueva_id = (creada.json() or {}).get("id") if creada.status_code == 201 else None
        m.comprobar("sucursal_B_tercera_409_limite",
                    m.pedir("POST", "/tenant/empresa/sucursales", token=tk_b, cuerpo={
                        "ciudad_id": 1, "nombre": f"Sucursal B P0 extra {sufijo}",
                        "direccion": "Calle de staging 5BY"}), 409)
        if nueva_id:
            m.comprobar("sucursal_B_desactivar",
                        m.pedir("PATCH",
                                f"/tenant/empresa/sucursales/{nueva_id}/estado",
                                token=tk_b, cuerpo={"estado": False}), 200)
    else:
        m.comprobar("sucursal_B_tercera_409_limite",
                    m.pedir("POST", "/tenant/empresa/sucursales", token=tk_b, cuerpo={
                        "ciudad_id": 1, "nombre": f"Sucursal B P0 extra {sufijo}",
                        "direccion": "Calle de staging 5BY"}), 409,
                    detalle="limite BASICO ya alcanzado en el staging")

    # ---------- venta de contado idempotente ----------
    operacion = str(uuid.uuid4())
    venta = m.pedir("POST", "/tenant/ventas/contado", token=tk_a, cuerpo={
        "sucursal_id": 1, "operacion_id": operacion,
        "lineas": [{"inventario_id": 1, "cantidad": 1}]})
    m.comprobar("venta_contado_201", venta, 201)
    v1 = venta.json() if venta.status_code == 201 else {}
    replay = m.pedir("POST", "/tenant/ventas/contado", token=tk_a, cuerpo={
        "sucursal_id": 1, "operacion_id": operacion,
        "lineas": [{"inventario_id": 1, "cantidad": 1}]})
    m.comprobar("venta_replay_200", replay, (200, 201))
    v2 = replay.json() if replay.status_code in (200, 201) else {}
    m.anotar("venta_replay_mismo_id", v1.get("venta_id"), v2.get("venta_id"),
             bool(v1) and v1.get("venta_id") == v2.get("venta_id"))
    m.anotar("venta_replay_idempotente", True, v2.get("idempotente"),
             v2.get("idempotente") is True)
    stock1 = (v1.get("stock") or [{}])[0].get("stock_actual")
    stock2 = (v2.get("stock") or [{}])[0].get("stock_actual")
    m.anotar("venta_stock_una_sola_vez", stock1, stock2,
             stock1 is not None and stock1 == stock2,
             detalle="el replay no vuelve a descontar")
    m.comprobar("venta_operacion_reutilizada_409",
                m.pedir("POST", "/tenant/ventas/contado", token=tk_a, cuerpo={
                    "sucursal_id": 1, "operacion_id": operacion,
                    "lineas": [{"inventario_id": 1, "cantidad": 2}]}), 409)
    m.comprobar("venta_inventario_ajeno_403",
                m.pedir("POST", "/tenant/ventas/contado", token=tk_a, cuerpo={
                    "sucursal_id": 1, "operacion_id": str(uuid.uuid4()),
                    "lineas": [{"inventario_id": 100, "cantidad": 1}]}), 403)
    m.comprobar("venta_sin_lineas_422",
                m.pedir("POST", "/tenant/ventas/contado", token=tk_a, cuerpo={
                    "sucursal_id": 1, "operacion_id": str(uuid.uuid4()),
                    "lineas": []}), 422)
    ventas_com = m.pedir("GET", "/tenant/comercial/ventas?limit=20", token=tk_a)
    m.comprobar("comercial_ventas_A", ventas_com, 200)

    # ---------- reservas ----------
    reservas_com = m.pedir("GET", "/tenant/comercial/reservas?limit=20", token=tk_a)
    m.comprobar("comercial_reservas_A", reservas_com, 200)
    # El router traduce sqlstate 42501 (RLS: la reserva no existe o no es
    # visible para la empresa) a 403, no a 404. La UI maneja ambos.
    m.comprobar("reserva_cancelar_inexistente_403",
                m.pedir("PATCH", "/tenant/reservas/999999/cancelar",
                        token=tk_a, cuerpo={"observacion": "prueba 5BY"}),
                (403, 404),
                detalle="42501 -> 403 Operacion no autorizada (no 404)")
    m.comprobar("reserva_cancelar_sin_body_422",
                m.pedir("PATCH", "/tenant/reservas/999999/cancelar", token=tk_a),
                422)
    m.comprobar("reservas_trailing_slash_503",
                m.pedir("POST", "/tenant/reservas/", token=tk_cli, cuerpo={
                    "carrito_id": 1,
                    "fecha_atencion": (datetime.now(timezone.utc)
                                       + timedelta(days=2)).isoformat()}), 503,
                detalle="el gate exige la ruta sin barra final")

    # ---------- compras ----------
    compras = m.pedir("GET", "/tenant/ordenes-compra?limit=20", token=tk_a)
    m.comprobar("compras_lista_A", compras, 200)
    lista_compras = compras.json() if compras.status_code == 200 else []
    if lista_compras:
        detalle = m.pedir("GET",
                          f"/tenant/ordenes-compra/{lista_compras[0].get('id')}",
                          token=tk_a)
        m.comprobar("compras_detalle_A", detalle, 200)
    else:
        m.info("compras_detalle_A", "sin ordenes en empresa-a; contrato no ejercitado")

    # ---------- devoluciones ----------
    devoluciones = m.pedir("GET", "/tenant/devoluciones?limit=20", token=tk_a)
    m.comprobar("devoluciones_lista_A", devoluciones, 200)
    lista_dev = devoluciones.json() if devoluciones.status_code == 200 else []
    if lista_dev:
        det = m.pedir("GET", f"/tenant/devoluciones/{lista_dev[0].get('id')}",
                      token=tk_a)
        m.comprobar("devoluciones_detalle_A", det, 200)
    else:
        m.info("devoluciones_detalle_A",
               "sin devoluciones en empresa-a; contrato no ejercitado")

    # ---------- bloqueo de rutas legacy con SaaS ON ----------
    m.comprobar("legacy_productos_503",
                m.pedir("GET", "/productos", token=tk_a), 503)
    m.comprobar("legacy_login_503",
                m.pedir("POST", "/auth/personal/login", cuerpo={
                    "correo": "admin@staging.invalid", "password": password}), 503)
    m.comprobar("legacy_me_503",
                m.pedir("GET", "/auth/me", token=tk_a), 503)

    resumen = m.resumen()
    datos = {
        "etapa": "5BY",
        "sello_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base": m.base,
        "datos_reales": False,
        "secretos_incluidos": False,
        "resumen": resumen,
        "pruebas": m.pruebas,
    }
    EVIDENCIAS.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = EVIDENCIAS / f"EVIDENCIA_INTEGRACION_FRONTEND_5BY_{sello}.json"
    destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    if args.json:
        print(json.dumps(datos, ensure_ascii=False, indent=2))
    else:
        for p in m.pruebas:
            print(f"[{p['estado']:4}] {p['nombre']:<38} "
                  f"esperado={p['esperado']} obtenido={p['obtenido']} "
                  f"{p['detalle']}")
        print()
        print(f"RESUMEN: {resumen['pass']} PASS / {resumen['fail']} FAIL / "
              f"{resumen['info']} INFO de {resumen['total']}")
        print(f"Evidencia: {destino}")

    return 0 if resumen["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
