"""Escrituras SaaS de catálogo; empresa obligatoria y sin DDL.

Los métodos no aceptan empresa desde la petición: reciben empresa_id
validado por FastAPI. Cada padre se verifica dentro del tenant y los
UPDATE restringen también por empresa_id. Las FK compuestas de PostgreSQL
siguen siendo la última defensa ante carreras concurrentes.

Bloqueo de catálogos globales (E2/5BN)
--------------------------------------
`talla` y `color` son diccionarios globales y el principal restringido solo
tiene SELECT sobre ellos. En PostgreSQL 18 **cualquier** cláusula de bloqueo de
fila (`FOR UPDATE`, `FOR NO KEY UPDATE`, `FOR SHARE`, `FOR KEY SHARE`) exige
privilegio UPDATE, comprobado empíricamente en el laboratorio: el intento con
`FOR SHARE` también devuelve 42501. Por eso el bloqueo exclusivo que producía el
HTTP 500 no se sustituye por otro bloqueo, sino por:

* lectura normal (sin bloqueo) de la talla y el color — cero privilegios nuevos;
* el invariante "la variante no puede referenciar una talla/color inactiva" se
  refuerza en la base con el trigger `trg_5bn_validar_catalogos_globales`
  (migración 5BN), que valida en el mismo INSERT/UPDATE y cubre a cualquier
  escritor, no solo a este repositorio.

Semántica de concurrencia declarada: ninguna ruta tenant desactiva `talla` ni
`color` (solo el plano global/SQL). Si una desactivación de plataforma se
confirma entre la lectura y el INSERT, la sentencia falla con 23514 (HTTP 409)
en vez de crear una variante inválida, y la respuesta se mantiene neutra.
"""
from datetime import datetime, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.core.bitacora_trigger import bitacora_categoria_por_trigger
from app.modules.autenticacion_seguridad.models.models import Bitacora
from app.modules.catalogo.models.models import (
    Categoria, Color, Producto, RecursoProducto, Talla, VarianteProducto,
)
from app.modules.catalogo.repositories.tenant_repository import TenantCatalogoRepository


