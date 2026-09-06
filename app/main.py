from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.database import engine
from app.modules.autenticacion_seguridad.api.router import router as auth_router
from app.modules.bitacora.api.router import router as bitacora_router
from app.modules.catalogo.api.router import router as catalogo_router
from app.modules.inventario.api.router import router as inventario_router
from app.modules.roles.api.router import router as roles_router
from app.modules.sucursales.api.router import router as sucursales_router
from app.modules.usuarios.api.router import router as usuarios_router

app = FastAPI(title="FashionStore API", version="1.0.0")

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
app.include_router(sucursales_router)
app.include_router(inventario_router)
app.include_router(auth_router)
app.include_router(bitacora_router)
app.include_router(roles_router)
app.include_router(usuarios_router)


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
