"""B3/5AL: adaptador transaccional CU16, deliberadamente SIN router HTTP.

Preparado para un despliegue futuro de PostgreSQL compartido con PRINCIPAL SQL
autenticado POR TENANT, RLS efectiva, grants y SP CU16 instalados y auditados.
No sustituye la autorización JWT de get_current_tenant_cliente ni convierte
el constructor 5O en autenticación. No llamar sin dependencia FastAPI segura.

Sin registro en app/main.py ni allowlist: ruta POST /tenant/reservas sigue 503.
Nunca conecta por sí mismo, no concede permisos ni instala procedimientos.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.tenant_context import TenantContext
from app.modules.reservas.services.tenant_sp_calls_5o import (
    preparar_creacion_reserva,
)


class ReservaTenantDeshabilitada(RuntimeError):
    """No hay una conexión tenant con RLS y permisos CU16 validados."""


class ReservaTenantIntegridadError(RuntimeError):
    """El resultado del SP no coincide con el carrito/cliente/empresa esperado."""


_PRINCIPAL_SQL = text("""
    SELECT session_user AS principal,
           r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolbypassrls
             AND NOT r.rolcreaterole AND NOT r.rolcreatedb
             AND NOT r.rolinherit AS restringido,
           row_security_active('public.empresa'::regclass) AS rls_empresas,
           row_security_active('public.reserva'::regclass) AS rls_reservas,
           row_security_active('public.inventario'::regclass) AS rls_inventario,
           (SELECT count(*) FROM public.empresa) AS empresas_visibles,
           (SELECT count(*) FROM public.empresa e
             WHERE e.id=:empresa_id AND e.slug=:slug) AS empresa_esperada_visible,
           (SELECT count(*)
             FROM public.suscripcion sus
             JOIN public.plan_saas pl ON pl.id=sus.plan_saas_id
             WHERE sus.empresa_id=:empresa_id
               AND sus.estado='ACTIVA' AND pl.estado IS TRUE
               AND pl.permite_reservas IS TRUE
               AND (timezone('America/La_Paz', now()))::date
                   BETWEEN sus.fecha_inicio AND sus.fecha_vencimiento
           ) AS planes_reserva_vigentes,
           (SELECT count(*)
             FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='public'
               AND p.proname='sp_crear_reserva_tenant'
               AND p.prokind='p'
               AND NOT p.prosecdef
               AND has_function_privilege(session_user,p.oid,'EXECUTE')
           ) AS procedimientos_autorizados
    FROM pg_roles r WHERE r.rolname=session_user
""")

_RESERVA_CONFIRMADA = text("""
    SELECT r.id
    FROM public.reserva r
    JOIN public.cliente c ON c.id=r.cliente_id
      AND c.empresa_id=r.empresa_id
      AND c.usuario_id=:usuario_id AND c.estado IS TRUE
    JOIN public.carrito ca ON ca.id=r.carrito_id
      AND ca.empresa_id=r.empresa_id
      AND ca.cliente_id=c.id
    WHERE r.id=:reserva_id
      AND r.empresa_id=:empresa_id
      AND r.carrito_id=:carrito_id
""")


def verificar_principal_reserva(db: Session, tenant: TenantContext) -> str:
    """Fail-closed: SQL principal real debe estar restringido al MISMO tenant.

    Se requiere exactamente una empresa visible con RLS vigente. Eso descarta
    conexiones superusuario/BYPASSRLS y servicios de SQL principal compartido
    con visibilidad global. No intenta demostrar por si solo identidad de
    usuario, que corresponde a JWT+membresía y al SP real.
    """
    if not isinstance(tenant, TenantContext):
        raise TypeError("Se requiere contexto JWT de tenant validado")
    if (type(tenant.empresa_id) is not int or tenant.empresa_id <= 0
            or not tenant.empresa_slug):
        raise ValueError("Empresa de tenant invalida")
    r = db.execute(_PRINCIPAL_SQL, {
        "empresa_id": tenant.empresa_id,
        "slug": tenant.empresa_slug,
    }).mappings().one_or_none()

    if not r or not all(
        r.get(key) is True for key in (
            "restringido", "rls_empresas", "rls_reservas", "rls_inventario"
        )
    ) or (
        r["empresas_visibles"] != 1
        or r["empresa_esperada_visible"] != 1
        or r["planes_reserva_vigentes"] != 1
        or r["procedimientos_autorizados"] != 1
        or not isinstance(r["principal"], str)
        or not r["principal"]
    ):
        raise ReservaTenantDeshabilitada(
            "Rol SQL de reserva no aislado o procedimiento CU16 no autorizado"
        )
    return r["principal"]


def crear_reserva_transaccional_sin_exposicion_http(
    db: Session,
    *,
    tenant: TenantContext,
    carrito_id: int,
    fecha_atencion: datetime,
    observacion: str | None = None,
) -> int:
    """CU16 preparado, pero TODAVIA NO CONECTADO a FastAPI.

    Solo una futura ruta con JWT/membresía real y planes verificados llamará
    esta función tras instalar y ensayar el SP tenant CU16. No usar en la
    instancia actual ni en las 5 empresas originales hasta superar aceptación.

    Una sola transacción DB: preflight -> CALL -> relectura de ownership ->
    COMMIT; ante cualquier fallo ROLLBACK. La reserva nunca puede asociarse
    a empresa/cliente/carrito distintos, incluso si el SP retorna un ID.
    """
    llamada = preparar_creacion_reserva(
        tenant=tenant, carrito_id=carrito_id,
        fecha_atencion=fecha_atencion, observacion=observacion,
    )
    try:
        verificar_principal_reserva(db, tenant)
        resultado = db.execute(
            llamada.sentencia, dict(llamada.parametros)
        ).one_or_none()
        if resultado is None or type(resultado[0]) is not int or resultado[0] <= 0:
            raise ReservaTenantIntegridadError("Procedimiento CU16 no devolvió reserva válida")
        reserva_id = resultado[0]
        coincide = db.scalar(_RESERVA_CONFIRMADA, {
            "empresa_id": tenant.empresa_id,
            "usuario_id": tenant.usuario_id,
            "carrito_id": carrito_id,
            "reserva_id": reserva_id,
        })
        if coincide != reserva_id:
            raise ReservaTenantIntegridadError("Reserva devuelta no pertenece al tenant")
        db.commit()
        return reserva_id
    except Exception:
        db.rollback()
        raise