class TenantCatalogoWriteRepository:
    @staticmethod
    def _tenant(empresa_id: int) -> int:
        return TenantCatalogoRepository._empresa_valida(empresa_id)

    @staticmethod
    def _id_activo(
        db: Session, tabla, empresa_id: int | None, id_: int,
        *, con_bloqueo: bool = True,
    ) -> bool:
        criterios = [tabla.c.id == id_, tabla.c.estado.is_(True)]
        if empresa_id is not None:
            criterios.append(tabla.c.empresa_id == empresa_id)
        consulta = select(tabla.c.id).where(*criterios)
        if con_bloqueo:
            consulta = consulta.with_for_update()
        return db.scalar(consulta) is not None

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
        # Diccionarios GLOBALES: el runtime solo tiene SELECT y PostgreSQL exige
        # UPDATE para cualquier bloqueo de fila. Se lee sin bloquear; el
        # invariante lo refuerza el trigger trg_5bn_validar_catalogos_globales.
        return (
            cls._id_activo(db, Talla.__table__, None, talla_id, con_bloqueo=False)
            and cls._id_activo(db, Color.__table__, None, color_id, con_bloqueo=False)
        )

    @classmethod
    def _auditar_categoria(
        cls, db: Session, *, empresa_id: int, usuario_id: int,
        accion: str, categoria_id: int
    ) -> None:
        # El primer parcial no tiene trigger para categoria.
        # Se registra en la MISMA transacción que el INSERT/UPDATE comercial.
        # 5CA: con RLS+ACL activos (050a-c) fashionstore_api NO tiene INSERT
        # sobre bitacora; la auditoría la escribe fn_registrar_bitacora() con un
        # owner NOLOGIN. El flag es explícito y apagado por defecto: mientras la
        # migración no esté aplicada se conserva el insert de este método.
        # 5CB: 040 instala trg_bitacora_categoria. Con el trigger presente el
        # evento ya lo escribe PostgreSQL, así que insertarlo aquí lo duplicaría
        # (y con 050c fallaría por ACL); la sonda decide en tiempo de ejecución.
        if bitacora_categoria_por_trigger(db):
            return
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

    @classmethod
    def crear_recurso(
        cls, db: Session, *, empresa_id: int, producto_id: int, color_id: int | None,
        tipo: str, url: str, es_principal: bool,
    ) -> int | None:
        tenant_id = cls._tenant(empresa_id)
        if not cls.producto_activo(db, empresa_id=tenant_id, producto_id=producto_id):
            return None
        if color_id is not None and not cls._id_activo(
            db, Color.__table__, None, color_id, con_bloqueo=False
        ):
            return None
        r = RecursoProducto.__table__
        if es_principal:
            db.execute(update(r).where(
                r.c.empresa_id == tenant_id, r.c.producto_id == producto_id,
                r.c.estado.is_(True),
                r.c.color_id.is_(color_id) if color_id is not None else r.c.color_id.is_(None),
            ).values(es_principal=False))
        return db.scalar(insert(r).values(
            empresa_id=tenant_id, producto_id=producto_id, color_id=color_id,
            tipo=tipo, url=url, es_principal=es_principal, estado=True,
        ).returning(r.c.id))

    @classmethod
    def actualizar_recurso(
        cls, db: Session, *, empresa_id: int, recurso_id: int, cambios: dict,
    ) -> bool:
        tenant_id = cls._tenant(empresa_id)
        r = RecursoProducto.__table__
        actual = db.execute(select(r.c.producto_id, r.c.color_id).where(
            r.c.id == recurso_id, r.c.empresa_id == tenant_id, r.c.estado.is_(True),
        )).mappings().one_or_none()
        if actual is None:
            return False
        if "color_id" in cambios and cambios["color_id"] is not None and not cls._id_activo(
            db, Color.__table__, None, cambios["color_id"], con_bloqueo=False
        ):
            return False
        if cambios.get("es_principal"):
            color_id = cambios.get("color_id", actual["color_id"])
            db.execute(update(r).where(
                r.c.empresa_id == tenant_id, r.c.producto_id == actual["producto_id"],
                r.c.estado.is_(True),
                r.c.color_id.is_(color_id) if color_id is not None else r.c.color_id.is_(None),
            ).values(es_principal=False))
        return db.scalar(update(r).where(
            r.c.id == recurso_id, r.c.empresa_id == tenant_id, r.c.estado.is_(True),
        ).values(**cambios).returning(r.c.id)) is not None

    @classmethod
    def cambiar_estado_recurso(
        cls, db: Session, *, empresa_id: int, recurso_id: int, estado: bool,
    ) -> bool:
        tenant_id = cls._tenant(empresa_id)
        r = RecursoProducto.__table__
        return db.scalar(update(r).where(
            r.c.id == recurso_id, r.c.empresa_id == tenant_id,
        ).values(estado=estado).returning(r.c.id)) is not None

    @classmethod
    def marcar_recurso_principal(
        cls, db: Session, *, empresa_id: int, recurso_id: int,
    ) -> bool:
        tenant_id = cls._tenant(empresa_id)
        r = RecursoProducto.__table__
        actual = db.execute(select(r.c.producto_id, r.c.color_id).where(
            r.c.id == recurso_id, r.c.empresa_id == tenant_id, r.c.estado.is_(True),
        )).mappings().one_or_none()
        if actual is None:
            return False
        filtro_color = r.c.color_id.is_(actual["color_id"]) if actual["color_id"] is None else r.c.color_id == actual["color_id"]
        db.execute(update(r).where(
            r.c.empresa_id == tenant_id, r.c.producto_id == actual["producto_id"],
            r.c.estado.is_(True), filtro_color,
        ).values(es_principal=False))
        return db.scalar(update(r).where(
            r.c.id == recurso_id, r.c.empresa_id == tenant_id,
        ).values(es_principal=True).returning(r.c.id)) is not None

    @classmethod
    def cambiar_estado_categoria(cls, db: Session, *, empresa_id: int, categoria_id: int, estado: bool) -> bool:
        tenant_id = cls._tenant(empresa_id)
        if not estado and db.scalar(select(Producto.__table__.c.id).where(
            Producto.__table__.c.empresa_id == tenant_id,
            Producto.__table__.c.categoria_id == categoria_id,
            Producto.__table__.c.estado.is_(True),
        ).limit(1)) is not None:
            return False
        return db.scalar(update(Categoria.__table__).where(
            Categoria.__table__.c.id == categoria_id,
            Categoria.__table__.c.empresa_id == tenant_id,
        ).values(estado=estado).returning(Categoria.__table__.c.id)) is not None

    @classmethod
    def cambiar_estado_producto(cls, db: Session, *, empresa_id: int, producto_id: int, estado: bool) -> bool:
        tenant_id = cls._tenant(empresa_id)
        return db.scalar(update(Producto.__table__).where(
            Producto.__table__.c.id == producto_id,
            Producto.__table__.c.empresa_id == tenant_id,
        ).values(estado=estado).returning(Producto.__table__.c.id)) is not None

    @classmethod
    def cambiar_estado_variante(cls, db: Session, *, empresa_id: int, variante_id: int, estado: bool) -> bool:
        tenant_id = cls._tenant(empresa_id)
        if estado:
            producto_id = db.scalar(select(VarianteProducto.__table__.c.producto_id).where(
                VarianteProducto.__table__.c.id == variante_id,
                VarianteProducto.__table__.c.empresa_id == tenant_id,
            ))
            if producto_id is None or not cls.producto_activo(
                db, empresa_id=tenant_id, producto_id=producto_id
            ):
                return False
        return db.scalar(update(VarianteProducto.__table__).where(
            VarianteProducto.__table__.c.id == variante_id,
            VarianteProducto.__table__.c.empresa_id == tenant_id,
        ).values(estado=estado).returning(VarianteProducto.__table__.c.id)) is not None
