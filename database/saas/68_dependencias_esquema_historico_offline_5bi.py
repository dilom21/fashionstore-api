"""5BI — dependencia del esquema histórico, análisis estático solamente.

No ejecuta SQL, no importa app, no accede a .env ni a redes y no conecta
PostgreSQL 5432/55432/55433 ni Supabase. NO USAR las cuatro migraciones
contra una base nueva: varias tienen guardas de datos históricos y un
nombre de restauración comercial explícito.

Lee exclusivamente cinco rutas versionadas y conocidas. El informe
permite decidir si existe un camino seguro para construir un esquema
comercial ficticio completo, sin copiar clientes, contraseñas ni backups.

ADVERTENCIA: la presencia de SQL versionado no prueba reproducibilidad;
la etapa 5BG probó CU16/17/18 en esquema PARCIAL, no la base empresarial.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[2]
MIGRATIONS=(
 "supabase/migrations/20261023000100_saas_base_tables.sql",
 "supabase/migrations/20261023000200_vanter_tenancy_historica.sql",
 "supabase/migrations/20261023000300_integridad_tenant_local.sql",
 "supabase/migrations/20261023000400_cuatro_tenants_demo_local.sql",
)
BITACORA="SQL_Correccion_Bitacora_IP.sql"
EXPECTED_DB="fashionstore_restore_20261023_193554_594"
BASE_TABLES=("empresa","plan_saas","suscripcion",
             "usuario_empresa","empresa_configuracion")


def require(ok,message):
    if not ok:
        raise RuntimeError("5BI PRECHECK BLOQUEADO: "+message)


def leer_rutas():
    sources={}
    for rel in (*MIGRATIONS,BITACORA):
        path=ROOT/rel
        require(path.is_file() and
                path.resolve().is_relative_to(ROOT.resolve()),
                "falta una fuente versionada o hay un enlace fuera del repo")
        require(path.stat().st_size<500_000,
                "fuente demasiado grande para auditoría estática")
        sources[rel]=path.read_text(encoding="utf-8")
    return sources


def verificar_dependencias(sources):
    base=sources[MIGRATIONS[0]]
    tables=tuple(re.findall(
        r"(?im)^\s*CREATE\s+TABLE\s+public\.(\w+)\s*\(",base))
    require(tables==BASE_TABLES,
            "la migración SaaS base ya no crea exactamente cinco tablas")
    require("to_regclass('public.usuario')" in base and
            "to_regclass('public.rol')" in base,
            "la dependencia de usuario/rol legado cambió")
    require("CREATE TABLE public.usuario (" not in base,
            "encontrado CREATE usuario inesperado; revisar preflight")
    check46=("v_tablas_public <> 46","v_total <> 46","v_n <> 46")
    hardcoded=re.compile(
      r"current_database\(\)\s*<>\s*'"+
      re.escape(EXPECTED_DB)+r"'",re.I)
    for file,marker in zip(MIGRATIONS[1:],check46):
        script=sources[file]
        require(marker in script,
                "cambió el conteo de 46 tablas históricas: "+file)
        require(hardcoded.search(script) is not None,
                "cambió la protección por nombre de base: "+file)
    second,third,fourth=(sources[f] for f in MIGRATIONS[1:])
    require("v_trigger_count <> 27" in second
            and "NOT t.tgisinternal) <> 27" in fourth,
            "cambió el requisito de 27 triggers")
    require("v_actual <> 46" in third and
            "fk_tenant_%" in fourth and
            "<> 48" in fourth,
            "cambió construcción de FK compuestas / catálogo exigido")
    require("CREATE TRIGGER trg_bitacora_completar_ip" in sources[BITACORA],
            "archivo complementario de bitácora cambió")
    # 5 nuevas + 41 preexistentes = 46 final esperado en las migraciones.
    # Este cálculo es inferencia del orden previsto, NO catálogo verificado.
    return {
        "base_saas":len(tables),
        "total_documentado_migraciones":46,
        "legacy_preexistente_estimado":46-len(tables),
        "triggers_heredados_exigidos":27,
        "fk_tenant_esperadas_paso6":48,
        "db_name_fijo":EXPECTED_DB,
        "fuentes_legado_ddl_completo":False,
        "nuevos_triggers_complemento":1,
    }


def reporte(datos):
    print("5BI AUDITORÍA PASS: cinco fuentes versionadas examinadas "
          "SIN ejecutar SQL ni conectar DB")
    print("5BI BASE: migración agrega "+
          str(datos["base_saas"])+" tablas SaaS; depende de usuario y rol "
          "preexistentes")
    print("5BI CONTEOS: 46 tablas exigidas tras migración; "+
          str(datos["legacy_preexistente_estimado"])+
          " serían anteriores si el conjunto es completo; "
          "27 triggers heredados y 48 FK tenant esperadas")
    print("5BI GUARDAS: tres scripts dependen del nombre fijo de la "
          "restauración y conteos históricos; NO ejecutarlos en PG TEMP")
    print("5BI BITÁCORA: un trigger adicional versionado no sustituye "
          "los 27 triggers heredados")
    print("5BI DISCREPANCIA: informes previos citan 47 tablas; "
          "estas migraciones exigen 46. Reconciliar antes de 5BJ")
    print("5BI RECONSTRUCCIÓN BLOQUEADA: no hay DDL heredado completo "
          "verificado entre estas fuentes; localizar script schema-only "
          "del primer parcial o diseño versionado equivalente")
    print("5BI SEGURIDAD: no se leyeron backups ni secretos; "
          "SaaS OFF, 5432/55432/55433 y Supabase intactos")


def main():
    data=verificar_dependencias(leer_rutas())
    reporte(data)


if __name__=="__main__":
    try:
        main()
    except Exception as error:
        print("5BI ERROR: "+type(error).__name__+": "+str(error),
              file=sys.stderr)
        sys.exit(2)
