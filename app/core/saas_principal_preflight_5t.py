"""B3/5T: precondiciones del principal SQL para un FUTURO ensayo RLS.

No crea roles/policies, no hace GRANT, no abre engine ni sesiones. Solo evalua
metadatos de la conexion SQL existente al ejecutarse explicitamente.
Esta funcion NO se conecta desde rutas, startup ni auth en 5T.
Aprobar el preflight NO certifica aislamiento empresarial ni autentica GUC.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


# Solo el sandbox local y un subconjunto comercial/bitacora. No usar en
# produccion como una garantia de seguridad de todos los recursos.
_BASE_SANDBOX_5T = "fashionstore_saas_reservas_sandbox_5h"
_TABLAS_CRITICAS_5T = frozenset({"carrito", "reserva", "inventario", "bitacora"})

# Consulta cerrada. No interpola identificadores del usuario, no lee filas
# comerciales ni informacion personal, ni modifica GUC o sesion.
_SQL_PRECONDICIONES_5T = text("""
    SELECT
      current_database() AS base,
      session_user AS login_sql,
      current_user AS principal_sql,
      sr.rolsuper AS login_superuser,
      sr.rolbypassrls AS login_bypassrls,
      r.rolsuper AS principal_superuser,
      r.rolbypassrls AS principal_bypassrls,
      c.relname AS tabla,
      c.relrowsecurity AS rls_habilitado,
      row_security_active(c.oid) AS rls_aplicado,
      pg_get_userbyid(c.relowner) AS propietario,
      pg_has_role(current_user, c.relowner, 'USAGE')
          AS hereda_privilegios_propietario,
      (SELECT count(*)
         FROM pg_policies p
        WHERE p.schemaname='public' AND p.tablename=c.relname)
          AS politicas
    FROM pg_class c
    JOIN pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_roles r ON r.rolname=current_user
    JOIN pg_roles sr ON sr.rolname=session_user
    WHERE n.nspname='public' AND c.relkind='r'
      AND c.relname IN ('carrito','reserva','inventario','bitacora')
    ORDER BY c.relname
""")


@dataclass(frozen=True, slots=True)
class PrecondicionesRls5T:
    """Dictamen preventivo, deliberadamente NO es 'seguridad aprobada'."""

    apto_para_ensayo: bool
    motivos: tuple[str, ...]


def _evaluar_filas_5t(filas: list[dict]) -> PrecondicionesRls5T:
    """Evaluacion pura, negativa ante NULL, incoherencias o filas ausentes."""
    motivos: list[str] = []
    if len(filas) != len(_TABLAS_CRITICAS_5T):
        motivos.append("faltan o sobran tablas criticas")
    tablas = [f.get("tabla") for f in filas]
    if len(set(tablas)) != len(_TABLAS_CRITICAS_5T) or set(tablas) != _TABLAS_CRITICAS_5T:
        motivos.append("tablas criticas duplicadas o ausentes")

    if not filas:
        return PrecondicionesRls5T(False, tuple(motivos or ["sin datos SQL"]))

    primero = filas[0]
    for f in filas:
        if f.get("base") != _BASE_SANDBOX_5T:
            motivos.append("conexion fuera del sandbox")
        if (
            not isinstance(f.get("principal_sql"), str)
            or not f["principal_sql"]
            or f.get("login_sql") != f.get("principal_sql")
        ):
            motivos.append("principal distinto al login SQL o indefinido")
        if any(
            f.get(k) is not False for k in (
                "login_superuser", "login_bypassrls",
                "principal_superuser", "principal_bypassrls",
            )
        ):
            motivos.append("principal de login o sesion con SUPERUSER/BYPASSRLS")
        if f.get("propietario") == f.get("principal_sql") or (
            f.get("hereda_privilegios_propietario") is not False
        ):
            motivos.append("propietario o heredero de privilegios del propietario")
        if f.get("rls_habilitado") is not True or f.get("rls_aplicado") is not True:
            motivos.append("RLS no efectivo")
        n = f.get("politicas")
        if type(n) is not int or n < 1:
            motivos.append("sin policies para tabla critica")
        # Evita mezclar lecturas de sesiones diferentes en una evaluacion.
        for k in (
            "base", "login_sql", "principal_sql", "login_superuser",
            "login_bypassrls", "principal_superuser", "principal_bypassrls",
        ):
            if f.get(k) != primero.get(k):
                motivos.append("filas de distintos principales SQL")

    return PrecondicionesRls5T(not motivos, tuple(dict.fromkeys(motivos)))


def evaluar_precondiciones_rls_5t(db: Session) -> PrecondicionesRls5T:
    """Ejecuta SELECT de catalogo UNICAMENTE al invocarse explicitamente.

    No usa metadatos como prueba de autorizacion; los ensayos tenant A/B y
    las politicas correctas siguen obligatorios incluso si devuelve True.
    """
    try:
        filas = db.execute(_SQL_PRECONDICIONES_5T).mappings().all()
    except Exception:
        # Solo un dictamen genérico: nunca filtrar SQL/credenciales a HTTP.
        return PrecondicionesRls5T(False, ("no se pudo consultar catalogo SQL",))
    try:
        return _evaluar_filas_5t([dict(row) for row in filas])
    except (TypeError, ValueError, KeyError):
        return PrecondicionesRls5T(False, ("metadatos SQL inesperados",))
