"""Consultas multitenant para autenticacion (solo se usan con flag SaaS activo).

La consulta usa SQL parametrizado, nunca concatenacion de IDs o slugs.
Requiere la expansion del esquema empresarial del paso posterior.
"""
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class CredencialesBootstrapTenant:
    """Hash solo para verificar Argon2 en backend; nunca incluir en HTTP/JWT."""

    usuario_id: int
    password_hash: str = field(repr=False)


# 5R: lectura mínima antes de conocer la empresa; no carga Usuario ORM,
# Rol ni otras columnas. No exponer esta consulta a clientes SQL directos.
# AUN NECESITA permisos RLS/ACL restringidos que NO se instalan aquí.
_SQL_CREDENCIALES_BOOTSTRAP_TENANT = text(
    """
    SELECT u.id AS usuario_id, u.password_hash
    FROM public.usuario AS u
    WHERE u.correo = :correo
      AND u.estado IS TRUE
    LIMIT 2
    """
)

_SQL_CONTEXTO_TENANT = text(
    """
    SELECT
        ue.usuario_id,
        e.id AS empresa_id,
        e.slug AS empresa_slug,
        e.nombre_comercial AS empresa_nombre,
        ue.rol_id,
        r.nombre AS rol_nombre,
        p.codigo AS plan_codigo,
        p.permite_reportes_ia
    FROM public.usuario_empresa AS ue
    JOIN public.usuario AS u ON u.id = ue.usuario_id
    JOIN public.empresa AS e ON e.id = ue.empresa_id
    JOIN public.rol AS r ON r.id = ue.rol_id
    JOIN public.suscripcion AS s ON s.empresa_id = e.id
    JOIN public.plan_saas AS p ON p.id = s.plan_saas_id
    WHERE ue.usuario_id = :usuario_id
      AND (CAST(:empresa_id AS bigint) IS NULL OR e.id = CAST(:empresa_id AS bigint))
      AND (CAST(:empresa_slug AS text) IS NULL OR e.slug = CAST(:empresa_slug AS text))
      AND u.estado IS TRUE
      AND ue.estado IS TRUE
      AND e.estado IS TRUE
      AND r.estado IS TRUE
      AND p.estado IS TRUE
      AND s.estado = 'ACTIVA'
      AND (timezone('America/La_Paz', now()))::date
          BETWEEN s.fecha_inicio AND s.fecha_vencimiento
      AND (
          (CAST(:contexto AS text) = 'cliente'
           AND EXISTS (
                SELECT 1 FROM public.cliente c
                WHERE c.usuario_id = u.id AND c.empresa_id = e.id
                  AND c.estado IS TRUE
           ))
          OR
          (CAST(:contexto AS text) = 'personal'
           AND EXISTS (
                SELECT 1 FROM public.empleado em
                JOIN public.sucursal su ON su.id = em.sucursal_id
                WHERE em.usuario_id = u.id AND em.empresa_id = e.id
                  AND su.empresa_id = e.id
                  AND em.estado IS TRUE
           ))
      )
    LIMIT 2
    """
)


class TenantAuthRepository:
    @staticmethod
    def obtener_credenciales_bootstrap(
        db: Session, correo: str
    ) -> CredencialesBootstrapTenant | None:
        """Proyección mínima: solo usuario_id/hash; sin seleccionar el ORM entero.

        La contraseña se verifica en AuthService, nunca aquí.
        Los permisos de tabla/columna y RLS para un rol bootstrap REAL
        siguen pendientes; SQL parametrizado no sustituye esos controles.
        """
        correo_normalizado = correo.strip().lower()
        if not correo_normalizado:
            return None
        rows = db.execute(
            _SQL_CREDENCIALES_BOOTSTRAP_TENANT,
            {"correo": correo_normalizado},
        ).mappings().all()
        # Fail closed: un usuario activo por correo como máximo.
        if len(rows) != 1:
            return None
        row = rows[0]
        return CredencialesBootstrapTenant(
            usuario_id=int(row["usuario_id"]),
            password_hash=str(row["password_hash"]),
        )

    @staticmethod
    def obtener_membresia_vigente(
        db: Session,
        *,
        usuario_id: int,
        contexto: str,
        empresa_id: int | None = None,
        empresa_slug: str | None = None,
    ) -> dict | None:
        if (empresa_id is None) == (empresa_slug is None):
            raise ValueError("Indicar exactamente una empresa por ID o slug")
        rows = db.execute(
            _SQL_CONTEXTO_TENANT,
            {
                "usuario_id": usuario_id,
                "empresa_id": empresa_id,
                "empresa_slug": empresa_slug,
                "contexto": contexto,
            },
        ).mappings().all()
        # Una empresa debe tener una sola suscripcion vigente y una membresia.
        if len(rows) != 1:
            return None
        return dict(rows[0])
