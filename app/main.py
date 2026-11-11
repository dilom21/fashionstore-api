from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.modules.asistencia_inteligente.api.router import (
    router as asistencia_inteligente_router,
)
from app.modules.vestidor_virtual.api.router import (
    router as vestidor_virtual_router,
)
from app.core.database import engine
from app.core.saas_route_gate import BloqueoRutasLegacySaaS
from app.modules.autenticacion_seguridad.api.router import router as auth_router
from app.modules.autenticacion_seguridad.api.tenant_router import router as tenant_auth_router
from app.modules.catalogo.api.tenant_router import router as tenant_catalogo_router
from app.modules.catalogo.api.tenant_write_router import router as tenant_catalogo_write_router
from app.modules.catalogo.api.tenant_merchandising_router import router as tenant_merchandising_router
from app.modules.catalogo.api.public_router import router as public_catalogo_router
from app.modules.bitacora.api.router import router as bitacora_router
from app.modules.bitacora.api.tenant_router import router as tenant_bitacora_router
from app.modules.carrito.api.router import router as carrito_router
from app.modules.carrito.api.tenant_router import router as tenant_carrito_router
from app.modules.carrito.api.tenant_read_router import router as tenant_carrito_read_router
from app.modules.reservas.api.tenant_prevalidacion_router import router as tenant_reserva_prevalidacion_router
from app.modules.reservas.api.tenant_reserva_write_router import router as tenant_reserva_write_router
from app.modules.reservas.api.tenant_gestion_router import router as tenant_reserva_gestion_router
from app.modules.reservas.api.tenant_atencion_router import router as tenant_reserva_atencion_router
from app.modules.ventas.api.tenant_presencial_router import router as tenant_ventas_presencial_router
from app.modules.pagos.api.tenant_router import router as tenant_pagos_router
from app.modules.sucursales.api.tenant_saas_admin_router import router as tenant_empresa_admin_router
from app.modules.sucursales.api.saas_superadmin_router import router as saas_superadmin_router, auth_router as saas_superadmin_auth_router
from app.modules.catalogo.api.router import router as catalogo_router
from app.modules.compras.api.router import router as compras_router
from app.modules.devoluciones.api.router import router as devoluciones_router
from app.modules.devoluciones.api.tenant_cu15_router import router as tenant_cu15_devoluciones_router
from app.modules.inventario.api.router import router as inventario_router
from app.modules.inventario.api.tenant_router import router as tenant_inventario_router
from app.modules.pagos.api.router import router as pagos_router
from app.modules.promociones.api.router import router as promociones_router
from app.modules.proveedores.api.router import router as proveedores_router
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
from app.modules.offline.router import router as tenant_offline_router

app = FastAPI(title="FashionStore API", version="1.0.0")

@app.middleware("http")
async def no_cache_platform_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/saas/superadmin"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response

# Bloque 3 (paso 1): en modo SaaS no se pueden utilizar endpoints heredados
# que aun consultan sin empresa_id. Se conserva el contrato legacy cuando el
# feature flag esta desactivado; las rutas nuevas /auth/tenant permanecen.
# Este guard se registra antes de CORS para preservar headers de CORS.
app.add_middleware(BloqueoRutasLegacySaaS)

# CORS habilitado unicamente para el frontend Angular en desarrollo.
# La consola de plataforma usa cookie HttpOnly + CSRF; el tenant sigue usando JWT.
cors_origins = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
    "https://fashionstore-web-taupe.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(catalogo_router)
app.include_router(asistencia_inteligente_router)
app.include_router(sucursales_router)
app.include_router(inventario_router)
app.include_router(tenant_inventario_router)
app.include_router(auth_router)
# Modo SaaS apagado por defecto; rutas nuevas devuelven 503 hasta migrar DB.
app.include_router(tenant_auth_router)
# Lecturas de catalogo con empresa_id obligatorio desde el JWT.
app.include_router(tenant_catalogo_router)
app.include_router(tenant_catalogo_write_router)
app.include_router(tenant_merchandising_router)
app.include_router(public_catalogo_router)
# Consulta comercial: admin tenant, GET y detalle; sin escrituras de negocio.
app.include_router(tenant_commercial_router)
# CU15 SaaS: primer POST transaccional para carrito de un cliente del tenant.
app.include_router(tenant_carrito_router)
app.include_router(tenant_carrito_read_router)
# CU16 SaaS: solo prevalidacion de stock/propietario, sin crear reserva.
app.include_router(tenant_reserva_prevalidacion_router)
# CU16: creación registrada pero denegada por segundo flag hasta SP y RLS real.
app.include_router(tenant_reserva_write_router)
# CU12 SaaS: gestion/atencion de reservas con empresa y sucursal efectiva.
app.include_router(tenant_reserva_gestion_router)
app.include_router(tenant_reserva_atencion_router)
# CU13 SaaS: venta presencial (PENDIENTE) y pago/confirmacion transaccional.
app.include_router(tenant_ventas_presencial_router)
app.include_router(tenant_pagos_router)
# Empresa SaaS: administracion de plan y sucursales con aislamiento tenant.
app.include_router(tenant_empresa_admin_router)
# CU01/CU02: superadministración global, propia credencial y flags OFF.
app.include_router(saas_superadmin_router)
app.include_router(saas_superadmin_auth_router)
# CU04 SaaS: solo rutas empresariales con admin/JWT y membresía propia.
app.include_router(tenant_miembros_router)
app.include_router(bitacora_router)
app.include_router(tenant_bitacora_router)
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
app.include_router(tenant_cu15_devoluciones_router)
app.include_router(pagos_router)
app.include_router(reportes_router)
app.include_router(tenant_reportes_router)
# CU19/CU20: ledger offline; flag independiente y migracion 106 previa.
app.include_router(tenant_offline_router)
app.include_router(vestidor_virtual_router)


@app.get("/")
def root():
    return {"message": "FashionStore API funcionando"}


@app.get("/health")
def health():
    return {"status": "ok"}


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
