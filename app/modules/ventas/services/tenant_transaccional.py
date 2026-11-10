"""CU13 SaaS: preflight y ejecucion de los SP tenant de venta/pago.

No usa los procedimientos legacy (sp_confirmar_venta) porque omiten la
validacion de empresa. Exige un principal SQL restringido, RLS efectiva y un
SP SECURITY DEFINER con propietario NOLOGIN auditado.
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.tenant_context import TenantContext
from app.modules.reservas.services.tenant_reserva_transaccional_5al import (
    _SP_DEFINER_SQL_META,
    _VERIFICADOR_ACTOR_SQL_META,
    _VERIFICADOR_ACTOR_SQL_REAL,
)

PROCEDIMIENTOS_VENTA = frozenset({
    "sp_crear_venta_presencial_tenant",
    "sp_registrar_pago_presencial_tenant",
    "sp_crear_venta_digital_tenant",
    "sp_registrar_intencion_stripe_tenant",
})


class VentaTenantDeshabilitada(RuntimeError):
    """No hay un principal SQL tenant con RLS y SP auditados."""


class VentaTenantIntegridadError(RuntimeError):
    """El resultado del SP no coincide con el tenant/sucursal esperado."""


_PRINCIPAL_SQL = text("""
    SELECT session_user AS principal,
           r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolbypassrls
             AND NOT r.rolcreaterole AND NOT r.rolcreatedb
             AND r.rolinherit
             AND pg_has_role(r.rolname,'fashionstore_api','USAGE')
             AND NOT EXISTS (
               SELECT 1
               FROM pg_auth_members m
               JOIN pg_roles heredado ON heredado.oid=m.roleid
               WHERE m.member=r.oid
                 AND heredado.rolname<>'fashionstore_api'
             ) AS restringido,
           row_security_active('public.empresa'::regclass) AS rls_empresas,
           row_security_active('public.venta'::regclass) AS rls_ventas,
           row_security_active('public.inventario'::regclass) AS rls_inventario,
           row_security_active('public.reserva'::regclass) AS rls_reservas,
           (SELECT count(*) FROM public.empresa) AS empresas_visibles,
           (SELECT count(*) FROM public.empresa e
             WHERE e.id=:empresa_id AND e.slug=:slug) AS empresa_esperada_visible,
           (SELECT count(*)
             FROM public.suscripcion sus
             JOIN public.plan_saas pl ON pl.id=sus.plan_saas_id
             WHERE sus.empresa_id=:empresa_id
               AND sus.estado='ACTIVA' AND pl.estado IS TRUE
               AND pl.permite_ventas IS TRUE
               AND (timezone('America/La_Paz', now()))::date
                   BETWEEN sus.fecha_inicio AND sus.fecha_vencimiento
           ) AS planes_venta_vigentes,
           (SELECT count(*)
             FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='public'
               AND p.proname=:procedimiento
               AND p.prokind='p'
               AND NOT p.prosecdef
               AND has_function_privilege(session_user,p.oid,'EXECUTE')
           ) AS procedimientos_autorizados
    FROM pg_roles r WHERE r.rolname=session_user
