"""Consultas agregadas de auditoria para CU28 (solo ADMINISTRADOR).

Usa la tabla real ``bitacora`` (id, usuario_id, fecha_hora, ip, accion,
entidad_afectada, descripcion). CU28 aporta agregados + exportacion; no
reemplaza la consulta detallada de CU05.

``bitacora`` no tiene ``sucursal_id``: el filtro de sucursal no aplica a este
reporte (documentado en el schema/servicio). No se exporta la IP.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Bitacora, Usuario


class AuditoriaReporteRepository:
    @staticmethod
    def _condiciones(desde: datetime | None, hasta: datetime | None) -> list:
        condiciones = []
        if desde is not None:
            condiciones.append(Bitacora.fecha_hora >= desde)
        if hasta is not None:
            condiciones.append(Bitacora.fecha_hora < hasta)
        return condiciones

    @staticmethod
    def contar(
        db: Session, *, desde: datetime | None, hasta: datetime | None
    ) -> int:
        condiciones = AuditoriaReporteRepository._condiciones(desde, hasta)
        return int(
            db.scalar(
                select(func.count()).select_from(Bitacora).where(*condiciones)
            )
            or 0
        )

    @staticmethod
    def por_accion(
        db: Session, *, desde: datetime | None, hasta: datetime | None
    ) -> list:
        condiciones = AuditoriaReporteRepository._condiciones(desde, hasta)
        statement = (
            select(Bitacora.accion, func.count(Bitacora.id))
            .where(*condiciones)
            .group_by(Bitacora.accion)
            .order_by(func.count(Bitacora.id).desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_entidad(
        db: Session, *, desde: datetime | None, hasta: datetime | None
    ) -> list:
        condiciones = AuditoriaReporteRepository._condiciones(desde, hasta)
        etiqueta = func.coalesce(Bitacora.entidad_afectada, "SIN_ENTIDAD")
        statement = (
            select(etiqueta, func.count(Bitacora.id))
            .where(*condiciones)
            .group_by(etiqueta)
            .order_by(func.count(Bitacora.id).desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_usuario(
        db: Session, *, desde: datetime | None, hasta: datetime | None
    ) -> list:
        condiciones = AuditoriaReporteRepository._condiciones(desde, hasta)
        etiqueta = func.coalesce(Usuario.correo, "SIN_USUARIO")
        statement = (
            select(etiqueta, func.count(Bitacora.id))
            .select_from(Bitacora)
            .outerjoin(Usuario, Usuario.id == Bitacora.usuario_id)
            .where(*condiciones)
            .group_by(etiqueta)
            .order_by(func.count(Bitacora.id).desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def evolucion(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        agrupacion: str = "DIA",
    ) -> list:
        unidad = "day" if agrupacion.upper() == "DIA" else "month"
        condiciones = AuditoriaReporteRepository._condiciones(desde, hasta)
        periodo = func.date_trunc(unidad, Bitacora.fecha_hora).label("periodo")
        statement = (
            select(periodo, func.count(Bitacora.id))
            .where(*condiciones)
            .group_by(periodo)
            .order_by(periodo)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def listar(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        limit: int = 20,
        offset: int = 0,
    ) -> list:
        """Tabla paginada (sin IP ni datos internos)."""
        condiciones = AuditoriaReporteRepository._condiciones(desde, hasta)
        statement = (
            select(
                Bitacora.id,
                Bitacora.fecha_hora,
                Usuario.correo,
                Bitacora.accion,
                Bitacora.entidad_afectada,
                Bitacora.descripcion,
            )
            .outerjoin(Usuario, Usuario.id == Bitacora.usuario_id)
            .where(*condiciones)
            .order_by(Bitacora.fecha_hora.desc(), Bitacora.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(db.execute(statement).all())
