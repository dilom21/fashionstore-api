from fastapi import FastAPI
from sqlalchemy import text

from app.core.database import engine
from app.routers.auth import router as auth_router
from app.routers.categorias import router as categorias_router
from app.routers.inventario import router as inventario_router
from app.routers.productos import router as productos_router
from app.routers.sucursales import router as sucursales_router

app = FastAPI(title="FashionStore API", version="1.0.0")

app.include_router(productos_router)
app.include_router(categorias_router)
app.include_router(sucursales_router)
app.include_router(inventario_router)
app.include_router(auth_router)


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