""")


def verificar_principal_ventas(
    db: Session, tenant: TenantContext, procedimiento: str
) -> str:
    if procedimiento not in PROCEDIMIENTOS_VENTA:
        raise ValueError("Procedimiento tenant de venta no permitido")
    if not isinstance(tenant, TenantContext):
        raise TypeError("Se requiere contexto JWT de tenant validado")
    if (
        type(tenant.empresa_id) is not int
        or tenant.empresa_id <= 0
        or not tenant.empresa_slug
    ):
        raise ValueError("Empresa de tenant invalida")

    fila = db.execute(
        _PRINCIPAL_SQL,
        {
            "empresa_id": tenant.empresa_id,
            "slug": tenant.empresa_slug,
            "procedimiento": procedimiento,
        },
    ).mappings().one_or_none()

    if (
        not fila
        or not all(
            fila.get(clave) is True
            for clave in (
                "restringido",
                "rls_empresas",
                "rls_ventas",
                "rls_inventario",
                "rls_reservas",
            )
        )
        or fila["empresas_visibles"] != 1
        or fila["empresa_esperada_visible"] != 1
        or fila["planes_venta_vigentes"] != 1
        or not isinstance(fila["principal"], str)
        or not fila["principal"]
    ):
        raise VentaTenantDeshabilitada(
            "Rol SQL de venta no aislado o procedimiento no autorizado"
        )

    sp_owner = settings.saas_sp_sql_owner
    if fila["procedimientos_autorizados"] == 1:
        if sp_owner:
            raise VentaTenantDeshabilitada(
                "SP esperado SECURITY DEFINER pero sigue INVOKER"
            )
    elif fila["procedimientos_autorizados"] == 0:
        if (
            not isinstance(sp_owner, str)
            or not sp_owner
            or sp_owner == fila["principal"]
            or sp_owner == settings.saas_actor_sql_owner
        ):
            raise VentaTenantDeshabilitada(
                "SP SECURITY DEFINER sin propietario validado"
            )
        meta = db.execute(
            _SP_DEFINER_SQL_META, {"procedimiento": procedimiento}
        ).mappings().one_or_none()
        if (
            not meta
            or meta.get("propietario") != sp_owner
            or not all(
                meta.get(clave) is True
                for clave in (
                    "security_definer",
                    "path_seguro",
                    "puede_ejecutar",
                    "propietario_restringido",
                    "owner_sin_ddl",
                    "llamador_ajeno_al_propietario",
                    "llamador_sin_ddl",
                    "publico_revocado",
                )
            )
        ):
            raise VentaTenantDeshabilitada(
                "SP SECURITY DEFINER inseguro o sin permisos"
            )
    else:
        raise VentaTenantDeshabilitada(
            "SP con cantidad de autorizaciones inesperada"
        )

    propietario_esperado = settings.saas_actor_sql_owner
    if (
        not isinstance(propietario_esperado, str)
        or not propietario_esperado
        or propietario_esperado == fila["principal"]
    ):
        raise VentaTenantDeshabilitada("Identidad SQL individual no configurada")
    meta = db.execute(_VERIFICADOR_ACTOR_SQL_META).mappings().one_or_none()
    if (
        not meta
        or meta.get("propietario") != propietario_esperado
        or not all(
            meta.get(clave) is True
            for clave in (
                "security_definer",
                "path_seguro",
                "puede_ejecutar",
                "propietario_restringido",
                "llamador_sin_ddl",
                "llamador_ajeno_al_propietario",
                "publico_revocado",
            )
        )
    ):
        raise VentaTenantDeshabilitada(
            "Verificador SQL individual no instalado o inseguro"
        )
    actor_ok = db.scalar(
        _VERIFICADOR_ACTOR_SQL_REAL,
        {
            "empresa_id": tenant.empresa_id,
            "usuario_id": tenant.usuario_id,
        },
    )
    if actor_ok is not True:
        raise VentaTenantDeshabilitada(
            "El principal SQL no acredita la identidad JWT"
        )
    return fila["principal"]


def _items_json(items: list[tuple[int, int]]) -> str:
    return json.dumps(
        [
            {"inventario_id": int(inventario_id), "cantidad": int(cantidad)}
            for inventario_id, cantidad in items
        ],
        separators=(",", ":"),
    )


def _verificar_venta_creada(
    db: Session,
    tenant: TenantContext,
    *,
    venta_id: int,
    reserva_id: int | None,
) -> None:
    if type(venta_id) is not int or venta_id <= 0:
        raise VentaTenantIntegridadError("SP no devolvio una venta valida")
    fila = db.execute(
        text("""
            SELECT ve.id, ve.estado, ve.canal, ve.reserva_id, ve.sucursal_id
            FROM public.venta ve
            WHERE ve.id=:venta_id AND ve.empresa_id=:empresa_id
        """),
        {"venta_id": venta_id, "empresa_id": tenant.empresa_id},
    ).mappings().one_or_none()
    if fila is None:
        raise VentaTenantIntegridadError("Venta no pertenece al tenant")
    if (
        fila["estado"] != "PENDIENTE"
        or fila["canal"] != "PRESENCIAL"
        or fila["reserva_id"] != reserva_id
    ):
        raise VentaTenantIntegridadError("Venta devuelta inconsistente")


def crear_venta_transaccional_tenant(
    db: Session,
    *,
    tenant: TenantContext,
    reserva_id: int | None,
    cliente_id: int | None,
    items: list[tuple[int, int]],
) -> int:
    verificar_principal_ventas(
        db, tenant, "sp_crear_venta_presencial_tenant"
    )
    resultado = db.execute(
        text(
            "CALL public.sp_crear_venta_presencial_tenant("
            ":empresa_id, :usuario_id, :reserva_id, :cliente_id, "
            "CAST(:items AS jsonb), :venta_id)"
        ),
        {
            "empresa_id": tenant.empresa_id,
            "usuario_id": tenant.usuario_id,
            "reserva_id": reserva_id,
            "cliente_id": cliente_id,
            "items": _items_json(items),
            "venta_id": None,
        },
    ).one_or_none()
    if resultado is None or type(resultado[0]) is not int or resultado[0] <= 0:
        raise VentaTenantIntegridadError("SP no devolvio una venta valida")
    venta_id = int(resultado[0])
    _verificar_venta_creada(
        db, tenant, venta_id=venta_id, reserva_id=reserva_id
    )
    return venta_id


def registrar_pago_transaccional_tenant(
    db: Session,
    *,
    tenant: TenantContext,
    venta_id: int,
    metodo: str,
) -> int:
    verificar_principal_ventas(
        db, tenant, "sp_registrar_pago_presencial_tenant"
    )
    resultado = db.execute(
        text(
            "CALL public.sp_registrar_pago_presencial_tenant("
            ":empresa_id, :venta_id, :usuario_id, :metodo, :pago_id)"
        ),
        {
            "empresa_id": tenant.empresa_id,
            "venta_id": venta_id,
            "usuario_id": tenant.usuario_id,
            "metodo": metodo,
            "pago_id": None,
        },
    ).one_or_none()
    if resultado is None or type(resultado[0]) is not int or resultado[0] <= 0:
        raise VentaTenantIntegridadError("SP no devolvio un pago valido")
    pago_id = int(resultado[0])

    fila = db.execute(
        text("""
            SELECT pa.id AS pago_id, pa.estado AS pago_estado,
                   pa.venta_id, pa.monto, ve.estado AS venta_estado,
                   ve.reserva_id
            FROM public.pago pa
            JOIN public.venta ve
              ON ve.id=pa.venta_id AND ve.empresa_id=pa.empresa_id
            WHERE pa.id=:pago_id AND pa.empresa_id=:empresa_id
              AND pa.venta_id=:venta_id
        """),
        {
            "pago_id": pago_id,
            "empresa_id": tenant.empresa_id,
            "venta_id": venta_id,
        },
    ).mappings().one_or_none()
    if fila is None:
        raise VentaTenantIntegridadError("Pago no pertenece al tenant")
    if fila["pago_estado"] != "APROBADO" or fila["venta_estado"] != "COMPLETADA":
        raise VentaTenantIntegridadError("Pago/venta no quedaron confirmados")
    if fila["reserva_id"] is not None:
        estado = db.scalar(
            text("""
                SELECT re.estado FROM public.reserva re
                WHERE re.id=:reserva_id AND re.empresa_id=:empresa_id
            """),
            {
                "reserva_id": int(fila["reserva_id"]),
                "empresa_id": tenant.empresa_id,
            },
        )
        if estado != "ATENDIDA":
            raise VentaTenantIntegridadError("Reserva no quedo ATENDIDA")
    return pago_id
