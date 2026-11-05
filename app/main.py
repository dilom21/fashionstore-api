from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.modules.asistencia_inteligente.api.router import (
    router as asistencia_inteligente_router,
)
from app.modules.asistencia_inteligente.api.tenant_router import (
    router as tenant_asistencia_inteligente_router,
)
from app.modules.vestidor_virtual.api.router import (
    router as vestidor_virtual_router,
)
from app.modules.vestidor_virtual.api.tenant_router import (
    router as tenant_vestidor_virtual_router,
)
from app.core.database import engine
from app.core.saas_route_gate import BloqueoRutasLegacySaaS
from app.core.surface_middleware import ResolucionSuperficie
from app.core.saas_superadmin_guard import GuardiaSuperadminSaaS
from app.modules.autenticacion_seguridad.api.router import router as auth_router
from app.modules.autenticacion_seguridad.api.tenant_router import router as tenant_auth_router
from app.modules.catalogo.api.tenant_router import router as tenant_catalogo_router
from app.modules.catalogo.api.tenant_write_router import router as tenant_catalogo_write_router
from app.modules.catalogo.api.tenant_public_router import router as tenant_public_router
from app.modules.catalogo.api.tenant_merchandising_router import router as tenant_merchandising_router
from app.modules.bitacora.api.router import router as bitacora_router
from app.modules.bitacora.api.tenant_router import router as tenant_bitacora_router
from app.modules.carrito.api.router import router as carrito_router
from app.modules.carrito.api.tenant_router import router as tenant_carrito_router
from app.modules.carrito.api.tenant_read_router import router as tenant_carrito_read_router
from app.modules.reservas.api.tenant_prevalidacion_router import router as tenant_reserva_prevalidacion_router
from app.modules.reservas.api.tenant_reserva_write_router import router as tenant_reserva_write_router
from app.modules.sucursales.api.tenant_saas_admin_router import router as tenant_empresa_admin_router
from app.modules.sucursales.api.saas_superadmin_router import router as saas_superadmin_router
from app.modules.sucursales.api.saas_superadmin_seguridad_router import (
    router as saas_superadmin_seguridad_router,
)
from app.modules.catalogo.api.router import router as catalogo_router
from app.modules.compras.api.router import router as compras_router
from app.modules.compras.api.tenant_orden_compra_router import router as tenant_orden_compra_router
from app.modules.devoluciones.api.router import router as devoluciones_router
from app.modules.devoluciones.api.tenant_devolucion_router import router as tenant_devolucion_router
from app.modules.inventario.api.router import router as inventario_router
from app.modules.inventario.api.tenant_inventario_router import (
    router as tenant_inventario_router,
)
from app.modules.inventario.api.tenant_movimientos_router import (
    router as tenant_movimientos_router,
)
from app.modules.pagos.api.router import router as pagos_router
from app.modules.promociones.api.router import router as promociones_router
from app.modules.proveedores.api.router import router as proveedores_router
from app.modules.proveedores.api.tenant_proveedor_router import (
    router as tenant_proveedor_router,
)
from app.modules.reportes.api.router import router as reportes_router
from app.modules.reportes.api.tenant_router import router as tenant_reportes_router
from app.modules.reservas.api.atencion_router import (
    router as atencion_reservas_router,
)
from app.modules.reservas.api.gestion_router import (
    router as reservas_sucursal_router,
)
from app.modules.reservas.api.router import router as reservas_router
from app.modules.roles.api.router import router as roles_router
from app.modules.sucursales.api.router import router as sucursales_router
from app.modules.temporadas_colecciones.api.router import (
    router as temporadas_colecciones_router,
)
from app.modules.usuarios.api.router import router as usuarios_router
from app.modules.usuarios.api.tenant_membership_router import router as tenant_miembros_router
from app.modules.ventas.api.router import router as ventas_router
from app.modules.ventas.api.tenant_commercial_router import router as tenant_commercial_router
from app.modules.ventas.api.tenant_cliente_router import router as tenant_cliente_router
from app.modules.ventas.api.tenant_venta_contado_router import router as tenant_venta_contado_router

app = FastAPI(title="FashionStore API", version="1.0.0")

# Bloque 3 (paso 1): en modo SaaS no se pueden utilizar endpoints heredados
# que aun consultan sin empresa_id. Se conserva el contrato legacy cuando el
# feature flag esta desactivado; las rutas nuevas /auth/tenant permanecen.
# Este guard se registra antes de CORS para preservar headers de CORS.
app.add_middleware(BloqueoRutasLegacySaaS)

# 5C1: decide contra que base corre la peticion (legacy o tenant) antes del
# gate. Se registra despues del gate para quedar por fuera de el.
app.add_middleware(ResolucionSuperficie)

# 5BO: rate limit compartido (PostgreSQL) y auditoria del plano superadmin.
# Se registra antes de CORS para que CORS quede por fuera y las respuestas
# 429/403 conserven sus encabezados.
app.add_middleware(GuardiaSuperadminSaaS)

# CORS habilitado unicamente para el frontend Angular en desarrollo.
# La autenticacion usa JWT por header Authorization (sin cookies de sesion),
# por lo que allow_credentials se mantiene en False.
cors_origins = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
    "https://fashionstore-web-taupe.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(catalogo_router)
