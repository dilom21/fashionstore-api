"""CU04 SaaS: membresías/roles de la empresa autenticada.

EXCLUSIVAMENTE la empresa_id verificada del JWT y membresía por
get_current_tenant_admin. Nunca recibir empresa_id del request.
No reutilizar /usuarios ni /roles globales del primer parcial en modo SaaS.

Los roles/permiso del esquema histórico son GLOBALES; no mutar rol,
rol_funcion ni usuario global aquí: sólo asignar/revocar la membresía
local ue.rol_id / ue.estado. La creación de empleados, rol personalizado
por tenant y matriz de permisos propia quedan pendientes del modelo v2.

Para cambios: SELECT ... FOR UPDATE sobre fila de empresa PRIMERO,
para serializar cambios administrativos concurrentes; luego membresía.
No dejar al tenant sin administrador, ni permitir al admin revocar su
propio acceso. La transacción/commit pertenece al router.

IMPORTANTE: JWT+membresía Python no es por sí solo RLS SQL:
conexiones y GRANTs del modo productivo siguen BLOQUEADOS por flags.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Boolean, String, and_, column, exists, func, or_, select,
    table, update,
)
from sqlalchemy.orm import Session


class GestionMembresiaTenantError(Exception):
    def __init__(self, codigo: int, detalle: str):
        super().__init__(detalle)
        self.codigo = codigo
        self.detalle = detalle


empresa = table("empresa",
    column("id", BigInteger), column("estado", Boolean), schema="public")
usuario_empresa = table("usuario_empresa",
    column("id", BigInteger), column("empresa_id", BigInteger),
    column("usuario_id", BigInteger), column("rol_id", BigInteger),
    column("estado", Boolean), schema="public")
usuario = table("usuario", column("id", BigInteger),
    column("correo", String), column("estado", Boolean),schema="public")
rol = table("rol", column("id", BigInteger),
    column("nombre", String), column("estado", Boolean),schema="public")
empleado = table("empleado",column("id", BigInteger),
    column("usuario_id", BigInteger), column("empresa_id", BigInteger),
    column("sucursal_id", BigInteger),column("nombres", String),
    column("apellidos", String),column("estado",Boolean),schema="public")
sucursal = table("sucursal",column("id",BigInteger),
    column("empresa_id",BigInteger),column("estado",Boolean),
    schema="public")

ROLES_BASE=frozenset({
    "ADMINISTRADOR","ENCARGADO_SUCURSAL","CAJERO","CLIENTE"
})


def _empresa_administrable(db:Session,empresa_id:int,*,bloqueo:bool):
    # Escrituras: un lock maestro por empresa serializa dos cambios
    # simultáneos que podrían revocar dos admins distintos.
    query=select(empresa.c.id).where(
        empresa.c.id==empresa_id,empresa.c.estado.is_(True))
    if bloqueo:
        query=query.with_for_update(of=empresa)
    existente=db.scalar(query)
    if existente is None:
        raise GestionMembresiaTenantError(404,"Empresa no disponible")


def _lista(db:Session,empresa_id:int,usuario_id:int|None=None):
    statement=(select(
        usuario_empresa.c.usuario_id,
        usuario.c.correo,
        usuario.c.estado.label("usuario_activo"),
        usuario_empresa.c.estado.label("membresia_activa"),
        usuario_empresa.c.rol_id,rol.c.nombre.label("rol"),
        empleado.c.id.label("empleado_id"),
        empleado.c.sucursal_id,
        empleado.c.nombres,
        empleado.c.apellidos,
        empleado.c.estado.label("empleado_activo"),
    ).select_from(
        usuario_empresa.join(
            usuario,usuario.c.id==usuario_empresa.c.usuario_id
        ).join(
            rol,rol.c.id==usuario_empresa.c.rol_id
        ).outerjoin(
            empleado,and_(
                empleado.c.usuario_id==usuario_empresa.c.usuario_id,
                empleado.c.empresa_id==usuario_empresa.c.empresa_id,
            )
        )
    ).where(usuario_empresa.c.empresa_id==empresa_id)
     .order_by(usuario_empresa.c.usuario_id))
    if usuario_id is not None:
        statement=statement.where(usuario_empresa.c.usuario_id==usuario_id)
    return [dict(x) for x in db.execute(statement).mappings().all()]


def _membresia(db:Session,empresa_id:int,usuario_id:int):
    statement=(
        select(
            usuario_empresa.c.id,usuario_empresa.c.usuario_id,
            usuario_empresa.c.estado,usuario_empresa.c.rol_id,
            rol.c.nombre.label("rol_nombre")
        ).select_from(usuario_empresa.join(
            rol,rol.c.id==usuario_empresa.c.rol_id))
        .where(usuario_empresa.c.empresa_id==empresa_id,
               usuario_empresa.c.usuario_id==usuario_id)
        .with_for_update(of=usuario_empresa)
    )
    fila=db.execute(statement).mappings().one_or_none()
    if fila is None:
        raise GestionMembresiaTenantError(
            404,"Membresía de usuario no encontrada en esta empresa")
    return dict(fila)


def _cantidad_otros_administradores(db:Session,empresa_id:int,
                                    excluir_usuario_id:int):
    # Contar solamente otros admins realmente capaces de iniciar
    # sesión personal: cuenta/rol/membresía/empleado/sucursal activos.
    statement=select(func.count()).select_from(
        usuario_empresa.join(
            rol,rol.c.id==usuario_empresa.c.rol_id
        ).join(
            usuario,usuario.c.id==usuario_empresa.c.usuario_id
        ).join(
            empleado,and_(empleado.c.usuario_id==usuario_empresa.c.usuario_id,
                empleado.c.empresa_id==usuario_empresa.c.empresa_id)
        ).join(
            sucursal,and_(sucursal.c.id==empleado.c.sucursal_id,
                 sucursal.c.empresa_id==usuario_empresa.c.empresa_id)
        )
    ).where(
        usuario_empresa.c.empresa_id==empresa_id,
        usuario_empresa.c.usuario_id!=excluir_usuario_id,
        usuario_empresa.c.estado.is_(True),
        usuario.c.estado.is_(True),
        rol.c.estado.is_(True),
        rol.c.nombre=="ADMINISTRADOR",
        empleado.c.estado.is_(True),
        sucursal.c.estado.is_(True),
    )
    return int(db.scalar(statement) or 0)


def _evitar_sin_administrador(db:Session,empresa_id:int,usuario_id:int):
    if _cantidad_otros_administradores(db,empresa_id,usuario_id)<1:
        raise GestionMembresiaTenantError(
            409,"La empresa debe conservar al menos un administrador activo")


class GestionMembresiasTenantRepository:
    @staticmethod
    def listar(db:Session,empresa_id:int):
        _empresa_administrable(db,empresa_id,bloqueo=False)
        return _lista(db,empresa_id)

    @staticmethod
    def roles_disponibles(db:Session,empresa_id:int):
        _empresa_administrable(db,empresa_id,bloqueo=False)
        # Roles de base o YA utilizados por este tenant. No mostrar
        # roles globales creados exclusivamente por otra empresa.
        used=exists(select(usuario_empresa.c.id).where(
            usuario_empresa.c.rol_id==rol.c.id,
            usuario_empresa.c.empresa_id==empresa_id
        ))
        query=(select(rol.c.id,rol.c.nombre,rol.c.estado)
            .where(rol.c.estado.is_(True),
                or_(rol.c.nombre.in_(ROLES_BASE),used))
            .order_by(rol.c.nombre))
        return [dict(r) for r in db.execute(query).mappings().all()]

    @staticmethod
    def cambiar_rol(db:Session,*,empresa_id:int,actor_id:int,
                    usuario_id:int,rol_id:int):
        _empresa_administrable(db,empresa_id,bloqueo=True)
        actual=_membresia(db,empresa_id,usuario_id)
        if actual["rol_id"]==rol_id:
            rows=_lista(db,empresa_id,usuario_id)
            return rows[0]
        if actor_id==usuario_id:
            raise GestionMembresiaTenantError(
                403,"No puede cambiar su propio rol empresarial")
        # Estos endpoints sólo gestionan personal existente. No
        # habilitan creación de usuarios globales ni perfiles CLIENTE.
        habilitado=db.scalar(select(empleado.c.id).where(
            empleado.c.empresa_id==empresa_id,
            empleado.c.usuario_id==usuario_id,
            empleado.c.estado.is_(True)))
        if habilitado is None:
            raise GestionMembresiaTenantError(
                409,"Usuario sin empleado activo en esta empresa")
        usados=exists(select(usuario_empresa.c.id).where(
            usuario_empresa.c.empresa_id==empresa_id,
            usuario_empresa.c.rol_id==rol_id))
        nuevo=db.execute(
            select(rol.c.id,rol.c.nombre).where(
                rol.c.id==rol_id,
                rol.c.estado.is_(True),
                or_(rol.c.nombre.in_(ROLES_BASE),usados))
        ).mappings().one_or_none()
        if nuevo is None or nuevo["nombre"]=="CLIENTE":
            raise GestionMembresiaTenantError(
                422,"Rol no asignable al personal de esta empresa")
        if actual["rol_nombre"]=="ADMINISTRADOR" and (
            nuevo["nombre"]!="ADMINISTRADOR" and actual["estado"]
        ):
            _evitar_sin_administrador(db,empresa_id,usuario_id)
        db.execute(update(usuario_empresa).where(
            usuario_empresa.c.id==actual["id"],
            usuario_empresa.c.empresa_id==empresa_id
        ).values(rol_id=rol_id))
        return _lista(db,empresa_id,usuario_id)[0]

    @staticmethod
    def cambiar_estado(db:Session,*,empresa_id:int,actor_id:int,
                       usuario_id:int,estado:bool):
        _empresa_administrable(db,empresa_id,bloqueo=True)
        actual=_membresia(db,empresa_id,usuario_id)
        if actual["estado"]==estado:
            return _lista(db,empresa_id,usuario_id)[0]
        if actor_id==usuario_id:
            raise GestionMembresiaTenantError(
                403,"No puede desactivar su propia membresía")
        if not estado and actual["rol_nombre"]=="ADMINISTRADOR":
            _evitar_sin_administrador(db,empresa_id,usuario_id)
        db.execute(update(usuario_empresa).where(
            usuario_empresa.c.id==actual["id"],
            usuario_empresa.c.empresa_id==empresa_id
        ).values(estado=estado))
        return _lista(db,empresa_id,usuario_id)[0]
