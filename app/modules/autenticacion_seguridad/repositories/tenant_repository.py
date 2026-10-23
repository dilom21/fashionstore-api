"""Consultas multitenant para autenticacion (solo se usan con flag SaaS activo).

La consulta usa SQL parametrizado, nunca concatenacion de IDs o slugs.
Requiere la expansion del esquema empresarial del paso posterior.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

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
      AND (:empresa_id IS NULL OR e.id = CAST(:empresa_id AS bigint))
      AND (:empresa_slug IS NULL OR e.slug = CAST(:empresa_slug AS text))
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
