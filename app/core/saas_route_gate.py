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
from app.core.surface_guard import SURFACE_LEGACY, superficie_de_ruta

# 5C1: rutas que no se abren aunque la superficie legacy este habilitada.
# `/health/db` consulta el engine tenant directamente y expone el destino;
# la documentacion interactiva describe contratos que no deben publicarse.
_RUTAS_SIEMPRE_BLOQUEADAS_EN_SAAS = frozenset(
    {
        "/health/db",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }
)


# CU01/CU02: rutas globales aisladas, SOLO con segundo opt-in.
_RUTAS_SAAS_SUPERADMIN = frozenset({
    ("GET","/saas/superadmin/empresas"),
    ("POST","/saas/superadmin/empresas"),
    ("GET","/saas/superadmin/planes"),
    ("POST","/saas/superadmin/planes"),
    # 5BO: verificacion de rotacion y auditoria global (solo lectura).
    ("GET","/saas/superadmin/claves"),
    ("GET","/saas/superadmin/auditoria"),
})
_RUTA_SAAS_SUPERADMIN_CAMBIO = re.compile(
    r"^/saas/superadmin/(?:empresas/[1-9][0-9]*/estado|planes/[1-9][0-9]*/estado|empresas/[1-9][0-9]*/suscripciones/[1-9][0-9]*/estado)$"
)
_RUTA_SAAS_SUPERADMIN_SUSCRIPCIONES = re.compile(
    r"^/saas/superadmin/empresas/[1-9][0-9]*/suscripciones$"
)

_RUTA_SAAS_SUPERADMIN_BOOTSTRAP = re.compile(
    r"^/saas/superadmin/empresas/[1-9][0-9]*/administrador-inicial$"
)

_RUTAS_PERMITIDAS_SAAS = frozenset(
    {
        ("GET", "/"),
        ("GET", "/health"),
        # 5C1: diagnostico de superficies y conectividad, sin secretos ni DSN.
        ("GET", "/health/superficies"),
        ("POST", "/auth/tenant/login"),
        ("GET", "/auth/tenant/me"),
        # 5BO: aceptacion de invitacion de personal (sin JWT; token de un solo uso).
        ("POST", "/auth/tenant/invitaciones/aceptar"),
        ("GET", "/tenant/empresa/me"),
        ("GET", "/tenant/empresa/plan"),
        ("GET", "/tenant/empresa/suscripcion"),
        ("GET", "/tenant/empresa/sucursales"),
        ("POST", "/tenant/empresa/sucursales"),
        # Cambio de plan empresarial por autoservicio bloqueado en SaaS:
        # CU02 exige autenticación de SUPERADMIN global.

        # CU04: catálogo empresarial/admin; nunca permitir /usuarios legacy.
        ("GET", "/tenant/empresa/usuarios"),
        ("GET", "/tenant/empresa/roles"),
        # 5BO: alta de personal nuevo (invitacion) y matriz RBAC nominal.
        ("POST", "/tenant/empresa/usuarios"),
        ("GET", "/tenant/empresa/permisos"),
    }
)


# Solo rutas NUEVAS con dependencia tenant obligatoria. No usar startswith,
# ya que permitiria subrutas no auditadas y acceso a endpoints legacy.
_RUTAS_CATALOGO_LISTADO = frozenset({
    "/tenant/catalogo/categorias",
    "/tenant/catalogo/productos",
    # 5BZ: listados que alimentan los formularios de la UI. Los diccionarios
    # globales (tallas/colores/ciudades) y los recursos empresariales
    # (proveedores/temporadas/variantes) son GET exactos sin parametro de ruta.
    "/tenant/catalogo/tallas",
    "/tenant/catalogo/colores",
    "/tenant/catalogo/ciudades",
    "/tenant/catalogo/proveedores",
    "/tenant/catalogo/temporadas",
    "/tenant/catalogo/variantes",
})

