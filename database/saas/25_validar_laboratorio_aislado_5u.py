"""B3/5U: valida un CONTRATO OFFLINE de laboratorio PostgreSQL aislado.

NO crea cluster, roles, credenciales, contenedores, policies o seed.
NO ejecuta comandos externos ni abre conexiones de red/DB. El CLI imprime
un estado BLOQUEADO deliberado, no certifica aislamiento tenant A/B.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "database/saas/37_b3_p5u_laboratorio_rls_aislado_NO_EJECUTAR.json"
EXPECTATIVAS = {
    "A01": ("A", "PERMITIR"),
    "A02": ("A", "DENEGAR_SIN_FILAS"),
    "A03": ("B", "DENEGAR_SIN_FILAS"),
    "A04": ("B", "PERMITIR"),
    "A05": ("A", "RECHAZAR_WITH_CHECK"),
    "A06": ("B", "RECHAZAR_SIN_CAMBIOS"),
    "A07": ("A", "DENEGAR_SIN_FILAS"),
    "A08": ("B", "RECHAZAR_IDENTIDAD_SQL"),
    "A09": ("A", "RECHAZAR_PERMISO"),
    "A10": ("A", "RECHAZAR_PERMISO"),
    "A11": ("A", "ROLLBACK_TOTAL"),
    "A12": ("B", "DENEGAR_SIN_FILAS"),
    "A13": ("A", "SIN_DOBLE_RESERVA"),
    "A14": ("A", "PERMITIR"),
}


def errores_contrato_5u(datos: dict[str, Any], raiz: Path = ROOT) -> list[str]:
    """Falla cerrado ante edición que intente habilitar un laboratorio no aprobado."""
    errores: list[str] = []

    def exige(condicion: bool, mensaje: str) -> None:
        if not condicion:
            errores.append(mensaje)

    exige(datos.get("version") == "fashionstore.b3.p5u.laboratorio-aislado.v1",
          "version desconocida")
    exige(datos.get("estado") == "BORRADOR_NO_EJECUTAR"
          and datos.get("desplegable") is False
          and datos.get("iniciar_laboratorio_autorizado") is False,
          "laboratorio indebidamente habilitado")

    origen = datos.get("origen", {})
    destino = datos.get("destino", {})
    exige(origen.get("host") == "127.0.0.1"
          and origen.get("puerto") == 5432
          and origen.get("base") == "fashionstore_saas_reservas_sandbox_5h",
          "origen distinto al sandbox permitido")
    exige(origen.get("solo_lectura") is True
          and origen.get("solo_esquema") is True
          and origen.get("filas_exportadas") is False,
          "origen exporta datos o no es readonly/schema-only")
    exige(origen.get("system_identifier") is None,
          "el manifiesto no debe fingir un system_identifier medido")
    exige(destino.get("host") == "127.0.0.1"
          and type(destino.get("puerto_propuesto")) is int
          and destino.get("puerto_propuesto") == 55432
          and destino.get("base_propuesta") == "fashionstore_rls_lab_5u",
          "destino no está separado del puerto 5432")
    exige(destino.get("system_identifier") is None,
          "el manifiesto no debe inventar identidad de destino")
    for clave in ("datadir_independiente_verificado",
                  "volumen_aislado_verificado",
                  "autenticacion_local_verificada",
                  "datos_reales_importados"):
        exige(destino.get(clave) is False, f"destino anticipa {clave}")

    condiciones = datos.get("condiciones_irrenunciables", [])
    exige(len(condiciones) == 8
          and any("system_identifier" in x for x in condiciones)
          and any("GUC" in x or "app.empresa_id" in x for x in condiciones)
          and any("--schema-only" in x for x in condiciones)
          and any("rol" in x.lower() for x in condiciones),
          "condiciones de seguridad incompletas")

    modelo = datos.get("modelo_futuro", {})
    exige(modelo.get("principal_sql_empresa_a") == "NO_CREADO"
          and modelo.get("principal_sql_empresa_b") == "NO_CREADO"
          and modelo.get("estado_acls_policies") == "PENDIENTE"
          and modelo.get("apto_para_ejecutar") is False
          and modelo.get("aislamiento_sql_ante_inyeccion") == "NO_DEMOSTRADO",
          "identidades SQL/policies ya activadas sin ensayo")

    exige(datos.get("tenants_ficticios") == ["empresa-lab-a", "empresa-lab-b"],
          "empresas ficticias insuficientes")
    casos = datos.get("plan_casos", [])
    indices = [x.get("id") for x in casos if isinstance(x, dict)]
    exige(len(casos) == 14 and len(indices) == len(set(indices))
          and set(indices) == set(EXPECTATIVAS),
          "14 casos A/B incompletos o duplicados")
    for caso in casos:
        if not isinstance(caso, dict):
            errores.append("caso de ensayo invalido")
            continue
        ident = caso.get("id")
        if ident in EXPECTATIVAS:
            esperado = EXPECTATIVAS[ident]
            exige(
                (caso.get("principal"), caso.get("esperado")) == esperado
                and isinstance(caso.get("operacion"), str)
                and len(caso["operacion"]) > 8,
                f"resultado esperado alterado para {ident}",
            )
    exige(any("app.empresa_id" in c.get("operacion", "") for c in casos
              if isinstance(c, dict)), "falta prueba de suplantacion GUC")
    exige(any("password_hash" in c.get("operacion", "") for c in casos
              if isinstance(c, dict)), "falta prueba de acceso a hash")

    evidencia = datos.get("evidencia", {})
    exige(evidencia.get("pruebas_pg_reales_ejecutadas") is False
          and evidencia.get("resultado_rls_a_b") == "NO_EJECUTADO"
          and evidencia.get("row_security_active_rol_a") is None
          and evidencia.get("row_security_active_rol_b") is None
          and evidencia.get("pase_de_casos") == []
          and evidencia.get("casos_reales_aprobados") == 0
          and evidencia.get("certificado_aislamiento") is False,
          "evidencia PostgreSQL inventada o fase declarada terminada")

    restricciones = datos.get("restricciones", {})
    exige(restricciones.get("saas_auth_enabled") is False
          and restricciones.get("transacciones_reserva_saas_habilitadas") is False
          and restricciones.get("principal_clúster_local_sin_mutaciones") is True
          and restricciones.get("main_sin_mutaciones") is True
          and restricciones.get("supabase_sin_mutaciones") is True
          and restricciones.get("ddl_futuro_solo_tras_autorizacion_explicita") is True,
          "se levantaron bloqueos de ambientes y DDL")
    exige(datos.get("baseline", {}).get("bloqueantes_abiertos") == 7,
          "los siete bloqueantes siguen abiertos")

    config = (raiz / "app/core/config.py").read_text(encoding="utf-8")
    gate = (raiz / "app/core/saas_route_gate.py").read_text(encoding="utf-8")
    exige("saas_auth_enabled: bool = False" in config,
          "SaaS activo por defecto")
    exige('("POST", "/tenant/reservas")' not in gate,
          "escritura de reservas desbloqueada")
    return errores


def comparar_identidad_clusters_5u(origen_id: Any, destino_id: Any) -> bool:
    """Compara evidencias externas futuras; NO conecta ni consulta PostgreSQL.

    Rechazar clones físicos: pg_basebackup puede preservar system_identifier.
    Identificadores iguales => clúster NO independiente. Diferentes =>
    condición necesaria pero aún NO SUFICIENTE para aislamiento de datos.
    """
    return (
        type(origen_id) is int and type(destino_id) is int
        and origen_id > 0 and destino_id > 0
        and origen_id != destino_id
    )


def main() -> int:
    datos = json.loads(MANIFEST.read_text(encoding="utf-8"))
    errores = errores_contrato_5u(datos)
    print("5U COMPROBACION OFFLINE - NO INICIA CLUSTER NI CREA ROLES")
    if errores:
        for error in errores:
            print("5U ERROR:", error)
        print("5U CONTRATO INVALIDO: no intentar SQL ni DDL")
        return 2
    print("5U MATRIZ CONSISTENTE: 14 casos A/B, 2 empresas ficticias")
    print("5U AISLAMIENTO DE CLUSTER PENDIENTE: system_identifier sin medir")
    print("5U NO EJECUTABLE: 7 bloqueantes y RLS A/B NO CERTIFICADO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
