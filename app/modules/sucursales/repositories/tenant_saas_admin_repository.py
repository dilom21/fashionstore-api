"""Administracion SaaS multitenant del prototipo academico.

La dependencia JWT valida membresia/rol/suscripcion; estas consultas SIEMPRE
filtran empresa_id. Para crear o habilitar sucursales se bloquea la fila empresa,
serializando el recuento y el limite de plan entre solicitudes de esta API.

Nunca usa una conexion nueva, no recibe empresa_id desde el body y no consulta
otras empresas para formar respuestas. No instala RLS ni sustituye la verificacion
de ACL SQL exigida para despliegue.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import (
    BigInteger, Boolean, Date, Integer, String, and_, column, func, insert,
    select, table, update,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.sucursales.models.models import Ciudad, Sucursal
from app.modules.autenticacion_seguridad.models.models import Empleado
from app.modules.inventario.models.models import Inventario
from app.modules.reservas.models.models import Reserva


class AdministracionTenantError(Exception):
    def __init__(self, codigo: int, detalle: str):
        super().__init__(detalle)
        self.codigo = codigo
        self.detalle = detalle


empresa = table(
    "empresa", column("id", BigInteger), column("slug", String),
    column("nombre_comercial", String), column("estado", Boolean),
)
plan_saas = table(
    "plan_saas", column("id", BigInteger), column("codigo", String),
    column("nombre", String), column("estado", Boolean),
    column("max_sucursales_activas", Integer),
    column("permite_productos", Boolean),
    column("permite_ventas", Boolean), column("permite_reservas", Boolean),
    column("permite_offline", Boolean),
    column("permite_reportes_convencionales", Boolean),
    column("permite_reportes_ia", Boolean),
)
suscripcion = table(
    "suscripcion", column("id", BigInteger), column("empresa_id", BigInteger),
    column("plan_saas_id", BigInteger), column("fecha_inicio", Date),
    column("fecha_vencimiento", Date), column("estado", String),
)

sucursal = Sucursal.__table__
ciudad = Ciudad.__table__
empleado = Empleado.__table__
inventario = Inventario.__table__
reserva = Reserva.__table__


def _hoy():
    return datetime.now(ZoneInfo("America/La_Paz")).date()


def _empresa_propietaria(db: Session, empresa_id: int, *, bloqueo: bool):
    consulta = select(empresa.c.id).where(
        empresa.c.id == empresa_id, empresa.c.estado.is_(True)
    )
    if bloqueo:
        consulta = consulta.with_for_update()
    if db.scalar(consulta) is None:
        raise AdministracionTenantError(404, "Empresa no encontrada")


def _plan_actual(db: Session, empresa_id: int):
    hoy = _hoy()
    consulta = (
        select(
            suscripcion.c.id.label("suscripcion_id"),
            suscripcion.c.fecha_inicio,
            suscripcion.c.fecha_vencimiento,
            suscripcion.c.estado.label("estado_suscripcion"),
            plan_saas.c.codigo, plan_saas.c.nombre,
            plan_saas.c.max_sucursales_activas,
            plan_saas.c.permite_productos,
            plan_saas.c.permite_ventas,
            plan_saas.c.permite_reservas,
            plan_saas.c.permite_offline,
            plan_saas.c.permite_reportes_convencionales,
            plan_saas.c.permite_reportes_ia,
        )
        .select_from(
            suscripcion.join(
                plan_saas, suscripcion.c.plan_saas_id == plan_saas.c.id
            )
        )
        .where(
            suscripcion.c.empresa_id == empresa_id,
            suscripcion.c.estado == "ACTIVA",
            suscripcion.c.fecha_inicio <= hoy,
            suscripcion.c.fecha_vencimiento >= hoy,
            plan_saas.c.estado.is_(True),
        )
    )
    filas = db.execute(consulta).mappings().all()
    if len(filas) != 1:
        raise AdministracionTenantError(403, "Suscripcion empresarial no vigente")
    return dict(filas[0])


def _contar_sucursales(db: Session, empresa_id: int):
    return int(db.scalar(
        select(func.count())
        .select_from(sucursal)
        .where(sucursal.c.empresa_id == empresa_id,
               sucursal.c.estado.is_(True))
    ))


def _plan_visible(db: Session, empresa_id: int, *, lock: bool):
    _empresa_propietaria(db, empresa_id, bloqueo=lock)
    plan = _plan_actual(db, empresa_id)
    plan["sucursales_activas"] = _contar_sucursales(db, empresa_id)
    return plan


def _limite(db: Session, empresa_id: int):
    plan = _plan_visible(db, empresa_id, lock=True)
    if plan["sucursales_activas"] >= plan["max_sucursales_activas"]:
        raise AdministracionTenantError(
            409, "Limite de sucursales activas alcanzado para el plan"
        )


def _consultar_sucursal(db: Session, empresa_id: int, sucursal_id: int):
    fila = db.execute(
        select(sucursal.c.id, sucursal.c.empresa_id, sucursal.c.ciudad_id,
               sucursal.c.nombre, sucursal.c.direccion, sucursal.c.telefono,
               sucursal.c.estado)
        .where(sucursal.c.empresa_id == empresa_id,
               sucursal.c.id == sucursal_id)
    ).mappings().one_or_none()
    if fila is None:
        raise AdministracionTenantError(404, "Sucursal no encontrada")
    return dict(fila)


class TenantEmpresaAdminRepository:
    @staticmethod
    def empresa_actual(db: Session, empresa_id: int):
        plan = _plan_visible(db, empresa_id, lock=False)
        fila = db.execute(
            select(empresa.c.id, empresa.c.slug, empresa.c.nombre_comercial)
            .where(empresa.c.id == empresa_id, empresa.c.estado.is_(True))
        ).mappings().one_or_none()
        if fila is None:
            raise AdministracionTenantError(404, "Empresa no encontrada")
        return {**dict(fila), "suscripcion": plan}

    @staticmethod
    def plan_actual(db: Session, empresa_id: int):
        return _plan_visible(db, empresa_id, lock=False)

    @staticmethod
    def sucursales(db: Session, empresa_id: int):
        _empresa_propietaria(db, empresa_id, bloqueo=False)
        filas = db.execute(
            select(sucursal.c.id, sucursal.c.ciudad_id,
                   sucursal.c.nombre, sucursal.c.direccion,
                   sucursal.c.telefono, sucursal.c.estado)
            .where(sucursal.c.empresa_id == empresa_id)
            .order_by(sucursal.c.id)
        ).mappings().all()
        return [dict(fila) for fila in filas]

    @staticmethod
    def crear_sucursal(db: Session, *, empresa_id: int, ciudad_id: int,
                      nombre: str, direccion: str, telefono: str | None):
        _limite(db, empresa_id)
        activa = db.scalar(
            select(ciudad.c.id).where(
                ciudad.c.id == ciudad_id, ciudad.c.estado.is_(True)
            )
        )
        if activa is None:
            raise AdministracionTenantError(404, "Ciudad activa no encontrada")
        nuevo_id = db.execute(
            insert(sucursal).values(
                empresa_id=empresa_id, ciudad_id=ciudad_id,
                nombre=nombre, direccion=direccion,
                telefono=telefono, estado=True,
            ).returning(sucursal.c.id)
        ).scalar_one()
        return _consultar_sucursal(db, empresa_id, nuevo_id)

    @staticmethod
    def cambiar_estado_sucursal(db: Session, *, empresa_id: int,
                                sucursal_id: int, habilitar: bool):
        _empresa_propietaria(db, empresa_id, bloqueo=True)
        actual = _consultar_sucursal(db, empresa_id, sucursal_id)
        _plan_actual(db, empresa_id)
        if actual["estado"] == habilitar:
            return actual
        if habilitar:
            _limite(db, empresa_id)
            activa = db.scalar(select(ciudad.c.id).where(
                ciudad.c.id == actual["ciudad_id"],
                ciudad.c.estado.is_(True)
            ))
            if activa is None:
                raise AdministracionTenantError(409, "Ciudad de sucursal inactiva")
        else:
            empleados_activos = db.scalar(select(func.count())
                .select_from(empleado).where(
                    empleado.c.empresa_id == empresa_id,
                    empleado.c.sucursal_id == sucursal_id,
                    empleado.c.estado.is_(True)))
            stock_reservado = db.scalar(select(func.count())
                .select_from(inventario).where(
                    inventario.c.empresa_id == empresa_id,
                    inventario.c.sucursal_id == sucursal_id,
                    inventario.c.stock_reservado > 0))
            reservas_pendientes = db.scalar(select(func.count())
                .select_from(reserva).where(
                    reserva.c.empresa_id == empresa_id,
                    reserva.c.sucursal_id == sucursal_id,
                    reserva.c.estado.in_(("PENDIENTE", "CONFIRMADA"))))
            if empleados_activos or stock_reservado or reservas_pendientes:
                raise AdministracionTenantError(
                    409, "Sucursal con empleados o reservas activas"
                )
        db.execute(update(sucursal)
            .where(sucursal.c.empresa_id == empresa_id,
                   sucursal.c.id == sucursal_id)
            .values(estado=habilitar))
        return _consultar_sucursal(db, empresa_id, sucursal_id)

    @staticmethod
    def cambiar_plan(db: Session, *, empresa_id: int, codigo: str):
        _empresa_propietaria(db, empresa_id, bloqueo=True)
        actual = _plan_actual(db, empresa_id)
        nuevo = db.execute(select(
            plan_saas.c.id, plan_saas.c.max_sucursales_activas
        ).where(
            plan_saas.c.codigo == codigo,
            plan_saas.c.estado.is_(True),
        )).mappings().one_or_none()
        if nuevo is None:
            raise AdministracionTenantError(404, "Plan no disponible")
        activas = _contar_sucursales(db, empresa_id)
        if activas > nuevo["max_sucursales_activas"]:
            raise AdministracionTenantError(
                409, "No se puede cambiar a un plan con menos sucursales"
            )
        db.execute(update(suscripcion).where(
            suscripcion.c.id == actual["suscripcion_id"],
            suscripcion.c.empresa_id == empresa_id,
        ).values(plan_saas_id=nuevo["id"]))
        return _plan_visible(db, empresa_id, lock=False)
