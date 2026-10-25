"""Bloque 3 / Paso 1: cierre preventivo de rutas legacy en modo SaaS.

La base SaaS comparte tablas de varias empresas. Los routers heredados del
primer parcial aun consultan sin empresa_id y NO son seguros con tenants.
Este guard es deliberadamente restrictivo: solo deja pasar login/me SaaS
y endpoints publicos estaticos mientras se migran los routers por etapas.

No es reemplazo de RLS ni de autorizacion tenant en cada servicio.
Con SAAS_AUTH_ENABLED=false conserva el comportamiento del primer parcial.
"""
import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings


_RUTAS_PERMITIDAS_SAAS = frozenset(
    {
        ("GET", "/"),
        ("GET", "/health"),
        ("POST", "/auth/tenant/login"),
        ("GET", "/auth/tenant/me"),
        ("GET", "/tenant/empresa/me"),
        ("GET", "/tenant/empresa/plan"),
        ("GET", "/tenant/empresa/suscripcion"),
        ("GET", "/tenant/empresa/sucursales"),
        ("POST", "/tenant/empresa/sucursales"),
        ("PATCH", "/tenant/empresa/plan"),
        # CU04: catálogo empresarial/admin; nunca permitir /usuarios legacy.
        ("GET", "/tenant/empresa/usuarios"),
        ("GET", "/tenant/empresa/roles"),
    }
)


# Solo rutas NUEVAS con dependencia tenant obligatoria. No usar startswith,
# ya que permitiria subrutas no auditadas y acceso a endpoints legacy.
_RUTAS_CATALOGO_LISTADO = frozenset({
    "/tenant/catalogo/categorias",
    "/tenant/catalogo/productos",
})
_RUTAS_CATALOGO_ESCRITURA = frozenset({
    ("POST", "/tenant/catalogo/categorias"),
    ("POST", "/tenant/catalogo/productos"),
})
_RUTA_CATALOGO_PATCH = re.compile(
    r"^/tenant/catalogo/(?:categorias|productos|variantes)/[1-9][0-9]*$"
)
_RUTA_CATALOGO_POST_VARIANTE = re.compile(
    r"^/tenant/catalogo/productos/[1-9][0-9]*/variantes$"
)

# Solo los seis recursos aprobados para consultas administrativas de solo lectura.
_RECURSOS_COMERCIALES_SAAS = frozenset({
    "/tenant/comercial/carritos",
    "/tenant/comercial/reservas",
    "/tenant/comercial/ventas",
    "/tenant/comercial/pagos",
    "/tenant/comercial/compras",
    "/tenant/comercial/devoluciones",
})
_DETALLE_COMERCIAL_SAAS = re.compile(
    r"^/tenant/comercial/(?:carritos|reservas|ventas|pagos|compras|devoluciones)/[1-9][0-9]*$"
)

# Solo tres mutaciones de carrito CU15 revisadas; sin abrir CRUD legacy.
_RUTA_CARRITO_LINEA = re.compile(
    r"^/tenant/carritos/[1-9][0-9]*/items/[1-9][0-9]*$"
)
_RUTA_CARRITO = re.compile(r"^/tenant/carritos/[1-9][0-9]*$")
_RUTA_RESERVA_CU16_CU18 = re.compile(
    r"^/tenant/reservas/[1-9][0-9]*/(?:cancelar|finalizar-sin-compra)$"
)
_RUTA_EMPRESA_SUCURSAL_ESTADO = re.compile(
    r"^/tenant/empresa/sucursales/[1-9][0-9]*/estado$"
)
_RUTA_EMPRESA_MIEMBRO_CAMBIO = re.compile(
    r"^/tenant/empresa/usuarios/[1-9][0-9]*/(?:rol|estado)$"
)

_RUTAS_CATALOGO_DETALLE = re.compile(
    r"^/tenant/catalogo/(?:categorias/[1-9][0-9]*|productos/[1-9][0-9]*(?:/variantes)?|variantes/[1-9][0-9]*)$"
)


