"""B3/P5M: revisión LOCAL de contratos de identidad/RLS (sin conexión a BD).

Es una herramienta de diagnóstico del CÓDIGO; su resultado no acredita
aislamiento PostgreSQL, seguridad JWT o ejecución de procedimientos.
No importa la aplicación ni abre conexiones, archivos secretos o .env.
"""
from __future__ import annotations

from pathlib import Path


BASE = Path(__file__).resolve().parents[2]
RUTAS = {
    "dependencias": "app/core/tenant_dependencies.py",
    "contexto": "app/core/tenant_context.py",
    "transacciones": "app/core/database.py",
    "login": "app/modules/autenticacion_seguridad/api/tenant_router.py",
    "repositorio_auth": "app/modules/autenticacion_seguridad/repositories/tenant_repository.py",
    "gate": "app/core/saas_route_gate.py",
    "config": "app/core/config.py",
    "sp_crear": "database/saas/borradores/12_sp_crear_reserva_tenant.sql.txt",
    "sp_cancelar": "database/saas/borradores/13_sp_cancelar_reserva_tenant.sql.txt",
    "sp_finalizar": "database/saas/borradores/14_sp_finalizar_reserva_sin_compra_tenant.sql.txt",
}


def evaluar(raiz: Path = BASE) -> dict[str, object]:
    """Inspecciona SOLO archivos fuente; ninguna operación de escritura."""
    archivos = {k: (raiz / v).read_text(encoding="utf-8")
                for k, v in RUTAS.items()}
    d, c, db = (archivos[k] for k in ("dependencias", "contexto", "transacciones"))
    login = archivos["login"]
    repo = archivos["repositorio_auth"]
    gate = archivos["gate"]
    conf = archivos["config"]
    sp = [archivos[n] for n in ("sp_crear", "sp_cancelar", "sp_finalizar")]

    # Este bloqueo debe cumplirse para poder continuar con cualquier auditoría.
    integridad = {
        "flag_default_inactivo": "saas_auth_enabled: bool = False" in conf,
        "login_token_y_membresia": (
            "emitir_token_empresa" in login
            and "obtener_membresia_vigente" in login
            and "obtener_membresia_vigente" in d
            and "leer_token_empresa" in d
        ),
        "contexto_empresa_posterior_membresia": (
            "db.info[\"empresa_id\"] = tenant.empresa_id" in d
            and d.index("obtener_membresia_vigente") <
                d.index("db.info[\"empresa_id\"] = tenant.empresa_id")
        ),
        "rutas_transaccionales_reserva_bloqueadas": (
            '("POST", "/tenant/reservas/prevalidar")' in gate
            and '("POST", "/tenant/reservas")' not in gate
        ),
        "tres_sp_aun_borradores_security_invoker": all(
            "SECURITY INVOKER" in texto
            and "CREATE OR REPLACE PROCEDURE" in texto
            and "IN p_empresa_id bigint" in texto
            and "IN p_usuario_id bigint" in texto
            for texto in sp
        ),
    }
    pendientes = {
        "bootstrap_login_sin_politicas_definidas": (
            "FROM public.usuario_empresa" in repo
            and "JOIN public.usuario" in repo
            and "JOIN public.suscripcion" in repo
            and "JOIN public.plan_saas" in repo
        ),
        "actor_auditoria_no_automatizado_en_dependencia_saas": (
            "set_config('app.usuario_id'" not in d
            or "db.info[\"usuario_id\"]" not in d
        ),
        "actor_no_reaplicado_automaticamente_tras_nueva_transaccion": (
            "session.info.get(\"usuario_id\")" not in db
        ),
        "args_sp_no_vinculados_al_principal_sql_autenticado": all(
            "IN p_empresa_id bigint" in texto
            and "IN p_usuario_id bigint" in texto
            for texto in sp
        ),
        "guc_empresa_no_es_identidad_independiente": (
            "set_config('app.empresa_id'" in d
            and "set_config('app.empresa_id'" in db
        ),
    }
    return {
        "integridad": integridad,
        "pendientes_de_diseno": pendientes,
        "evidencia": {
            "cantidad_sp_inactivos": len(sp),
            "consulta_login_previa_al_tenant": (
                "AuthService.autenticar_credenciales" in login
                and "obtener_membresia_vigente" in login
            ),
            "token_firmado_debe_validarse": "decode_access_token" in c,
        },
        "advertencia": (
            "INSPECCION ESTATICA. RLS NO PROBADO. Ningún GUC de sesión "
            "demuestra identidad ante SQL arbitrario."
        ),
    }


def main() -> int:
    resultado = evaluar()
    print("5M REVISION ESTATICA LOCAL — NO MODIFICA BD NI ARCHIVOS")
    for clave, aprobado in resultado["integridad"].items():
        print(f"GUARDA {'OK' if aprobado else 'FALLA'}: {clave}")
    for clave, pendiente in resultado["pendientes_de_diseno"].items():
        print(f"{'BLOQUEANTE DE DISENO' if pendiente else 'REVISAR CAMBIO'}: {clave}")
    print(resultado["advertencia"])
    if not all(resultado["integridad"].values()):
        print("ERROR: falta una guarda; no preparar ni ejecutar DDL en sandbox.")
        return 2
    print("5M ANALISIS DE CODIGO COMPLETADO; RLS REAL TODAVIA NO VALIDADO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
