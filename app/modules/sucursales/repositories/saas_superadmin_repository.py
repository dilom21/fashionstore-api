"""CU01/CU02 — operaciones globales SaaS, exclusivamente superadministrador.

NO utiliza el contexto tenant ni el rol ADMINISTRADOR de una tienda.
Estas operaciones necesitan un servicio SQL con privilegios propios
y ACL/RLS auditados antes del despliegue real. Flags OFF por defecto.
No toca credenciales de clientes ni usuario/rol legacy.
"""
from __future__ import annotations

from datetime import date
from sqlalchemy import (
    BigInteger, Boolean, Date, Integer, String, column, func, insert,
    select, table, update,
)
from sqlalchemy.orm import Session


class SuperadminSaaSError(Exception):
    def __init__(self,codigo:int,mensaje:str):
        super().__init__(mensaje)
        self.codigo=codigo
        self.mensaje=mensaje


empresa=table("empresa",
    column("id",BigInteger),column("slug",String),
    column("razon_social",String),column("nombre_comercial",String),
    column("nit",String),column("correo_contacto",String),
    column("telefono",String),column("estado",Boolean),
    schema="public")
plan=table("plan_saas",
    column("id",BigInteger),column("codigo",String),
    column("nombre",String),column("descripcion",String),
    column("max_sucursales_activas",Integer),
    column("permite_productos",Boolean),column("permite_ventas",Boolean),
    column("permite_reservas",Boolean),column("permite_offline",Boolean),
    column("permite_reportes_convencionales",Boolean),
    column("permite_reportes_ia",Boolean),column("estado",Boolean),
    schema="public")
suscripcion=table("suscripcion",
    column("id",BigInteger),column("empresa_id",BigInteger),
    column("plan_saas_id",BigInteger),column("fecha_inicio",Date),
    column("fecha_vencimiento",Date),column("estado",String),
    column("observacion",String),schema="public")
configuracion=table("empresa_configuracion",
    column("empresa_id",BigInteger),schema="public")

_PLAN_FIELDS={
    "codigo","nombre","descripcion","max_sucursales_activas",
    "permite_productos","permite_ventas","permite_reservas",
    "permite_offline","permite_reportes_convencionales","permite_reportes_ia",
}
_ESTADOS={"PENDIENTE","ACTIVA","SUSPENDIDA","VENCIDA","CANCELADA"}


def _empresa_por_id(db:Session,empresa_id:int,*,bloqueo=False):
    q=select(empresa).where(empresa.c.id==empresa_id)
    if bloqueo:
        q=q.with_for_update(of=empresa)
    row=db.execute(q).mappings().one_or_none()
    if row is None:
        raise SuperadminSaaSError(404,"Empresa inexistente")
    return dict(row)


def _plan_activo(db:Session,plan_id:int):
    row=db.execute(select(plan).where(
        plan.c.id==plan_id,plan.c.estado.is_(True))).mappings().one_or_none()
    if row is None:
        raise SuperadminSaaSError(404,"Plan activo inexistente")
    return dict(row)


def _fechas_validas(inicio:date,fin:date):
    if fin<inicio:
        raise SuperadminSaaSError(422,"Fecha de fin anterior al inicio")


def _respuesta_suscripcion(db:Session,suscripcion_id:int):
    return dict(db.execute(select(suscripcion).where(
        suscripcion.c.id==suscripcion_id)).mappings().one())


