"""Escrituras SaaS de catálogo; empresa obligatoria y sin DDL.

Los métodos no aceptan empresa desde la petición: reciben empresa_id
validado por FastAPI. Cada padre se verifica dentro del tenant y los
UPDATE restringen también por empresa_id. Las FK compuestas de PostgreSQL
siguen siendo la última defensa ante carreras concurrentes.
"""
from datetime import datetime, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Bitacora
from app.modules.catalogo.models.models import (
    Categoria, Color, Producto, Talla, VarianteProducto,
)
from app.modules.catalogo.repositories.tenant_repository import TenantCatalogoRepository


class TenantCatalogoWriteRepository:
    @staticmethod
    def _tenant(empresa_id: int) -> int:
        return TenantCatalogoRepository._empresa_valida(empresa_id)

    @staticmethod
    def _id_activo(db: Session, tabla, empresa_id: int | None, id_: int) -> bool:
        criterios = [tabla.c.id == id_, tabla.c.estado.is_(True)]
        if empresa_id is not None:
            criterios.append(tabla.c.empresa_id == empresa_id)
        return db.scalar(
            select(tabla.c.id).where(*criterios).with_for_update()
        ) is not None

    @classmethod
    def categoria_activa(cls, db: Session, *, empresa_id: int, categoria_id: int) -> bool:
        return cls._id_activo(db, Categoria.__table__, cls._tenant(empresa_id), categoria_id)

    @classmethod
    def producto_activo(cls, db: Session, *, empresa_id: int, producto_id: int) -> bool:
        return cls._id_activo(db, Producto.__table__, cls._tenant(empresa_id), producto_id)

    @classmethod
    def variante_activa(cls, db: Session, *, empresa_id: int, variante_id: int) -> bool:
        return cls._id_activo(db, VarianteProducto.__table__, cls._tenant(empresa_id), variante_id)

    @classmethod
    def catalogos_globales_activos(cls, db: Session, *, talla_id: int, color_id: int) -> bool:
        return (
            cls._id_activo(db, Talla.__table__, None, talla_id)
            and cls._id_activo(db, Color.__table__, None, color_id)
        )

    @classmethod
    def _auditar_categoria(
        cls, db: Session, *, empresa_id: int, usuario_id: int,
        accion: str, categoria_id: int
    ) -> None:
        # El primer parcial no tiene trigger para categoria.
        # Se registra en la MISMA transacción que el INSERT/UPDATE comercial.
        db.execute(insert(Bitacora.__table__).values(
            empresa_id=empresa_id,
            usuario_id=usuario_id,
            fecha_hora=datetime.now(timezone.utc),
            accion=accion,
            entidad_afectada="categoria",
            descripcion=f"Catalogo SaaS: categoria {categoria_id}",
        ))

    @classmethod
    def crear_categoria(
        cls, db: Session, *, empresa_id: int, usuario_id: int,
        nombre: str, descripcion: str | None
    ) -> int:
        tenant_id = cls._tenant(empresa_id)
        categoria_id = db.scalar(
            insert(Categoria.__table__).values(
                empresa_id=tenant_id,
                nombre=nombre,
                descripcion=descripcion,
                estado=True,
            ).returning(Categoria.__table__.c.id)
        )
        cls._auditar_categoria(
            db, empresa_id=tenant_id, usuario_id=usuario_id,
            accion="CREAR", categoria_id=categoria_id
        )
        return categoria_id

    @classmethod
    def actualizar_categoria(
        cls, db: Session, *, empresa_id: int, usuario_id: int,
        categoria_id: int, cambios: dict
    ) -> bool:
        tenant_id = cls._tenant(empresa_id)
        if not cls.categoria_activa(db, empresa_id=tenant_id, categoria_id=categoria_id):
            return False
        actualizado = db.scalar(
            update(Categoria.__table__)
            .where(
                Categoria.__table__.c.id == categoria_id,
                Categoria.__table__.c.empresa_id == tenant_id,
                Categoria.__table__.c.estado.is_(True),
            )
            .values(**cambios).returning(Categoria.__table__.c.id)
        )
        if actualizado is None:
            return False
        cls._auditar_categoria(
            db, empresa_id=tenant_id, usuario_id=usuario_id,
            accion="MODIFICAR", categoria_id=categoria_id,
        )
        return True

    @classmethod
    def crear_producto(
        cls, db: Session, *, empresa_id: int, categoria_id: int,
        nombre: str, descripcion: str | None, precio
    ) -> int | None:
        tenant_id = cls._tenant(empresa_id)
        if not cls.categoria_activa(
            db, empresa_id=tenant_id, categoria_id=categoria_id
        ):
            return None
        return db.scalar(
            insert(Producto.__table__).values(
                empresa_id=tenant_id, categoria_id=categoria_id,
                nombre=nombre, descripcion=descripcion, precio=precio,
                estado=True,
            ).returning(Producto.__table__.c.id)
        )

    @classmethod
    def actualizar_producto(
        cls, db: Session, *, empresa_id: int, producto_id: int, cambios: dict
    ) -> bool:
        tenant_id = cls._tenant(empresa_id)
        if not cls.producto_activo(db, empresa_id=tenant_id, producto_id=producto_id):
            return False
        if "categoria_id" in cambios and not cls.categoria_activa(
            db, empresa_id=tenant_id, categoria_id=cambios["categoria_id"]
        ):
            return False
        return db.scalar(
            update(Producto.__table__).where(
                Producto.__table__.c.empresa_id == tenant_id,
                Producto.__table__.c.id == producto_id,
                Producto.__table__.c.estado.is_(True),
            ).values(**cambios).returning(Producto.__table__.c.id)
        ) is not None

    @classmethod
    def crear_variante(
        cls, db: Session, *, empresa_id: int, producto_id: int,
        talla_id: int, color_id: int, sku: str
    ) -> int | None:
        tenant_id = cls._tenant(empresa_id)
        if not cls.producto_activo(
            db, empresa_id=tenant_id, producto_id=producto_id
        ) or not cls.catalogos_globales_activos(db, talla_id=talla_id, color_id=color_id):
            return None
        return db.scalar(
            insert(VarianteProducto.__table__).values(
                empresa_id=tenant_id,
                producto_id=producto_id, talla_id=talla_id,
                color_id=color_id, sku=sku, estado=True,
            ).returning(VarianteProducto.__table__.c.id)
        )

    @classmethod
    def actualizar_variante(
        cls, db: Session, *, empresa_id: int, variante_id: int, cambios: dict
    ) -> bool:
        tenant_id = cls._tenant(empresa_id)
        if not cls.variante_activa(db, empresa_id=tenant_id, variante_id=variante_id):
            return False
        if "talla_id" in cambios or "color_id" in cambios:
            v = VarianteProducto.__table__
            actual = db.execute(
                select(v.c.talla_id, v.c.color_id).where(
                    v.c.empresa_id == tenant_id, v.c.id == variante_id,
                )
            ).mappings().one_or_none()
            if actual is None or not cls.catalogos_globales_activos(
                db,
                talla_id=cambios.get("talla_id", actual["talla_id"]),
                color_id=cambios.get("color_id", actual["color_id"]),
            ):
                return False
        v = VarianteProducto.__table__
        return db.scalar(
            update(v).where(
                v.c.id == variante_id, v.c.empresa_id == tenant_id,
                v.c.estado.is_(True),
            ).values(**cambios).returning(v.c.id)
        ) is not None