# 5BZ: inventario tenant por sucursal. Ruta EXACTA: la barra final o cualquier
# subruta queda fuera de la allowlist y responde 503.
_RUTA_INVENTARIO_TENANT = "/tenant/inventario"
# T03: consultas tenant de auditoria y Kardex. Se mantienen rutas exactas y
# detalles limitados a IDs positivos para no abrir subrutas por prefijo.
_RUTAS_T03_TENANT = frozenset({
    "/tenant/bitacora",
    "/tenant/bitacora/catalogos",
    "/tenant/movimientos-inventario",
})
_RUTA_T03_DETALLE = re.compile(
    r"^/tenant/(?:bitacora|movimientos-inventario)/[1-9][0-9]*$"
)
# T02/T04: canal cliente. El webhook usa slug publico validado y firma Stripe;
# ninguna ruta acepta empresa_id desde el request.
_RUTAS_CLIENTE_TENANT_GET = frozenset({
    "/tenant/cliente/reservas",
    "/tenant/ventas/historial",
})
_RUTA_CLIENTE_RESERVA_DETALLE = re.compile(
    r"^/tenant/cliente/reservas/[1-9][0-9]*$"
)
_RUTAS_CLIENTE_TENANT_POST = frozenset({
    "/tenant/ventas/digital",
    "/tenant/pagos/stripe/intencion",
})
_RUTA_CLIENTE_COMPROBANTE = re.compile(
    r"^/tenant/ventas/[1-9][0-9]*/comprobante$"
)
_RUTA_CLIENTE_VENTA_DETALLE = re.compile(
    r"^/tenant/ventas/historial/[1-9][0-9]*$"
)
_RUTA_CLIENTE_ESTADO_PAGO = re.compile(
    r"^/tenant/pagos/stripe/ventas/[1-9][0-9]*/estado$"
)
_RUTA_WEBHOOK_STRIPE_TENANT = re.compile(
    r"^/tenant/public/[a-z0-9]+(?:-[a-z0-9]+)*/pagos/stripe/webhook$"
)
# T05/T06: audited public and authenticated tenant routes. Every dynamic
# segment is constrained; no module is opened by prefix.
_RUTA_PUBLICA_T05 = re.compile(
    r"^/tenant/public/[a-z0-9]+(?:-[a-z0-9]+)*/(?:catalogo|sucursales|temporadas-colecciones|promociones)$"
)
_RUTA_PUBLICA_PRODUCTO_T05 = re.compile(
    r"^/tenant/public/[a-z0-9]+(?:-[a-z0-9]+)*/catalogo/[1-9][0-9]*$"
)
_RUTAS_REPORTES_T06 = frozenset({
    ("GET", "/tenant/reportes/resumen"),
    ("POST", "/tenant/reportes/consulta"),
    ("GET", "/tenant/reportes/analitica/ml"),
    ("POST", "/tenant/reportes/generativo"),
    ("POST", "/tenant/asistencia-inteligente/recomendaciones"),
})
_RUTA_REPORTE_EXPORTAR_T06 = re.compile(
    r"^/tenant/reportes/exportar/(?:ventas|inventario|reservas|clientes|productos)$"
)
_RUTA_VESTIDOR_CONFIG = re.compile(
    r"^/tenant/vestidor-virtual/productos/[1-9][0-9]*/configuraciones$"
)
_RUTA_VESTIDOR_PRUEBA = re.compile(
    r"^/tenant/vestidor-virtual/sesiones/[1-9][0-9]*/pruebas$"
)
_RUTA_VESTIDOR_FINALIZAR = re.compile(
    r"^/tenant/vestidor-virtual/(?:pruebas|sesiones)/[1-9][0-9]*/finalizar$"
)
_RUTA_MERCH_LISTA = re.compile(
    r"^/tenant/merchandising/(?:temporadas|colecciones|promociones)$"
)
_RUTA_MERCH_ITEM = re.compile(
    r"^/tenant/merchandising/(?:temporadas|colecciones|promociones)/[1-9][0-9]*$"
)
_RUTA_MERCH_ESTADO = re.compile(
    r"^/tenant/merchandising/(?:temporadas|colecciones|promociones)/[1-9][0-9]*/estado$"
)
_RUTA_MERCH_PRODUCTOS = re.compile(
    r"^/tenant/merchandising/(?:colecciones|promociones)/[1-9][0-9]*/productos$"
)
_RUTA_MERCH_PRECIO = re.compile(
    r"^/tenant/merchandising/productos/[1-9][0-9]*/precio-promocional$"
)
_RUTAS_CATALOGO_ESCRITURA = frozenset({
    ("POST", "/tenant/catalogo/categorias"),
    ("POST", "/tenant/catalogo/productos"),
})
_RUTA_CATALOGO_PATCH = re.compile(
    r"^/tenant/catalogo/(?:categorias|productos|variantes)/[1-9][0-9]*(?:/estado)?$"
)
_RUTA_CATALOGO_POST_VARIANTE = re.compile(
    r"^/tenant/catalogo/productos/[1-9][0-9]*/variantes$"
)
_RUTA_CATALOGO_RECURSO = re.compile(
    r"^/tenant/catalogo/(?:productos/[1-9][0-9]*/recursos|recursos-producto/[1-9][0-9]*(?:/(?:estado|principal))?)$"
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
_RUTA_EMPRESA_SUCURSAL = re.compile(
    r"^/tenant/empresa/sucursales/[1-9][0-9]*$"
)
_RUTA_EMPRESA_MIEMBRO_CAMBIO = re.compile(
    r"^/tenant/empresa/usuarios/[1-9][0-9]*/(?:rol|estado|sucursal)$"
)
_RUTA_EMPRESA_ROL_PERMISOS = re.compile(
    r"^/tenant/empresa/roles/[1-9][0-9]*/permisos$"
)

_RUTAS_CATALOGO_DETALLE = re.compile(
    r"^/tenant/catalogo/(?:categorias/[1-9][0-9]*|productos/[1-9][0-9]*(?:/variantes)?|variantes/[1-9][0-9]*)$"
)

# E2/5BN: compras y devoluciones. Metodo + ruta EXACTOS y flag propio por
# modulo; nunca prefijos. Un ID no positivo no entra en la allowlist.
_RUTA_ORDEN_COMPRA_DETALLE = re.compile(r"^/tenant/ordenes-compra/[1-9][0-9]*$")
_RUTA_ORDEN_COMPRA_ACCION = re.compile(
    r"^/tenant/ordenes-compra/[1-9][0-9]*/(?:enviar|cancelar|recepciones)$"
)
_RUTA_DEVOLUCION_DETALLE = re.compile(r"^/tenant/devoluciones/[1-9][0-9]*$")
_RUTA_DEVOLUCION_ACCION = re.compile(
    r"^/tenant/devoluciones/[1-9][0-9]*/(?:aprobar|rechazar|procesar)$"
)
# 5BZ: lineas de venta para devolucion. Ruta EXACTA con prefijo literal
# `ventas/`: el segmento no numerico la excluye de las reglas de ID.
_RUTA_DEVOLUCION_LINEAS_VENTA = re.compile(
    r"^/tenant/devoluciones/ventas/[1-9][0-9]*/lineas$"
)

# 5C0: alta tenant de proveedores y su asociacion de productos. Metodo + ruta
# EXACTOS; comparte el flag de compras porque desbloquea el alta de ordenes.
_RUTA_PROVEEDOR_TENANT = "/tenant/proveedores"
_RUTA_PROVEEDOR_DETALLE = re.compile(
    r"^/tenant/proveedores/[1-9][0-9]*$"
)
_RUTA_PROVEEDOR_PRODUCTOS = re.compile(
    r"^/tenant/proveedores/[1-9][0-9]*/productos$"
)


def _ruta_saas_permitida(metodo: str, ruta: str) -> bool:
    # CU01/CU02: opt-in y rutas exactas. La dependencia del router
    # comprueba además la clave superadmin, independiente del JWT tenant.
    if settings.saas_superadmin_enabled:
        if (metodo,ruta) in _RUTAS_SAAS_SUPERADMIN:
            return True
        if metodo=="PATCH" and _RUTA_SAAS_SUPERADMIN_CAMBIO.fullmatch(ruta):
            return True
        if metodo in ("GET","POST") and (
            _RUTA_SAAS_SUPERADMIN_SUSCRIPCIONES.fullmatch(ruta)):
            return True
        if metodo == "POST" and _RUTA_SAAS_SUPERADMIN_BOOTSTRAP.fullmatch(ruta):
            return True
    if (metodo, ruta) in _RUTAS_PERMITIDAS_SAAS:
        return True
    if metodo == "GET" and (
        _RUTA_PUBLICA_T05.fullmatch(ruta) is not None
        or _RUTA_PUBLICA_PRODUCTO_T05.fullmatch(ruta) is not None
    ):
        return True
    if (metodo, ruta) in _RUTAS_REPORTES_T06:
        return True
    if metodo == "GET" and _RUTA_REPORTE_EXPORTAR_T06.fullmatch(ruta):
        return True
    if metodo == "GET" and _RUTA_VESTIDOR_CONFIG.fullmatch(ruta):
        return True
    if metodo == "POST" and (
        ruta == "/tenant/vestidor-virtual/sesiones"
        or _RUTA_VESTIDOR_PRUEBA.fullmatch(ruta) is not None
    ):
        return True
    if metodo == "PATCH" and _RUTA_VESTIDOR_FINALIZAR.fullmatch(ruta):
        return True
    if metodo in ("GET", "POST") and _RUTA_MERCH_LISTA.fullmatch(ruta):
        return True
    if metodo == "GET" and (
        _RUTA_MERCH_ITEM.fullmatch(ruta) is not None
        or _RUTA_MERCH_PRODUCTOS.fullmatch(ruta) is not None
        or _RUTA_MERCH_PRECIO.fullmatch(ruta) is not None
    ):
        return True
    if metodo == "PATCH" and (
        _RUTA_MERCH_ITEM.fullmatch(ruta) is not None
        or _RUTA_MERCH_ESTADO.fullmatch(ruta) is not None
    ):
        return True
    if metodo == "PUT" and _RUTA_MERCH_PRODUCTOS.fullmatch(ruta):
        return True
    if metodo == "POST" and _RUTA_WEBHOOK_STRIPE_TENANT.fullmatch(ruta):
        return True
    # 5BZ: lineas de venta para devolucion. Se evalua ANTES de cualquier regla
    # generica (catalogo/comercial) y exige el flag propio del modulo.
    if metodo == "GET" and (
        _RUTA_DEVOLUCION_LINEAS_VENTA.fullmatch(ruta) is not None
    ):
        return bool(getattr(settings, "saas_devoluciones_enabled", False))
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
    # E1/5BM: venta presencial de contado, flag propio OFF por defecto.
    if metodo == "POST" and ruta == "/tenant/ventas/contado":
        return bool(getattr(settings, "saas_ventas_enabled", False))
    if metodo == "GET" and re.fullmatch(
        r"/tenant/ventas/contado/[1-9][0-9]*/comprobante", ruta
    ):
        return bool(getattr(settings, "saas_ventas_enabled", False))
    # CU16: SOLO prevalidacion sin DML; crear/cancelar reserva siguen 503.
    if (metodo, ruta) == ("POST", "/tenant/reservas/prevalidar"):
        return True
    if metodo == "PATCH" and _RUTA_EMPRESA_SUCURSAL_ESTADO.fullmatch(ruta):
        return True
    if metodo in ("GET", "PATCH") and _RUTA_EMPRESA_SUCURSAL.fullmatch(ruta):
        return True
    # CU04: solo dos operaciones PATCH exactas sobre IDs positivos.
    if metodo == "PATCH" and _RUTA_EMPRESA_MIEMBRO_CAMBIO.fullmatch(ruta):
        return True
    # 5BO: matrices de permisos por rol de la propia empresa.
    if metodo == "PATCH" and _RUTA_EMPRESA_ROL_PERMISOS.fullmatch(ruta):
        return True
    if metodo == "PATCH" and _RUTA_CARRITO_LINEA.fullmatch(ruta) is not None:
        return True
    if metodo == "DELETE" and (
        _RUTA_CARRITO_LINEA.fullmatch(ruta) is not None
        or _RUTA_CARRITO.fullmatch(ruta) is not None
    ):
        return True
    # E2/5BN: compras y recepciones, flag propio y ruta exacta.
    if ruta == "/tenant/ordenes-compra":
        return metodo in ("GET", "POST") and bool(
            getattr(settings, "saas_compras_enabled", False))
    # 5C0: alta de proveedores (POST) y reemplazo de productos (PUT).
    if ruta == _RUTA_PROVEEDOR_TENANT:
        return metodo in ("GET", "POST") and bool(
            getattr(settings, "saas_compras_enabled", False))
    if metodo == "GET" and (
        _RUTA_PROVEEDOR_DETALLE.fullmatch(ruta) is not None
        or _RUTA_PROVEEDOR_PRODUCTOS.fullmatch(ruta) is not None
    ):
        return bool(getattr(settings, "saas_compras_enabled", False))
    if metodo == "PUT" and (
        _RUTA_PROVEEDOR_PRODUCTOS.fullmatch(ruta) is not None
    ):
        return bool(getattr(settings, "saas_compras_enabled", False))
    if metodo in ("GET", "POST") and (
        _RUTA_ORDEN_COMPRA_DETALLE.fullmatch(ruta) is not None
    ):
        return bool(getattr(settings, "saas_compras_enabled", False))
    if metodo == "POST" and (
        _RUTA_ORDEN_COMPRA_ACCION.fullmatch(ruta) is not None
    ):
        return bool(getattr(settings, "saas_compras_enabled", False))
    # E2/5BN: devoluciones tenant, flag propio y ruta exacta.
    if ruta == "/tenant/devoluciones":
        return metodo in ("GET", "POST") and bool(
            getattr(settings, "saas_devoluciones_enabled", False))
    if metodo == "GET" and (
        _RUTA_DEVOLUCION_DETALLE.fullmatch(ruta) is not None
    ):
        return bool(getattr(settings, "saas_devoluciones_enabled", False))
    if metodo == "POST" and (
        _RUTA_DEVOLUCION_ACCION.fullmatch(ruta) is not None
    ):
        return bool(getattr(settings, "saas_devoluciones_enabled", False))
    if metodo == "GET":
        # CU15: el cliente solo puede listar sus carritos o consultar
        # uno concreto; cualquier otra subruta sigue bloqueada.
        if ruta == "/tenant/carritos" or _RUTA_CARRITO.fullmatch(ruta) is not None:
            return True
        return (
            ruta in _RUTAS_CATALOGO_LISTADO
            or ruta == _RUTA_INVENTARIO_TENANT
            or ruta in _RUTAS_T03_TENANT
            or _RUTA_T03_DETALLE.fullmatch(ruta) is not None
            or ruta in _RUTAS_CLIENTE_TENANT_GET
            or _RUTA_CLIENTE_RESERVA_DETALLE.fullmatch(ruta) is not None
            or _RUTA_CLIENTE_COMPROBANTE.fullmatch(ruta) is not None
            or _RUTA_CLIENTE_VENTA_DETALLE.fullmatch(ruta) is not None
            or _RUTA_CLIENTE_ESTADO_PAGO.fullmatch(ruta) is not None
            or _RUTAS_CATALOGO_DETALLE.fullmatch(ruta) is not None
            or _RUTA_CATALOGO_RECURSO.fullmatch(ruta) is not None
            or ruta in _RECURSOS_COMERCIALES_SAAS
            or _DETALLE_COMERCIAL_SAAS.fullmatch(ruta) is not None
        )
    if (metodo, ruta) in _RUTAS_CATALOGO_ESCRITURA:
        return True
    if metodo == "POST" and ruta in _RUTAS_CLIENTE_TENANT_POST:
        return True
    if metodo == "POST":
        return (
            _RUTA_CATALOGO_POST_VARIANTE.fullmatch(ruta) is not None
            or re.fullmatch(r"/tenant/catalogo/productos/[1-9][0-9]*/recursos", ruta) is not None
        )
    if metodo == "PATCH":
        return (
            _RUTA_CATALOGO_PATCH.fullmatch(ruta) is not None
            or _RUTA_CATALOGO_RECURSO.fullmatch(ruta) is not None
        )
    return False


class BloqueoRutasLegacySaaS:
    """Middleware ASGI: en modo SaaS solo deja pasar lo autorizado.

    Reglas (5C1, tras separar las dos superficies):

    1. ``saas_auth_enabled=false`` -> comportamiento del primer parcial intacto.
    2. ``saas_auth_enabled=true``  -> la superficie tenant sigue una allowlist
       EXACTA (sin prefijos abiertos) para no liberar subrutas no auditadas.
    3. La superficie legacy solo se abre si ``legacy_surface_enabled=true``; el
       arranque ya garantizo que en ese caso tiene un destino de datos propio
       (ver ``app.core.surface_guard``). No se relaja ninguna RLS ni se acepta
       un token de empresa en una ruta heredada.

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

        ruta = scope.get("path", "")

        if scope["type"] == "websocket":
            # Un canal heredado solo existe si su superficie esta habilitada y
            # su base es la legacy; el resto se cierra antes de aceptar.
            if (
                settings.legacy_surface_enabled
                and superficie_de_ruta(ruta) == SURFACE_LEGACY
            ):
                await self.app(scope, receive, send)
                return
            await send({"type": "websocket.close", "code": 1008})
            return

        if scope["type"] != "http":
            # Eventos internos lifespan/asgi nunca son datos de un tenant.
            await self.app(scope, receive, send)
            return

        metodo = scope.get("method", "")
        if _ruta_saas_permitida(metodo, ruta):
            await self.app(scope, receive, send)
            return
        if ruta in _RUTAS_SIEMPRE_BLOQUEADAS_EN_SAAS:
            # La documentación describe también contratos legacy y nunca debe
            # publicarse desde una superficie SaaS, incluso en modo SaaS-only.
            await _denegar(scope, receive, send)
            return
        if (
            settings.legacy_surface_enabled
            and superficie_de_ruta(ruta) == SURFACE_LEGACY
        ):
            await self.app(scope, receive, send)
            return

        await _denegar(scope, receive, send)


async def _denegar(scope: Scope, receive: Receive, send: Send) -> None:
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