class SuperadminSaaSRepository:
    @staticmethod
    def listar_empresas(db:Session):
        return [dict(row) for row in db.execute(
            select(empresa).order_by(empresa.c.id)).mappings().all()]

    @staticmethod
    def crear_empresa(db:Session,*,razon_social:str,nombre_comercial:str,
                      slug:str,nit:str|None,correo_contacto:str|None,
                      telefono:str|None,plan_id:int,
                      fecha_inicio:date,fecha_vencimiento:date):
        _fechas_validas(fecha_inicio,fecha_vencimiento)
        _plan_activo(db,plan_id)
        if db.scalar(select(empresa.c.id).where(empresa.c.slug==slug)) is not None:
            raise SuperadminSaaSError(409,"Slug empresarial duplicado")
        nuevo_id=db.execute(insert(empresa).values(
            razon_social=razon_social,nombre_comercial=nombre_comercial,
            slug=slug,nit=nit,correo_contacto=correo_contacto,
            telefono=telefono,estado=True).returning(empresa.c.id)).scalar_one()
        db.execute(insert(configuracion).values(empresa_id=nuevo_id))
        sub_id=db.execute(insert(suscripcion).values(
            empresa_id=nuevo_id,plan_saas_id=plan_id,
            fecha_inicio=fecha_inicio,fecha_vencimiento=fecha_vencimiento,
            estado="ACTIVA").returning(suscripcion.c.id)).scalar_one()
        # No se crea usuario administrador sin credenciales/cuenta verificadas.
        return {"empresa":_empresa_por_id(db,nuevo_id),
                "suscripcion":_respuesta_suscripcion(db,sub_id)}

    @staticmethod
    def cambiar_estado_empresa(db:Session,empresa_id:int,estado:bool):
        original=_empresa_por_id(db,empresa_id,bloqueo=True)
        if original["estado"]!=estado:
            db.execute(update(empresa).where(
                empresa.c.id==empresa_id).values(estado=estado))
        # La desactivación bloquea el acceso de todas las membresías
        # mediante TenantAuthRepository, sin borrar datos del tenant.
        return _empresa_por_id(db,empresa_id)

    @staticmethod
    def listar_planes(db:Session):
        return [dict(row) for row in db.execute(select(plan).order_by(
            plan.c.id)).mappings().all()]

    @staticmethod
    def crear_plan(db:Session,datos:dict):
        if set(datos)!=_PLAN_FIELDS:
            raise SuperadminSaaSError(422,"Campos del plan incompletos")
        code=datos["codigo"]
        if db.scalar(select(plan.c.id).where(plan.c.codigo==code)) is not None:
            raise SuperadminSaaSError(409,"Código de plan duplicado")
        pid=db.execute(insert(plan).values(**datos,estado=True).returning(
            plan.c.id)).scalar_one()
        return dict(db.execute(select(plan).where(
            plan.c.id==pid)).mappings().one())

    @staticmethod
    def cambiar_estado_plan(db:Session,plan_id:int,estado:bool):
        p=db.execute(select(plan).where(plan.c.id==plan_id)
             .with_for_update(of=plan)).mappings().one_or_none()
        if p is None:
            raise SuperadminSaaSError(404,"Plan inexistente")
        if p["estado"]!=estado:
            if not estado:
                count=db.scalar(select(func.count()).select_from(suscripcion)
                  .where(suscripcion.c.plan_saas_id==plan_id,
                         suscripcion.c.estado=="ACTIVA"))
                if count:
                    raise SuperadminSaaSError(
                        409,"No se puede desactivar un plan con suscripciones activas")
            db.execute(update(plan).where(
                plan.c.id==plan_id).values(estado=estado))
        return dict(db.execute(select(plan).where(
            plan.c.id==plan_id)).mappings().one())

    @staticmethod
    def listar_suscripciones(db:Session,empresa_id:int):
        _empresa_por_id(db,empresa_id)
        return [dict(row) for row in db.execute(
            select(suscripcion).where(suscripcion.c.empresa_id==empresa_id)
            .order_by(suscripcion.c.id)).mappings().all()]

    @staticmethod
    def asignar_suscripcion(db:Session,*,empresa_id:int,plan_id:int,
                            fecha_inicio:date,fecha_vencimiento:date,
                            observacion:str|None=None):
        _fechas_validas(fecha_inicio,fecha_vencimiento)
        # Lock de empresa ANTES de leer/cancelar, para serializar
        # asignaciones de plan por administradores concurrentes.
        _empresa_por_id(db,empresa_id,bloqueo=True)
        _plan_activo(db,plan_id)
        db.execute(update(suscripcion).where(
            suscripcion.c.empresa_id==empresa_id,
            suscripcion.c.estado=="ACTIVA").values(estado="CANCELADA"))
        sid=db.execute(insert(suscripcion).values(
            empresa_id=empresa_id,plan_saas_id=plan_id,
            fecha_inicio=fecha_inicio,fecha_vencimiento=fecha_vencimiento,
            estado="ACTIVA",observacion=observacion
        ).returning(suscripcion.c.id)).scalar_one()
        return _respuesta_suscripcion(db,sid)

    @staticmethod
    def cambiar_estado_suscripcion(db:Session,*,empresa_id:int,
                                   suscripcion_id:int,estado:str):
        if estado not in _ESTADOS:
            raise SuperadminSaaSError(422,"Estado de suscripción inválido")
        _empresa_por_id(db,empresa_id,bloqueo=True)
        actual=db.execute(select(suscripcion).where(
            suscripcion.c.id==suscripcion_id,
            suscripcion.c.empresa_id==empresa_id
        ).with_for_update(of=suscripcion)).mappings().one_or_none()
        if actual is None:
            raise SuperadminSaaSError(404,"Suscripción ajena o inexistente")
        if actual["estado"]==estado:
            return dict(actual)
        # Evitar la reactivación accidental de una suscripción finalizada
        # o generar dos suscripciones marcadas ACTIVA por empresa.
        if estado=="ACTIVA":
            raise SuperadminSaaSError(409,
                "La activación requiere una nueva asignación de suscripción")
        if actual["estado"]!="ACTIVA":
            raise SuperadminSaaSError(409,
                "Solo puede modificarse una suscripción activa")
        db.execute(update(suscripcion).where(
            suscripcion.c.id==suscripcion_id,
            suscripcion.c.empresa_id==empresa_id
        ).values(estado=estado))
        return _respuesta_suscripcion(db,suscripcion_id)