app.include_router(asistencia_inteligente_router)
app.include_router(sucursales_router)
app.include_router(inventario_router)
app.include_router(auth_router)
# Modo SaaS apagado por defecto; rutas nuevas devuelven 503 hasta migrar DB.
app.include_router(tenant_auth_router)
# Lecturas de catalogo con empresa_id obligatorio desde el JWT.
app.include_router(tenant_catalogo_router)
app.include_router(tenant_catalogo_write_router)
# T05: storefront by validated active slug and tenant merchandising admin.
app.include_router(tenant_public_router)
app.include_router(tenant_merchandising_router)
# T05/T06: client-owned fitting room, intelligent assistance and reports.
app.include_router(tenant_vestidor_virtual_router)
app.include_router(tenant_asistencia_inteligente_router)
app.include_router(tenant_reportes_router)
# 5BZ: inventario tenant por sucursal (solo GET, permiso VENTAS/CONSULTAR).
app.include_router(tenant_inventario_router)
# T03: auditoria y Kardex de la empresa autenticada (solo lectura).
app.include_router(tenant_bitacora_router)
app.include_router(tenant_movimientos_router)
# Consulta comercial: admin tenant, GET y detalle; sin escrituras de negocio.
app.include_router(tenant_commercial_router)
# T02/T04: canal cliente tenant (reservas, checkout, pago e historial).
app.include_router(tenant_cliente_router)
# CU15 SaaS: primer POST transaccional para carrito de un cliente del tenant.
app.include_router(tenant_carrito_router)
app.include_router(tenant_carrito_read_router)
# CU16 SaaS: solo prevalidacion de stock/propietario, sin crear reserva.
app.include_router(tenant_reserva_prevalidacion_router)
# CU16: creación registrada pero denegada por segundo flag hasta SP y RLS real.
app.include_router(tenant_reserva_write_router)
# E1/5BM: venta presencial de contado (flag SAAS_VENTAS_ENABLED, OFF).
app.include_router(tenant_venta_contado_router)
# E2/5BN: compras/recepcion y devoluciones tenant (rutas exactas en el gate).
app.include_router(tenant_orden_compra_router)
app.include_router(tenant_proveedor_router)
app.include_router(tenant_devolucion_router)
# Empresa SaaS: administracion de plan y sucursales con aislamiento tenant.
app.include_router(tenant_empresa_admin_router)
# CU01/CU02: superadministración global, propia credencial y flags OFF.
app.include_router(saas_superadmin_router)
# 5BO: verificacion de rotacion de claves y auditoria global (solo lectura).
app.include_router(saas_superadmin_seguridad_router)
# CU04 SaaS: solo rutas empresariales con admin/JWT y membresía propia.
app.include_router(tenant_miembros_router)
app.include_router(bitacora_router)
app.include_router(roles_router)
app.include_router(usuarios_router)
app.include_router(temporadas_colecciones_router)
app.include_router(promociones_router)
app.include_router(proveedores_router)
app.include_router(compras_router)
app.include_router(carrito_router)
app.include_router(reservas_router)
app.include_router(reservas_sucursal_router)
app.include_router(atencion_reservas_router)
app.include_router(ventas_router)
app.include_router(devoluciones_router)
app.include_router(pagos_router)
app.include_router(reportes_router)
app.include_router(vestidor_virtual_router)


@app.get("/")
def root():
    return {"message": "FashionStore API funcionando"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/superficies")
def health_superficies():
    """5C1: estado de las dos superficies sin exponer DSN ni credenciales.

    Devuelve solo booleanos y el resultado de un ``SELECT 1`` por destino. Se
    usa para verificar el redeploy (``legacy`` y ``tenant`` responden desde su
    propia base) sin abrir la base heredada a traves de ``/health/db``.
    """
    from app.core.config import settings
    from app.core.database import engine_legacy

    def _responde(motor) -> bool:
        if motor is None:
            return False
        try:
            with motor.connect() as conexion:
                conexion.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    separacion = engine_legacy is not None
    return {
        "superficies": {
            "legacy": bool(settings.legacy_surface_enabled),
            "tenant": bool(settings.saas_auth_enabled),
        },
        "datos": {
            # Con separacion fisica cada superficie tiene su propio destino.
            # Sin ella, legacy y tenant comparten el engine tenant.
            "tenant_ok": _responde(engine),
            "legacy_ok": _responde(engine_legacy) if separacion else _responde(engine),
        },
        "separacion_datos": separacion,
    }


@app.get("/health/db")
def health_db():
    try:
        with engine.connect() as connection:
            result = connection.execute(
                text(
                    """
                    SELECT
                        current_database() AS database,
                        current_user AS usuario,
                        COUNT(*) AS total_productos
                    FROM producto;
                    """
                )
            )
            row = result.mappings().one()
            return {
                "status": "ok",
                "database": row["database"],
                "usuario": row["usuario"],
                "total_productos": row["total_productos"],
            }
    except Exception:
        return {"status": "error", "detail": "No fue posible consultar la base de datos"}
