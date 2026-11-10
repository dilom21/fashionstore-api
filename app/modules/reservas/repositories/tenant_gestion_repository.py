"""Acceso a datos tenant para CU12 (gestion y atencion de reservas).

Todas las consultas incluyen ``empresa_id`` obtenido del JWT validado. La RLS
de PostgreSQL sigue siendo la autoridad final; estos filtros son defensa en
profundidad. No se reutilizan repositorios legacy que consultan sin empresa.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.reservas.models.models import DetalleReserva, Reserva
from app.modules.reservas.repositories.repository import ReservaRepository
from app.modules.ventas.models.models import Venta


class ReservaTenantGestionRepository:
    @staticmethod
    def _carga_completa():
        return ReservaRepository._carga_completa() + (
            joinedload(Reserva.cliente),
        )

    @classmethod
    def obtener_por_id(
        cls, db: Session, *, empresa_id: int, reserva_id: int
    ) -> Reserva | None:
        if isinstance(reserva_id, bool) or not isinstance(reserva_id, int) or reserva_id <= 0:
            return None
        statement = (
            select(Reserva)
            .options(*cls._carga_completa())
            .where(Reserva.id == reserva_id, Reserva.empresa_id == empresa_id)
        )
        return db.scalars(statement).unique().first()

    @classmethod
    def _condiciones(
        cls,
        *,
        empresa_id: int,
        sucursal_id: int | None,
        estado: str | None,
        cliente_id: int | None,
        buscar: str | None,
        desde: datetime | None,
        hasta: datetime | None,
    ) -> list:
        condiciones = [Reserva.empresa_id == empresa_id]
        if sucursal_id is not None:
            condiciones.append(Reserva.sucursal_id == sucursal_id)
        if estado is not None:
            condiciones.append(Reserva.estado == estado)
        if cliente_id is not None:
            condiciones.append(Reserva.cliente_id == cliente_id)
        if desde is not None:
            condiciones.append(Reserva.fecha_atencion >= desde)
        if hasta is not None:
            condiciones.append(Reserva.fecha_atencion <= hasta)

        termino = (buscar or "").strip()
        if termino:
            patron = f"%{termino}%"
            clientes = select(Cliente.id).where(
                Cliente.empresa_id == empresa_id,
                or_(
                    Cliente.nombre.ilike(patron),
                    Cliente.apellido.ilike(patron),
                    Cliente.ci.ilike(patron),
                ),
            )
            opciones = [Reserva.cliente_id.in_(clientes)]
            if termino.isdigit():
                opciones.append(Reserva.id == int(termino))
            condiciones.append(or_(*opciones))
        return condiciones

    @classmethod
    def listar(
        cls,
        db: Session,
        *,
        empresa_id: int,
        sucursal_id: int | None,
        estado: str | None,
        cliente_id: int | None,
        buscar: str | None,
        desde: datetime | None,
        hasta: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Reserva], int]:
        condiciones = cls._condiciones(
            empresa_id=empresa_id,
            sucursal_id=sucursal_id,
            estado=estado,
            cliente_id=cliente_id,
            buscar=buscar,
            desde=desde,
            hasta=hasta,
        )
        total = int(
            db.scalar(
                select(func.count()).select_from(Reserva).where(*condiciones)
            )
            or 0
        )
        statement = (
            select(Reserva)
            .options(*cls._carga_completa())
            .where(*condiciones)
            .order_by(Reserva.fecha_atencion.asc(), Reserva.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(db.scalars(statement).unique().all()), total

    @classmethod
    def estadisticas(
        cls,
        db: Session,
        *,
        empresa_id: int,
        sucursal_id: int | None,
        desde: datetime | None,
        hasta: datetime | None,
    ) -> dict:
        condiciones = [
            Reserva.empresa_id == empresa_id,
        ]
        if sucursal_id is not None:
            condiciones.append(Reserva.sucursal_id == sucursal_id)
        if desde is not None:
            condiciones.append(Reserva.fecha_atencion >= desde)
        if hasta is not None:
            condiciones.append(Reserva.fecha_atencion <= hasta)

        filas = db.execute(
            select(Reserva.estado, func.count(Reserva.id))
            .where(*condiciones)
            .group_by(Reserva.estado)
        ).all()
        por_estado = {str(estado): int(total) for estado, total in filas}

        ids = select(Reserva.id).where(*condiciones)
        total_unidades = int(
            db.scalar(
                select(func.coalesce(func.sum(DetalleReserva.cantidad), 0)).where(
                    DetalleReserva.empresa_id == empresa_id,
                    DetalleReserva.reserva_id.in_(ids),
                )
            )
            or 0
        )
        return {
            "por_estado": por_estado,
            "total_reservas": sum(por_estado.values()),
            "total_unidades": total_unidades,
        }

    @classmethod
    def obtener_venta_por_reserva(
        cls, db: Session, *, empresa_id: int, reserva_id: int
    ) -> Venta | None:
        statement = select(Venta).where(
            Venta.empresa_id == empresa_id,
            Venta.reserva_id == reserva_id,
        )
        return db.scalars(statement).first()