def _ruta_saas_permitida(metodo: str, ruta: str) -> bool:
    if (metodo, ruta) in _RUTAS_PERMITIDAS_SAAS:
        return True
    # Primera transaccion CU15 migrada: cliente validado, POST exacto.
    if (metodo, ruta) == ("POST", "/tenant/carritos/items"):
        return True
    # Cancelar CU16/CU17 y finalizar CU18 solo con doble opt-in.
    if metodo == "PATCH" and _RUTA_RESERVA_CU16_CU18.fullmatch(ruta):
        return settings.saas_reservas_enabled
    # CU16 comercial: doble opt-in, jamás abrir sin flag específico.
    # Un endpoint con fallas de principal SQL/RLS igualmente falla cerrado.
    if metodo == "POST" and ruta == "/tenant/reservas":
        return settings.saas_reservas_enabled
    # CU16: SOLO prevalidacion sin DML; crear/cancelar reserva siguen 503.
    if (metodo, ruta) == ("POST", "/tenant/reservas/prevalidar"):
        return True
    if metodo == "PATCH" and _RUTA_EMPRESA_SUCURSAL_ESTADO.fullmatch(ruta):
        return True
    # CU04: solo dos operaciones PATCH exactas sobre IDs positivos.
    if metodo == "PATCH" and _RUTA_EMPRESA_MIEMBRO_CAMBIO.fullmatch(ruta):
        return True
    if metodo == "PATCH" and _RUTA_CARRITO_LINEA.fullmatch(ruta) is not None:
        return True
    if metodo == "DELETE" and (
        _RUTA_CARRITO_LINEA.fullmatch(ruta) is not None
        or _RUTA_CARRITO.fullmatch(ruta) is not None
    ):
        return True
    if metodo == "GET":
        # CU15: el cliente solo puede listar sus carritos o consultar
        # uno concreto; cualquier otra subruta sigue bloqueada.
        if ruta == "/tenant/carritos" or _RUTA_CARRITO.fullmatch(ruta) is not None:
            return True
        return (
            ruta in _RUTAS_CATALOGO_LISTADO
            or _RUTAS_CATALOGO_DETALLE.fullmatch(ruta) is not None
            or ruta in _RECURSOS_COMERCIALES_SAAS
            or _DETALLE_COMERCIAL_SAAS.fullmatch(ruta) is not None
        )
    if (metodo, ruta) in _RUTAS_CATALOGO_ESCRITURA:
        return True
    if metodo == "POST":
        return _RUTA_CATALOGO_POST_VARIANTE.fullmatch(ruta) is not None
    if metodo == "PATCH":
        return _RUTA_CATALOGO_PATCH.fullmatch(ruta) is not None
    return False


class BloqueoRutasLegacySaaS:
    """Middleware ASGI: si SaaS esta activo, deniega rutas no migradas.

    Una allowlist exacta evita liberar rutas por coincidencias de prefijo
    (p. ej. /auth/tenant/login/extra o /health/db). Tampoco se permite
    abrir rutas legacy enviando un token de usuario empresarial.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not settings.saas_auth_enabled:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # Los futuros canales websocket tambien necesitan empresa_id.
            # Negar la conexion antes de aceptarla; no hay rutas WS migradas.
            await send({"type": "websocket.close", "code": 1008})
            return

        if scope["type"] != "http":
            # Eventos internos lifespan/asgi nunca son datos de un tenant.
            await self.app(scope, receive, send)
            return

        metodo = scope.get("method", "")
        ruta = scope.get("path", "")
        if _ruta_saas_permitida(metodo, ruta):
            await self.app(scope, receive, send)
            return

        respuesta = JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Ruta no disponible en modo SaaS hasta implementar "
                    "autorizacion y aislamiento por empresa"
                )
            },
        )
        await respuesta(scope, receive, send)
