"""CU01: alta del PRIMER administrador tenant tras crear empresa SaaS.

El superadministrador crea la empresa y su suscripción usando CU01.
Después, esta operación indivisible prepara: sucursal del nuevo tenant,
usuario GLOBAL nuevo con rol legacy de mínimo privilegio CLIENTE,
empleado del tenant y usuario_empresa con rol ADMINISTRADOR del tenant.

Reutilizar rol global ADMINISTRADOR para usuario.rol_id concedería
privilegios heredados fuera del tenant; nunca hacerlo. La autoridad de
administración reside SOLO en usuario_empresa.rol_id con empresa_id.
Si la base carece de los roles activos CLIENTE/ADMINISTRADOR o de
ciudad válida y plan vigente, fallar sin insertar registros.

NO enviar password_hash en responses ni logs. Ni contraseñas reales,
ni hashes migrados, ni valores por defecto. No activar SaaS: esta API
sigue cerrada por sus dos flags y privilegios SQL productivos pendientes.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from argon2 import PasswordHasher
from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, String, and_, column, func,
    insert, select, table,
)
from sqlalchemy.orm import Session

from app.core.password_policy import validar_password_segura
from app.modules.sucursales.repositories.saas_superadmin_repository import (
    SuperadminSaaSError,
)

empresa=table("empresa",column("id",BigInteger),column("estado",Boolean),
    schema="public")
plan=table("plan_saas",column("id",BigInteger),
    column("max_sucursales_activas",BigInteger),column("estado",Boolean),
    schema="public")
suscripcion=table("suscripcion",column("empresa_id",BigInteger),
    column("plan_saas_id",BigInteger),column("estado",String),
    column("fecha_inicio",Date),column("fecha_vencimiento",Date),
    schema="public")
ciudad=table("ciudad",column("id",BigInteger),column("estado",Boolean),
    schema="public")
sucursal=table("sucursal",column("id",BigInteger),
    column("empresa_id",BigInteger),column("ciudad_id",BigInteger),
    column("nombre",String),column("direccion",String),
    column("telefono",String),column("estado",Boolean),schema="public")
rol=table("rol",column("id",BigInteger),column("nombre",String),
    column("estado",Boolean),schema="public")
usuario=table("usuario",column("id",BigInteger),column("rol_id",BigInteger),
    column("correo",String),column("password_hash",String),
    column("fecha_creacion",DateTime(timezone=True)),
    column("estado",Boolean),schema="public")
empleado=table("empleado",column("id",BigInteger),
    column("empresa_id",BigInteger),column("usuario_id",BigInteger),
    column("sucursal_id",BigInteger),column("nombres",String),
    column("apellidos",String),column("ci",String),
    column("telefono",String),column("fecha_contratacion",Date),
    column("estado",Boolean),schema="public")
usuario_empresa=table("usuario_empresa",column("id",BigInteger),
    column("usuario_id",BigInteger),column("empresa_id",BigInteger),
    column("rol_id",BigInteger),column("estado",Boolean),schema="public")
_hasher=PasswordHasher()


def _rol_activo(db:Session,nombre:str)->int:
    rows=db.execute(select(rol.c.id).where(
        rol.c.nombre==nombre,rol.c.estado.is_(True))).scalars().all()
    if len(rows)!=1:
        raise SuperadminSaaSError(409,
            "Rol base del sistema no configurado: "+nombre)
    return int(rows[0])


def _empresa_libre_y_plan(db:Session,empresa_id:int):
    q=select(empresa.c.id).where(
        empresa.c.id==empresa_id,empresa.c.estado.is_(True)
    ).with_for_update(of=empresa)
    if db.scalar(q) is None:
        raise SuperadminSaaSError(404,"Empresa no disponible")
    # Únicamente primer bootstrap: jamás usarlo para otorgar
    # administradores posteriores o reemplazar una identidad perdida.
    if db.scalar(select(func.count()).select_from(usuario_empresa).where(
        usuario_empresa.c.empresa_id==empresa_id)):
        raise SuperadminSaaSError(409,
            "Empresa con membresías; utilizar administración propia CU04")
    if db.scalar(select(func.count()).select_from(sucursal).where(
        sucursal.c.empresa_id==empresa_id)):
        raise SuperadminSaaSError(409,
            "Empresa con sucursales; registro inicial no aplicable")
    hoy=date.today()
    plans=db.execute(
        select(plan.c.id,plan.c.max_sucursales_activas)
        .select_from(suscripcion.join(
            plan,plan.c.id==suscripcion.c.plan_saas_id))
        .where(suscripcion.c.empresa_id==empresa_id,
            suscripcion.c.estado=="ACTIVA",
            suscripcion.c.fecha_inicio<=hoy,
            suscripcion.c.fecha_vencimiento>=hoy,
            plan.c.estado.is_(True))
    ).mappings().all()
    if len(plans)!=1 or plans[0]["max_sucursales_activas"]<1:
        raise SuperadminSaaSError(409,
            "Empresa sin una suscripción vigente que admita sucursales")


def registrar_primer_administrador(
    db:Session,*,empresa_id:int,correo:str,password:str,nombres:str,
    apellidos:str,ci:str,ciudad_id:int,nombre_sucursal:str,
    direccion_sucursal:str,telefono:str|None,
)->dict:
    """No hace commit: el router confirma o revierte TODO.

    Cualquier fallo deja las filas sin commit para rollback del router.
    Los IDs se generan en DB y se devuelven con RETURNING.
    """
    # No calcular hashes ni abrir transacción SQL si la clave es inválida.
    try:
        validar_password_segura(password)
    except ValueError as exc:
        raise SuperadminSaaSError(422,str(exc)) from exc
    email=correo.strip().lower()
    documento=ci.strip()
    if not email or not documento or not nombres.strip() or not apellidos.strip():
        raise SuperadminSaaSError(422,"Datos del administrador incompletos")

    _empresa_libre_y_plan(db,empresa_id)
    city=db.scalar(select(ciudad.c.id).where(
        ciudad.c.id==ciudad_id,ciudad.c.estado.is_(True)))
    if city is None:
        raise SuperadminSaaSError(404,"Ciudad activa inexistente")
    cliente_role_id=_rol_activo(db,"CLIENTE")
    admin_role_id=_rol_activo(db,"ADMINISTRADOR")
    if db.scalar(select(usuario.c.id).where(
        func.lower(usuario.c.correo)==email)) is not None:
        raise SuperadminSaaSError(409,"Correo ya registrado")
    if db.scalar(select(empleado.c.id).where(
        empleado.c.empresa_id==empresa_id,
        func.lower(empleado.c.ci)==documento.lower())) is not None:
        raise SuperadminSaaSError(409,"Documento de empleado duplicado")
    branch=db.execute(insert(sucursal).values(
        empresa_id=empresa_id,ciudad_id=ciudad_id,nombre=nombre_sucursal,
        direccion=direccion_sucursal,telefono=telefono,estado=True
    ).returning(sucursal.c.id)).scalar_one()
    account=db.execute(insert(usuario).values(
        rol_id=cliente_role_id,correo=email,
        password_hash=_hasher.hash(password),
        fecha_creacion=datetime.now(timezone.utc),estado=True
    ).returning(usuario.c.id)).scalar_one()
    employee=db.execute(insert(empleado).values(
        empresa_id=empresa_id,usuario_id=account,sucursal_id=branch,
        nombres=nombres.strip(),apellidos=apellidos.strip(),
        ci=documento,telefono=telefono,
        fecha_contratacion=date.today(),estado=True
    ).returning(empleado.c.id)).scalar_one()
    membership=db.execute(insert(usuario_empresa).values(
        empresa_id=empresa_id,usuario_id=account,rol_id=admin_role_id,
        estado=True
    ).returning(usuario_empresa.c.id)).scalar_one()
    # Solo IDs e información no sensible: NUNCA password/password_hash.
    return {"empresa_id":empresa_id,"usuario_id":account,
            "empleado_id":employee,"membresia_id":membership,
            "sucursal_id":branch,"correo":email,
            "rol_empresa":"ADMINISTRADOR"}
