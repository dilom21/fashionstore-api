"""B3/5X: 14 tests OFFLINE del preflight de identidad PostgreSQL fuente.

No se ejecuta PowerShell ni SQL, no se consulta DB real ni crea roles.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "database/saas/28_auditar_identidad_cluster_fuente_5x.sql"
PS = ROOT / "database/saas/28_auditar_identidad_cluster_fuente_5x.ps1"
DOC = ROOT / "database/saas/40_b3_p5x_identidad_cluster_pgdata_fuente_readonly.md"
CONFIG = ROOT / "app/core/config.py"
MANIFEST = ROOT / "database/saas/37_b3_p5u_laboratorio_rls_aislado_NO_EJECUTAR.json"


def sql():
    return SQL.read_text(encoding="utf-8")


def ps():
    return PS.read_text(encoding="utf-8")


def test_5x_transaccion_sql_exclusiva_de_lectura():
    s = sql()
    assert "BEGIN TRANSACTION READ ONLY;" in s
    assert "SET LOCAL statement_timeout" in s
    assert "SET LOCAL lock_timeout" in s
    assert "SET LOCAL row_security = on;" in s
    assert re.search(r"(?m)^ROLLBACK;$", s)


def test_5x_sql_no_ejecuta_ddl_dml_o_set_role():
    code = "\n".join(x for x in sql().splitlines()
                     if x.strip() and not x.strip().startswith("--"))
    assert not re.search(
        r"(?mi)^\s*(?:CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|"
        r"CALL|INSERT|UPDATE|DELETE|COMMIT|SET\s+ROLE)\b", code
    )


def test_5x_guard_de_host_puerto_y_base_exactos():
    s = sql()
    assert "current_database() <> 'fashionstore_saas_reservas_sandbox_5h'" in s
    assert "host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'" in s
    assert "inet_server_port() <> 5432" in s


def test_5x_principal_original_sin_set_role():
    s = sql()
    assert "session_user <> 'postgres' OR current_user <> 'postgres'" in s
    assert "session_user AS login_sql" in s
    assert "current_user AS principal_sql" in s


def test_5x_comprueba_version_18_e_identificador_real():
    s = sql()
    assert "current_setting('server_version_num')::int / 10000 <> 18" in s
    assert "FROM pg_control_system()" in s
    assert "system_identifier" in s
    assert "v_id IS NULL OR v_id <= 0" in s


def test_5x_pgdata_no_se_publica_en_resultados():
    s = sql()
    assert "current_setting('data_directory')" in s
    assert "RUTA_PGDATA_ORIGEN_NO_PUBLICADA" in s
    assert "v_pgdata IS NULL OR btrim(v_pgdata) = ''" in s
    assert "paths.fuente_pgdata" in s
    assert not re.search(r"(?im)^\s*SELECT\s+current_setting\('data_directory'\)", s)


def test_5x_compara_rutas_sin_comodines_like():
    s = sql()
    assert "C:/SI2_Parcial1/fashionstore-rls-lab-5v-data" in s
    assert "replace(current_setting('data_directory'), chr(92), '/')" in s
    assert "left(paths.fuente_pgdata, length(paths.ruta_propuesta) + 1)" in s
    assert "SOLAPAMIENTO_TEXTUAL_BLOQUEAR" in s
    assert "SIN_SOLAPAMIENTO_TEXTUAL_NO_CERTIFICA" in s
    assert "paths.fuente_pgdata LIKE" not in s
    assert "paths.ruta_propuesta LIKE" not in s


def test_5x_comprueba_esquema_sandbox_46_y_48():
    s = sql()
    assert "c.relkind='r') <> 46" in s
    assert "co.convalidated AND co.conname LIKE 'fk_tenant_%') <> 48" in s
    assert "pg_policies" in s
    assert "policies criticas instaladas inesperadamente" in s


def test_5x_dictamen_reconoce_destino_inexistente():
    s = sql()
    assert "1_IDENTIDAD_CLUSTER_FUENTE" in s
    assert "2_DICTAMEN_ALCANCE" in s
    assert "ORIGEN_MEDIDO_DESTINO_AUN_INEXISTENTE" in s
    assert "SIN_CLUSTER_NUEVO_SIN_ROLES_SIN_RLS_A_B" in s


def test_5x_psql_exclusivamente_local_y_solo_lectura():
    s = ps()
    assert "$servidor = '127.0.0.1'" in s
    assert "$puerto = 5432" in s
    assert "$usuario = 'postgres'" in s
    assert "$base = 'fashionstore_saas_reservas_sandbox_5h'" in s
    assert "default_transaction_read_only=on" in s
    assert "--no-psqlrc" in s
    assert "--set=ON_ERROR_STOP=1" in s


def test_5x_reporte_temporal_no_sobrescrito_ni_versionado():
    s = ps()
    assert "Join-Path $env:TEMP 'fashionstore_5x_identidad_fuente_LOCAL_REVISAR.txt'" in s
    assert "Test-Path -LiteralPath $salida" in s
    assert "NO sobrescribir" in s
    assert "no subir GitHub" in s
    assert "ROLLBACK" in s


def test_5x_contrasena_local_oculta_y_variables_restauradas():
    s = ps()
    assert "Read-Host 'Contrasena PostgreSQL LOCAL (NO Supabase)' -AsSecureString" in s
    assert "ZeroFreeBSTR" in s
    for name in ("PGPASSWORD", "PGSSLMODE", "PGOPTIONS"):
        assert f"Env:{name}" in s
    assert "5X DESTINO=NO_EXISTE; RLS_A_B=NO_CERTIFICADO" in s


def test_5x_documentacion_distingue_preflight_de_aislamiento_real():
    s = DOC.read_text(encoding="utf-8")
    for phrase in ("574 passed", "560 passed", "SAAS_AUTH_ENABLED=false",
                   "7 bloqueantes", "Bloque 3 4/7", "NO_EJECUTADO",
                   "system_identifier", "autorización expresa"):
        assert phrase in s


def test_5x_manifiesto_sigue_bloqueado_y_flag_apagado():
    import json
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert m["estado"] == "BORRADOR_NO_EJECUTAR"
    assert m["desplegable"] is False
    assert m["iniciar_laboratorio_autorizado"] is False
    assert m["evidencia"]["pruebas_pg_reales_ejecutadas"] is False
    assert m["baseline"]["bloqueantes_abiertos"] == 7
    assert "saas_auth_enabled: bool = False" in CONFIG.read_text(encoding="utf-8")
