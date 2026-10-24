"""B3/5Y: validar plan NO EJECUTABLE de segunda instancia PostgreSQL 18.

Solo JSON y ficheros de codigo del repositorio: sin conexiones ni procesos,
escrituras, credenciales o comprobaciones en Windows. NO inicia laboratorio.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "database/saas/41_b3_p5y_instancia_nativa_NO_EJECUTAR.json"
LAB_5U = ROOT / "database/saas/37_b3_p5u_laboratorio_rls_aislado_NO_EJECUTAR.json"
GATES = tuple(f"G{i:02d}" for i in range(10))


def errores_plan_5y(p: dict[str, Any], raiz: Path = ROOT) -> list[str]:
    """Detiene toda validación si se anticipa un permiso, dato o paso real."""
    errores: list[str] = []

    def exigir(condicion: bool, mensaje: str) -> None:
        if not condicion:
            errores.append(mensaje)

    exigir(p.get("version") == "fashionstore.b3.p5y.nativa.v1",
           "version desconocida")
    exigir(p.get("estado") == "PLAN_NO_EJECUTAR",
           "estado no bloqueado")
    for campo in ("ejecucion_autorizada", "entorno_autorizado",
                  "operaciones_sql_autorizadas", "limpieza_autorizada"):
        exigir(p.get(campo) is False, f"permiso anticipado: {campo}")

    d = p.get("destino", {})
    exigir(
        d.get("motor") == "PostgreSQL 18"
        and d.get("modo") == "segunda_instancia_nativa_independiente"
        and d.get("host") == "127.0.0.1"
        and type(d.get("puerto_propuesto")) is int
        and d.get("puerto_propuesto") == 55432
        and d.get("directorio_pgdata_propuesto")
        == r"C:\SI2_Parcial1\fashionstore-rls-lab-5v-data",
        "destino no autorizado",
    )
    exigir(d.get("cluster_inicializado") is False
           and d.get("system_identifier_destino") is None,
           "destino real declarado sin aprobacion")

    o = p.get("origen_protegido", {})
    exigir(o.get("host") == "127.0.0.1"
           and o.get("puerto") == 5432
           and o.get("base_sandbox") == "fashionstore_saas_reservas_sandbox_5h"
           and o.get("base_historica_no_tocar")
           == "fashionstore_restore_20261023_193554_594",
           "fuente PostgreSQL incorrecta")
    exigir(o.get("system_identifier_fuente_en_repo") is None
           and o.get("consulta_solo_lectura") is True
           and o.get("ruta_pgdata_no_versionar") is True,
           "filtracion de PGDATA/ID o permiso de escritura al origen")

    seguridad = p.get("seguridad_propuesta", {})
    exigir(seguridad.get("autenticacion") == "scram-sha-256"
           and seguridad.get("listen_addresses") == "127.0.0.1"
           and seguridad.get("hba_ipv4") == "127.0.0.1/32",
           "autenticacion/escucha del laboratorio insegura")
    para_verdadero = (
        "prohibir_trust", "prohibir_md5", "password_superuser_interactivo",
        "sin_contrasena_en_argumentos_o_repo", "sin_acceso_red_publica",
        "no_registrar_servicio_windows",
        "no_usar_set_role_de_postgres_para_pruebas",
        "no_importar_filas_historicas", "no_exportar_secretos",
    )
    exigir(all(seguridad.get(c) is True for c in para_verdadero),
           "faltan restricciones de seguridad")

    compuertas = p.get("compuertas")
    if not isinstance(compuertas, list):
        compuertas = []
    ids = [g.get("id") if isinstance(g, dict) else None for g in compuertas]
    exigir(len(compuertas) == 10 and tuple(ids) == GATES,
           "compuertas faltantes/desordenadas")
    exigir(all(isinstance(g, dict)
               and g.get("cumplida") is False
               and isinstance(g.get("objetivo"), str)
               and len(g["objetivo"]) >= 22 for g in compuertas),
           "compuerta marcada terminada sin evidencia")

    rls = p.get("validacion_rls", {})
    exigir(rls.get("ensayos_previstos") == 14
           and rls.get("casos_reales_ejecutados") == 0
           and rls.get("certificado_aislamiento") is False
           and rls.get("politicas_instaladas") is False
           and rls.get("roles_restringidos_creados") is False
           and rls.get("saas_auth_enabled") is False
           and rls.get("bloqueantes_pendientes") == 7,
           "aislamiento RLS indebidamente declarado")

    ev = p.get("evidencia_inicial", {})
    exigir(ev.get("suite_validada") == "574 passed, 2 warnings in 7.21s"
           and ev.get("preflight_5x") == "ORIGEN_MEDIDO_DESTINO_AUN_INEXISTENTE"
           and ev.get("origen_medido_solo_local") is True
           and ev.get("laboratorio_creado") is False,
           "evidencia historica falsa")

    lab = json.loads((raiz / "database/saas/37_b3_p5u_laboratorio_rls_aislado_NO_EJECUTAR.json")
                     .read_text(encoding="utf-8"))
    exigir(lab.get("estado") == "BORRADOR_NO_EJECUTAR"
           and lab.get("desplegable") is False
           and lab.get("iniciar_laboratorio_autorizado") is False
           and lab.get("evidencia", {}).get("resultado_rls_a_b") == "NO_EJECUTADO",
           "manifiesto de laboratorio 5U desbloqueado")
    exigir((raiz / "app/core/config.py").read_text(encoding="utf-8")
           .find("saas_auth_enabled: bool = False") >= 0,
           "SaaS encendido por defecto")
    return errores


def main() -> int:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    errores = errores_plan_5y(plan)
    print("5Y REVISION OFFLINE - NO EJECUTA initdb/pg_ctl NI SQL")
    if errores:
        for error in errores:
            print("5Y ERROR:", error)
        print("5Y CONTRATO INVALIDO - NO PROCEDER")
        return 2
    print("5Y PLAN CONSISTENTE: 10 compuertas, 14 pruebas A/B futuras")
    print("5Y SEGURIDAD: SCRAM-SHA-256, loopback, sin trust, sin secretos")
    print("5Y BLOQUEADO: autorizacion explicita e instancia independiente pendientes")
    print("5Y NO CERTIFICA RLS - 7 bloqueantes abiertos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
