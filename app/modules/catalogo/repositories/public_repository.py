"""Acceso al catalogo publico exclusivamente mediante la proyeccion SQL CU10."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session


class PublicCatalogoRepository:
    """Repositorio sin acceso directo a tablas del catalogo.

    La cuenta usada por este pool solo necesita EXECUTE sobre las funciones de
    ``fashionstore_private``. Los argumentos de ``func`` se convierten en
    parametros enlazados por SQLAlchemy; nunca se concatena SQL ni filtros.
    """

    @staticmethod
    def _filas(resultado) -> list[dict]:
        return [fila[0] for fila in resultado if fila[0] is not None]

    @classmethod
    def listar_productos(
        cls,
        db: Session,
        *,
        empresa_slug: str,
        buscar: str | None = None,
        categoria_id: int | None = None,
        talla_id: int | None = None,
        color_id: int | None = None,
        temporada_id: int | None = None,
        coleccion_id: int | None = None,
        sucursal_id: int | None = None,
        disponibilidad: str = "TODOS",
        limit: int = 24,
        offset: int = 0,
    ):
        sentencia = select(
            func.fashionstore_private.catalogo_publico_filtrado(
                empresa_slug,
                None,
                buscar,
                categoria_id,
                talla_id,
                color_id,
                temporada_id,
                coleccion_id,
                sucursal_id,
                disponibilidad,
                limit,
                offset,
            )
        )
        return cls._filas(db.execute(sentencia))

    @classmethod
    def detalle_producto(cls, db: Session, *, empresa_slug: str, producto_id: int):
        sentencia = select(
            func.fashionstore_private.catalogo_publico_detalle(
                empresa_slug, producto_id
            )
        )
        return db.scalar(sentencia)

    @classmethod
    def filtros(cls, db: Session, *, empresa_slug: str):
        sentencia = select(
            func.fashionstore_private.catalogo_publico_filtros(empresa_slug)
        )
        return db.scalar(sentencia) or {
            "categorias": [],
            "tallas": [],
            "colores": [],
            "temporadas": [],
            "colecciones": [],
            "sucursales": [],
        }
