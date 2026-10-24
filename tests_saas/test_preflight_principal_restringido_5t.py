"""B3/5T: 16 pruebas offline del preflight de principal PostgreSQL.

NO conecta ni modifica bases, no crea roles; simula metadatos PG.
"""
from pathlib import Path
from types import SimpleNamespace

from app.core.saas_principal_preflight_5t import (
    PrecondicionesRls5T, _evaluar_filas_5t,
    evaluar_precondiciones_rls_5t,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app/core/saas_principal_preflight_5t.py"


def filas_seguras():
    return [
        {
            "base": "fashionstore_saas_reservas_sandbox_5h",
            "login_sql": "saas_ensayo_limitado",
            "principal_sql": "saas_ensayo_limitado",
            "login_superuser": False,
            "login_bypassrls": False,
            "principal_superuser": False,
            "principal_bypassrls": False,
            "tabla": tabla,
            "rls_habilitado": True,
            "rls_aplicado": True,
            "propietario": "postgres",
            "hereda_privilegios_propietario": False,
            "politicas": 1,
        }
        for tabla in ("bitacora", "carrito", "inventario", "reserva")
    ]


class SesionFalsa:
    def __init__(self, rows=(), error=None):
        self.rows = rows
        self.error = error
        self.sentencias = []

    def execute(self, sentencia):
        self.sentencias.append(str(sentencia))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(all=lambda: self.rows)
        )


def test_5t_acepta_solo_precondiciones_de_ensayo_simuladas():
    out = _evaluar_filas_5t(filas_seguras())
    assert out == PrecondicionesRls5T(True, ())
    assert not hasattr(out, "aislamiento_certificado")


def test_5t_rechaza_sin_tablas():
    assert _evaluar_filas_5t([]).apto_para_ensayo is False


def test_5t_rechaza_si_falta_tabla_critica():
    out = _evaluar_filas_5t(filas_seguras()[:-1])
    assert out.apto_para_ensayo is False


def test_5t_rechaza_tabla_duplicada():
    rows = filas_seguras()
    rows[3]["tabla"] = "bitacora"
    assert not _evaluar_filas_5t(rows).apto_para_ensayo


def test_5t_rechaza_base_fuera_del_sandbox():
    rows = filas_seguras()
    rows[0]["base"] = "fashionstore_restore_20261023_193554_594"
    assert not _evaluar_filas_5t(rows).apto_para_ensayo


def test_5t_rechaza_postgres_superusuario():
    rows = filas_seguras()
    for row in rows:
        row["principal_sql"] = row["login_sql"] = "postgres"
        row["principal_superuser"] = row["login_superuser"] = True
    out = _evaluar_filas_5t(rows)
    assert not out.apto_para_ensayo
    assert any("SUPERUSER" in m for m in out.motivos)


def test_5t_rechaza_bypassrls_del_principal_actual():
    rows = filas_seguras()
    rows[0]["principal_bypassrls"] = True
    assert not _evaluar_filas_5t(rows).apto_para_ensayo


def test_5t_rechaza_set_role_desde_login_privilegiado():
    rows = filas_seguras()
    for row in rows:
        row["login_sql"] = "postgres"
        row["login_superuser"] = True
    out = _evaluar_filas_5t(rows)
    assert not out.apto_para_ensayo
    assert any("principal distinto" in m for m in out.motivos)


def test_5t_rechaza_propietario_de_tabla():
    rows = filas_seguras()
    rows[0]["propietario"] = "saas_ensayo_limitado"
    assert not _evaluar_filas_5t(rows).apto_para_ensayo


def test_5t_rechaza_privilegios_heredados_del_owner():
    rows = filas_seguras()
    rows[0]["hereda_privilegios_propietario"] = True
    assert not _evaluar_filas_5t(rows).apto_para_ensayo


def test_5t_rechaza_rls_habilitado_pero_no_efectivo():
    rows = filas_seguras()
    rows[1]["rls_aplicado"] = False
    assert not _evaluar_filas_5t(rows).apto_para_ensayo


def test_5t_rechaza_tabla_sin_policy():
    rows = filas_seguras()
    rows[0]["politicas"] = 0
    assert not _evaluar_filas_5t(rows).apto_para_ensayo


def test_5t_rechaza_flag_desconocido_null():
    rows = filas_seguras()
    rows[1]["principal_superuser"] = None
    assert not _evaluar_filas_5t(rows).apto_para_ensayo


def test_5t_evaluacion_con_sesion_falsa_solo_consulta_catalogo():
    db = SesionFalsa(filas_seguras())
    out = evaluar_precondiciones_rls_5t(db)
    assert out.apto_para_ensayo
    assert len(db.sentencias) == 1
    assert "FROM pg_class c" in db.sentencias[0]
    assert "row_security_active(c.oid)" in db.sentencias[0]
    assert "session_user AS login_sql" in db.sentencias[0]


def test_5t_error_sql_devuelve_denegacion_sin_fuga():
    db = SesionFalsa(error=RuntimeError("conexion-secreta-ensayo"))
    out = evaluar_precondiciones_rls_5t(db)
    assert not out.apto_para_ensayo
    assert "conexion-secreta-ensayo" not in str(out)


def test_5t_no_se_integra_ni_ejecuta_sql_al_importar():
    code = SOURCE.read_text(encoding="utf-8")
    assert "create_engine(" not in code
    assert "SessionLocal(" not in code
    assert "psycopg" not in code
    assert "CREATE ROLE" not in code
    assert "CREATE POLICY" not in code
    assert "GRANT ALL" not in code
    assert "app/core/database" not in code
    assert "evaluar_precondiciones_rls_5t(db)" not in (
        ROOT / "app/modules/autenticacion_seguridad/api/tenant_router.py"
    ).read_text(encoding="utf-8")
