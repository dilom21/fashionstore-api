from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.modules.autenticacion_seguridad.models.models import Bitacora, Empleado
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Ciudad, Sucursal


class SucursalRepository:
    """Metodos CU06 para sucursales. Los metodos usados por CU03 se conservan."""

    @staticmethod
    def listar_activas(db: Session) -> list[Sucursal]:
        statement = (
            select(Sucursal)
            .options(joinedload(Sucursal.ciudad))
            .where(Sucursal.estado.is_(True))
            .order_by(Sucursal.nombre)
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_activa_por_id(db: Session, sucursal_id: int) -> Sucursal | None:
        statement = (
            select(Sucursal)
            .options(joinedload(Sucursal.ciudad))
            .where(Sucursal.id == sucursal_id, Sucursal.estado.is_(True))
        )
        return db.scalar(statement)

    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None = None,
        ciudad_id: int | None = None,
        estado: bool | None = None,
    ) -> list[Sucursal]:
        statement = select(Sucursal).options(joinedload(Sucursal.ciudad))
        if buscar:
            patron = f"%{buscar.strip()}%"
            statement = statement.where(
                or_(
                    Sucursal.nombre.ilike(patron),
                    Sucursal.direccion.ilike(patron),
                    Sucursal.ciudad.has(Ciudad.nombre.ilike(patron)),
                )
            )
        if ciudad_id is not None:
            statement = statement.where(Sucursal.ciudad_id == ciudad_id)
        if estado is not None:
            statement = statement.where(Sucursal.estado.is_(estado))
        statement = statement.order_by(Sucursal.nombre, Sucursal.id)
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, sucursal_id: int) -> Sucursal | None:
        statement = (
            select(Sucursal)
            .options(joinedload(Sucursal.ciudad))
            .where(Sucursal.id == sucursal_id)
        )
        return db.scalar(statement)

    @staticmethod
    def buscar_duplicada(
        db: Session,
        *,
        nombre: str,
        ciudad_id: int,
        excluir_id: int | None = None,
    ) -> Sucursal | None:
        """Sucursal con el mismo nombre normalizado dentro de la misma ciudad."""
        statement = select(Sucursal).where(
            Sucursal.ciudad_id == ciudad_id,
            func.lower(Sucursal.nombre) == nombre.strip().lower(),
        )
        if excluir_id is not None:
            statement = statement.where(Sucursal.id != excluir_id)
        return db.scalar(statement)

    @staticmethod
    def crear(
        db: Session,
        *,
        ciudad_id: int,
        nombre: str,
        direccion: str,
        telefono: str | None = None,
    ) -> Sucursal:
        sucursal = Sucursal(
            ciudad_id=ciudad_id,
            nombre=nombre,
            direccion=direccion,
            telefono=telefono,
            estado=True,
        )
        db.add(sucursal)
        db.flush()
        return sucursal

    @staticmethod
    def contar_empleados_activos(db: Session, sucursal_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(Empleado)
            .where(Empleado.sucursal_id == sucursal_id, Empleado.estado.is_(True))
        )
        return int(db.scalar(statement) or 0)

    @staticmethod
    def contar_inventario_pendiente(db: Session, sucursal_id: int) -> int:
        """Registros de inventario con stock pendiente (real, sin inventar estados)."""
        statement = (
            select(func.count())
            .select_from(Inventario)
            .where(
                Inventario.sucursal_id == sucursal_id,
                or_(
                    Inventario.stock_actual > 0,
                    Inventario.stock_reservado > 0,
                ),
            )
        )
        return int(db.scalar(statement) or 0)


class CiudadRepository:
    """Acceso a la tabla ciudad para CU06."""

    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None = None,
        estado: bool | None = None,
    ) -> list[Ciudad]:
        statement = select(Ciudad)
        if buscar:
            statement = statement.where(
                Ciudad.nombre.ilike(f"%{buscar.strip()}%")
            )
        if estado is not None:
            statement = statement.where(Ciudad.estado.is_(estado))
        statement = statement.order_by(Ciudad.nombre, Ciudad.id)
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, ciudad_id: int) -> Ciudad | None:
        return db.get(Ciudad, ciudad_id)

    @staticmethod
    def buscar_por_nombre_normalizado(
        db: Session, nombre: str
    ) -> Ciudad | None:
        statement = select(Ciudad).where(
            func.lower(Ciudad.nombre) == nombre.strip().lower()
        )
        return db.scalar(statement)

    @staticmethod
    def crear(db: Session, *, nombre: str) -> Ciudad:
        ciudad = Ciudad(nombre=nombre, estado=True)
        db.add(ciudad)
        db.flush()
        return ciudad

    @staticmethod
    def contar_sucursales_activas(db: Session, ciudad_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(Sucursal)
            .where(Sucursal.ciudad_id == ciudad_id, Sucursal.estado.is_(True))
        )
        return int(db.scalar(statement) or 0)

    @staticmethod
    def agrupar_conteo_sucursales(
        db: Session,
    ) -> dict[int, dict[str, int]]:
        """Conteo (total, activas) de sucursales por ciudad en una sola query."""
        statement = (
            select(
                Sucursal.ciudad_id,
                func.count(),
                func.count().filter(Sucursal.estado.is_(True)),
            )
            .group_by(Sucursal.ciudad_id)
        )
        filas = db.execute(statement).all()
        return {
            ciudad_id: {
                "sucursales_total": int(total),
                "sucursales_activas": int(activas),
            }
            for ciudad_id, total, activas in filas
        }

    @staticmethod
    def registrar_evento_bitacora(
        db: Session,
        *,
        usuario_id: int,
        accion: str,
        entidad_afectada: str,
        descripcion: str,
    ) -> None:
        """Escritura manual en bitacora para ciudad.

        La tabla ciudad no posee trigger de auditoria a diferencia de
        sucursal, por lo que el evento se registra explicitamente.
        """
        db.add(
            Bitacora(
                usuario_id=usuario_id,
                fecha_hora=datetime.now(timezone.utc),
                accion=accion,
                entidad_afectada=entidad_afectada,
                descripcion=descripcion,
            )
        )
        db.flush()
