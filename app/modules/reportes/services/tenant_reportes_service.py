"""Tenant-scoped conventional metrics and bounded generative interpretation."""
import json
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.modules.asistencia_inteligente.providers.base import (
    AsistenteIAProvider, AsistenteRespuestaInvalidaError,
)
from app.modules.asistencia_inteligente.providers.llm import crear_proveedor_ia
from app.modules.catalogo.models.models import Producto
from app.modules.inventario.models.models import Inventario
from app.modules.reservas.models.models import Reserva
from app.modules.sucursales.models.models import Sucursal
from app.modules.ventas.models.models import Venta

MAX_DIAS_REPORTE = 366


class ReporteTenantError(Exception):
    def __init__(self, codigo: int, detalle: str):
        self.codigo, self.detalle = codigo, detalle
        super().__init__(detalle)


class RespuestaGenerativa(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resumen: str = Field(min_length=1, max_length=1200)
    alertas: list[str] = Field(default_factory=list, max_length=5)


def obtener_proveedor_reporte() -> AsistenteIAProvider:
    return crear_proveedor_ia()


class TenantReportesService:
    @staticmethod
    def _rango(desde: date | None, hasta: date | None) -> tuple[datetime | None, datetime | None]:
        if desde and hasta and desde > hasta:
            raise ReporteTenantError(422, "El rango de fechas es inválido")
        if desde and hasta and (hasta - desde).days > MAX_DIAS_REPORTE:
            raise ReporteTenantError(422, "El rango máximo es de 366 días")
        inicio = datetime.combine(desde, time.min) if desde else None
        fin = datetime.combine(hasta + timedelta(days=1), time.min) if hasta else None
        return inicio, fin

    @staticmethod
    def _validar_sucursal(db: Session, empresa_id: int, sucursal_id: int | None) -> None:
        if sucursal_id is None:
            return
        existe = db.scalar(select(Sucursal.id).where(
            Sucursal.id == sucursal_id, Sucursal.empresa_id == empresa_id,
            Sucursal.estado.is_(True),
        ))
        if existe is None:
            raise ReporteTenantError(404, "Sucursal no encontrada")

    @classmethod
    def resumen(
        cls, db: Session, *, empresa_id: int, fecha_desde: date | None,
        fecha_hasta: date | None, sucursal_id: int | None,
    ) -> dict:
        inicio, fin = cls._rango(fecha_desde, fecha_hasta)
        cls._validar_sucursal(db, empresa_id, sucursal_id)
        v, r, i, p = Venta.__table__, Reserva.__table__, Inventario.__table__, Producto.__table__

        filtro_ventas = [v.c.empresa_id == empresa_id]
        filtro_reservas = [r.c.empresa_id == empresa_id]
        filtro_inventario = [i.c.empresa_id == empresa_id]
        if sucursal_id is not None:
            filtro_ventas.append(v.c.sucursal_id == sucursal_id)
            filtro_reservas.append(r.c.sucursal_id == sucursal_id)
            filtro_inventario.append(i.c.sucursal_id == sucursal_id)
        if inicio is not None:
            filtro_ventas.append(v.c.fecha_hora >= inicio)
            filtro_reservas.append(r.c.fecha_reserva >= inicio)
        if fin is not None:
            filtro_ventas.append(v.c.fecha_hora < fin)
            filtro_reservas.append(r.c.fecha_reserva < fin)

        ventas = db.execute(select(
            func.count(v.c.id).filter(v.c.estado == "COMPLETADA").label("completadas"),
            func.coalesce(func.sum(v.c.total).filter(v.c.estado == "COMPLETADA"), 0).label("total"),
            func.count(v.c.id).filter(v.c.estado == "PENDIENTE").label("pendientes"),
        ).where(*filtro_ventas)).mappings().one()
        reservas = db.execute(select(
            func.count(r.c.id).label("total"),
            func.count(r.c.id).filter(r.c.estado == "PENDIENTE").label("pendientes"),
        ).where(*filtro_reservas)).mappings().one()
        inventario = db.execute(select(
            func.coalesce(func.sum(i.c.stock_actual), 0).label("stock_actual"),
            func.coalesce(func.sum(i.c.stock_reservado), 0).label("stock_reservado"),
        ).where(*filtro_inventario)).mappings().one()
        productos = int(db.scalar(select(func.count(p.c.id)).where(
            p.c.empresa_id == empresa_id, p.c.estado.is_(True),
        )) or 0)
        completadas = int(ventas["completadas"] or 0)
        total = Decimal(str(ventas["total"] or 0))
        return {
            "periodo": {"fecha_desde": fecha_desde, "fecha_hasta": fecha_hasta},
            "sucursal_id": sucursal_id,
            "ventas_completadas": completadas,
            "ventas_pendientes": int(ventas["pendientes"] or 0),
            "total_vendido": total,
            "ticket_promedio": total / completadas if completadas else Decimal("0.00"),
            "reservas_total": int(reservas["total"] or 0),
            "reservas_pendientes": int(reservas["pendientes"] or 0),
            "productos_activos": productos,
            "stock_actual": int(inventario["stock_actual"] or 0),
            "stock_reservado": int(inventario["stock_reservado"] or 0),
            "stock_disponible": int(inventario["stock_actual"] or 0) - int(inventario["stock_reservado"] or 0),
        }

    @staticmethod
    def generar(resumen: dict, proveedor: AsistenteIAProvider | None = None) -> dict:
        proveedor = proveedor or obtener_proveedor_reporte()
        contexto = json.dumps(resumen, default=str, ensure_ascii=False, sort_keys=True)
        contenido = proveedor.generar(
            system=(
                "Analiza únicamente las métricas agregadas suministradas. No inventes datos, "
                "clientes, productos ni comparaciones. Devuelve JSON exacto con resumen (máximo "
                "1200 caracteres) y alertas (lista de máximo 5 textos)."
            ),
            usuario=f"Métricas agregadas de una sola empresa:\n{contexto}",
        )
        try:
            validada = RespuestaGenerativa.model_validate(json.loads((contenido or "").strip()))
        except (ValueError, TypeError, ValidationError) as exc:
            raise AsistenteRespuestaInvalidaError("Respuesta generativa inválida") from exc
        return {"fuente": resumen, **validada.model_dump()}

    @classmethod
    def dinamico(cls, db: Session, *, empresa_id: int, tipo: str,
                 pregunta: str | None, fecha_desde: date | None,
                 fecha_hasta: date | None, sucursal_id: int | None) -> dict:
        """Return an aggregate, tabular and chart-ready report without PII."""
        aliases = {
            "ventas": "ventas", "venta": "ventas", "sales": "ventas",
            "inventario": "inventario", "stock": "inventario",
            "reservas": "reservas", "reserva": "reservas",
            "clientes": "clientes", "cliente": "clientes",
            "productos": "productos", "producto": "productos",
        }
        normalized = (tipo or "").strip().lower()
        selected = aliases.get(normalized)
        if not selected and pregunta:
            words = re.findall(r"[a-záéíóúñ]+", pregunta.lower())
            selected = next((aliases[word] for word in words if word in aliases), None)
        if not selected:
            raise ReporteTenantError(422, "Indique un tipo de reporte válido")
        summary = cls.resumen(db, empresa_id=empresa_id, fecha_desde=fecha_desde,
                              fecha_hasta=fecha_hasta, sucursal_id=sucursal_id)
        if selected == "ventas":
            rows = [{"metrica": "completadas", "valor": summary["ventas_completadas"]},
                    {"metrica": "pendientes", "valor": summary["ventas_pendientes"]},
                    {"metrica": "total_vendido", "valor": summary["total_vendido"]}]
        elif selected == "inventario":
            rows = [{"metrica": "stock_actual", "valor": summary["stock_actual"]},
                    {"metrica": "stock_reservado", "valor": summary["stock_reservado"]},
                    {"metrica": "stock_disponible", "valor": summary["stock_disponible"]}]
        elif selected == "reservas":
            rows = [{"metrica": "total", "valor": summary["reservas_total"]},
                    {"metrica": "pendientes", "valor": summary["reservas_pendientes"]}]
        else:
            rows = [{"metrica": "productos_activos", "valor": summary["productos_activos"]}]
            if selected == "clientes":
                # Deliberately expose only a count; names, email and phone never leave the API.
                rows = [{"metrica": "clientes_con_actividad", "valor": cls._clientes_count(
                    db, empresa_id=empresa_id, fecha_desde=fecha_desde,
                    fecha_hasta=fecha_hasta, sucursal_id=sucursal_id)}]
        return {"tipo": selected, "pregunta": pregunta, "filtros": summary["periodo"],
                "resumen": summary, "filas": rows,
                "serie": [{"label": row["metrica"], "value": row["valor"]} for row in rows]}

    @staticmethod
    def _clientes_count(db: Session, *, empresa_id: int, fecha_desde: date | None,
                        fecha_hasta: date | None, sucursal_id: int | None) -> int:
        v = Venta.__table__
        filters = [v.c.empresa_id == empresa_id, v.c.cliente_id.is_not(None),
                   v.c.estado == "COMPLETADA"]
        if sucursal_id is not None:
            filters.append(v.c.sucursal_id == sucursal_id)
        inicio, fin = TenantReportesService._rango(fecha_desde, fecha_hasta)
        if inicio is not None: filters.append(v.c.fecha_hora >= inicio)
        if fin is not None: filters.append(v.c.fecha_hora < fin)
        return int(db.scalar(select(func.count(func.distinct(v.c.cliente_id))).where(*filters)) or 0)
