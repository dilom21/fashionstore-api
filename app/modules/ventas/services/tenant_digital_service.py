"""CU14 SaaS: conversion atomica de un carrito propio en venta PENDIENTE."""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.tenant_context import TenantContext
from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.ventas.repositories.tenant_repository import VentaTenantRepository
from app.modules.ventas.schemas.schemas import RealizarCompraDigitalRequest
from app.modules.ventas.services.service import _detalle_response
from app.modules.ventas.services.tenant_transaccional import (
    VentaTenantDeshabilitada, verificar_principal_ventas,
)


class VentaDigitalTenantError(Exception):
    pass


class VentaDigitalTenantConflicto(VentaDigitalTenantError):
    pass


def _cliente(db: Session, tenant: TenantContext) -> Cliente:
    cliente = db.scalars(select(Cliente).where(
        Cliente.empresa_id == tenant.empresa_id,
        Cliente.usuario_id == tenant.usuario_id,
        Cliente.estado.is_(True),
    )).first()
    if cliente is None:
        raise VentaDigitalTenantConflicto()
    return cliente


class VentaDigitalTenantService:
    @staticmethod
    def crear(db: Session, tenant: TenantContext, datos: RealizarCompraDigitalRequest):
        if tenant.contexto != "cliente":
            raise VentaDigitalTenantConflicto()
        cliente = _cliente(db, tenant)
        verificar_principal_ventas(db, tenant, "sp_crear_venta_digital_tenant")
        try:
            fila = db.execute(text(
                "CALL public.sp_crear_venta_digital_tenant("
                ":empresa_id,:usuario_id,:cliente_id,:carrito_id,:canal,NULL)"
            ), {
                "empresa_id": tenant.empresa_id,
                "usuario_id": tenant.usuario_id,
                "cliente_id": int(cliente.id),
                "carrito_id": int(datos.carrito_id),
                "canal": datos.canal.value,
            }).mappings().one()
            venta_id = int(fila["p_venta_id"])
            db.commit()
        except DBAPIError as exc:
            db.rollback()
            estado = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if estado in {"23505", "23514", "P0001", "P0002"}:
                raise VentaDigitalTenantConflicto() from exc
            raise
        venta = VentaTenantRepository.obtener_por_id(
            db, empresa_id=tenant.empresa_id, venta_id=venta_id
        )
        if venta is None or venta.cliente_id != int(cliente.id):
            raise VentaTenantDeshabilitada("Venta creada no visible para su actor")
        return _detalle_response(db, venta)
