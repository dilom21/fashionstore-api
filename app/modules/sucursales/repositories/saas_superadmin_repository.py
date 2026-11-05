"""CU01/CU02 — operaciones globales SaaS, exclusivamente superadministrador.

Dos rutas de ejecucion con el MISMO contrato publico:

* PostgreSQL (produccion/laboratorio): el endpoint usa el DSN global
  ``SAAS_ADMIN_DATABASE_URL`` y este repositorio invoca funciones
  ``saas_admin.*`` (SECURITY DEFINER, dueno NOLOGIN). El rol administrativo no
  tiene privilegios de tabla ni puede leer `public.usuario` directamente.
* SQLite (pruebas offline): se conserva la implementacion relacional historica
  para no perder la cobertura unitaria existente. Ese motor no tiene RLS ni
  privilegios SQL, por lo que nunca sustituye una prueba real.

NO utiliza el contexto tenant ni el rol ADMINISTRADOR de una tienda.
No toca credenciales de clientes ni usuario/rol legacy.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import (
    BigInteger, Boolean, Date, Integer, String, column, func, insert,
    select, table, text, update,
)
from sqlalchemy.exc import DBAPIError
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
sucursal=table("sucursal",column("id",BigInteger),
    column("empresa_id",BigInteger),column("estado",Boolean),schema="public")

_PLAN_FIELDS={
    "codigo","nombre","descripcion","max_sucursales_activas",
    "permite_productos","permite_ventas","permite_reservas",
    "permite_offline","permite_reportes_convencionales","permite_reportes_ia",
}
_ESTADOS={"PENDIENTE","ACTIVA","SUSPENDIDA","VENCIDA","CANCELADA"}


def _es_postgres(db:Session)->bool:
    try:
        bind=db.get_bind()
    except AttributeError:
        return False
    return getattr(getattr(bind,"dialect",None),"name","")=="postgresql"


def _jsonb(db:Session,sql:str,params:dict|None=None):
    """Ejecuta una funcion global y normaliza jsonb/dict de psycopg."""
    valor=db.scalar(text(sql),params or {})
    if isinstance(valor,(str,bytes)):
        valor=json.loads(valor)
    return valor


def _funcion(db:Session,sql:str,params:dict|None=None)->dict:
    """Invoca `saas_admin.*` y traduce el sobre {"ok":...} a excepcion."""
    try:
        data=_jsonb(db,sql,params)
    except DBAPIError as exc:
        estado=getattr(getattr(exc,"orig",None),"sqlstate",None)
        if estado=="23505":
            raise SuperadminSaaSError(
                409,"Conflicto de empresa, plan o suscripción") from exc
        raise
    if not isinstance(data,dict):
        raise SuperadminSaaSError(503,"Respuesta global ilegible")
    if data.get("ok") is not True:
        raise SuperadminSaaSError(
            int(data.get("codigo",409)),str(data.get("mensaje","Conflicto global")))
    return data


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
        if _es_postgres(db):
            # Las funciones de lectura devuelven un ARRAY jsonb, no un sobre.
            filas=_jsonb(db,"SELECT saas_admin.listar_empresas()")
            return filas if isinstance(filas,list) else []
        return [dict(row) for row in db.execute(
            select(empresa).order_by(empresa.c.id)).mappings().all()]

    @staticmethod
    def crear_empresa(db:Session,*,razon_social:str,nombre_comercial:str,
                      slug:str,nit:str|None,correo_contacto:str|None,
                      telefono:str|None,plan_id:int,
                      fecha_inicio:date,fecha_vencimiento:date):
        if _es_postgres(db):
            data=_funcion(db,
                "SELECT saas_admin.crear_empresa("
                "CAST(:razon AS text), CAST(:nombre AS text), CAST(:slug AS text), "
                "CAST(:nit AS text), CAST(:correo AS text), CAST(:telefono AS text), "
                "CAST(:plan_id AS bigint), CAST(:inicio AS date), CAST(:fin AS date))",
                {"razon":razon_social,"nombre":nombre_comercial,"slug":slug,
                 "nit":nit,"correo":correo_contacto,"telefono":telefono,
                 "plan_id":plan_id,"inicio":fecha_inicio,"fin":fecha_vencimiento})
            return {"empresa":data["empresa"],"suscripcion":data["suscripcion"]}
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
        if _es_postgres(db):
            data=_funcion(db,
                "SELECT saas_admin.cambiar_estado_empresa("
                "CAST(:id AS bigint), CAST(:estado AS boolean))",
                {"id":empresa_id,"estado":estado})
            return data["empresa"]
        original=_empresa_por_id(db,empresa_id,bloqueo=True)
        if original["estado"]!=estado:
            db.execute(update(empresa).where(
                empresa.c.id==empresa_id).values(estado=estado))
        # La desactivación bloquea el acceso de todas las membresías
        # mediante TenantAuthRepository, sin borrar datos del tenant.
        return _empresa_por_id(db,empresa_id)

    @staticmethod
    def listar_planes(db:Session):
        if _es_postgres(db):
            filas=_jsonb(db,"SELECT saas_admin.listar_planes()")
            return filas if isinstance(filas,list) else []
        return [dict(row) for row in db.execute(select(plan).order_by(
            plan.c.id)).mappings().all()]

    @staticmethod
    def crear_plan(db:Session,datos:dict):
        if set(datos)!=_PLAN_FIELDS:
            raise SuperadminSaaSError(422,"Campos del plan incompletos")
        if _es_postgres(db):
            data=_funcion(db,
                "SELECT saas_admin.crear_plan("
                "CAST(:codigo AS text), CAST(:nombre AS text), "
                "CAST(:descripcion AS text), CAST(:maximo AS integer), "
                "CAST(:productos AS boolean), CAST(:ventas AS boolean), "
                "CAST(:reservas AS boolean), CAST(:offline AS boolean), "
                "CAST(:reportes AS boolean), CAST(:ia AS boolean))",
                {"codigo":datos["codigo"],"nombre":datos["nombre"],
                 "descripcion":datos["descripcion"],
                 "maximo":datos["max_sucursales_activas"],
                 "productos":datos["permite_productos"],
                 "ventas":datos["permite_ventas"],
                 "reservas":datos["permite_reservas"],
                 "offline":datos["permite_offline"],
                 "reportes":datos["permite_reportes_convencionales"],
                 "ia":datos["permite_reportes_ia"]})
            return data["plan"]
        code=datos["codigo"]
        if db.scalar(select(plan.c.id).where(plan.c.codigo==code)) is not None:
            raise SuperadminSaaSError(409,"Código de plan duplicado")
        pid=db.execute(insert(plan).values(**datos,estado=True).returning(
            plan.c.id)).scalar_one()
        return dict(db.execute(select(plan).where(
            plan.c.id==pid)).mappings().one())

    @staticmethod
    def cambiar_estado_plan(db:Session,plan_id:int,estado:bool):
        if _es_postgres(db):
            data=_funcion(db,
                "SELECT saas_admin.cambiar_estado_plan("
                "CAST(:id AS bigint), CAST(:estado AS boolean))",
                {"id":plan_id,"estado":estado})
            return data["plan"]
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
        if _es_postgres(db):
            data=_funcion(db,"SELECT saas_admin.listar_suscripciones(CAST(:id AS bigint))",
                          {"id":empresa_id})
            return data["suscripciones"]
        _empresa_por_id(db,empresa_id)
        return [dict(row) for row in db.execute(
            select(suscripcion).where(suscripcion.c.empresa_id==empresa_id)
            .order_by(suscripcion.c.id)).mappings().all()]

    @staticmethod
    def asignar_suscripcion(db:Session,*,empresa_id:int,plan_id:int,
                            fecha_inicio:date,fecha_vencimiento:date,
                            observacion:str|None=None):
        if _es_postgres(db):
            data=_funcion(db,
                "SELECT saas_admin.asignar_suscripcion("
                "CAST(:empresa AS bigint), CAST(:plan AS bigint), "
                "CAST(:inicio AS date), CAST(:fin AS date), CAST(:obs AS text))",
                {"empresa":empresa_id,"plan":plan_id,"inicio":fecha_inicio,
                 "fin":fecha_vencimiento,"obs":observacion})
            return data["suscripcion"]
        _fechas_validas(fecha_inicio,fecha_vencimiento)
        # Lock de empresa ANTES de leer/cancelar, para serializar
        # asignaciones de plan por administradores concurrentes.
        _empresa_por_id(db,empresa_id,bloqueo=True)
        nuevo=_plan_activo(db,plan_id)
        # CU02: rechazar downgrade que deja más sucursales ACTIVAS
        # de las permitidas en el plan de destino.
        branches=int(db.scalar(select(func.count()).select_from(sucursal)
            .where(sucursal.c.empresa_id==empresa_id,
                   sucursal.c.estado.is_(True))) or 0)
        if branches>nuevo["max_sucursales_activas"]:
            raise SuperadminSaaSError(409,
                "El plan elegido permite menos sucursales activas "
                "que las existentes")
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
        if _es_postgres(db):
            data=_funcion(db,
                "SELECT saas_admin.cambiar_estado_suscripcion("
                "CAST(:empresa AS bigint), CAST(:sub AS bigint), CAST(:estado AS text))",
                {"empresa":empresa_id,"sub":suscripcion_id,"estado":estado})
            return data["suscripcion"]
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
