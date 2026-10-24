"""5Q: validador OFFLINE del contrato de acceso. No aplica permisos SQL.

Este programa no importa la app ni abre BD, no lee .env ni secretos.
Sirve para detectar cambios peligrosos en la propuesta, NO para certificar
RLS aplicado ni para autorizar CREATE ROLE, GRANT o CREATE POLICY.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = (
    ROOT / "database/saas/32_b3_p5q_matriz_limites_roles_NO_APLICAR.json"
)
REQUIRED_BLOCKS = frozenset({
    "bootstrap_rls", "acceso_hash_seguro", "sql_identidad_invocador",
    "guc_mutable", "rls_policies_y_grants", "legacy_execute_public",
    "integracion_pg_real",
})
REQUIRED_FORBIDDEN = frozenset({
    "CREATE ROLE", "CREATE POLICY", "GRANT", "REVOKE", "ALTER TABLE",
    "INSERT DE SEED", "CALL sp_*_tenant", "SAAS_AUTH_ENABLED=true",
})
REQUIRED_LOGIN_TABLES = frozenset({
    "usuario", "rol", "usuario_empresa", "empresa", "suscripcion",
    "plan_saas", "cliente", "empleado", "sucursal",
})
PROCEDURES = frozenset({
    "sp_crear_reserva_tenant", "sp_cancelar_reserva_tenant",
    "sp_finalizar_reserva_sin_compra_tenant",
})


def errores_contrato(datos: dict[str, Any], raiz: Path = ROOT) -> list[str]:
    """Lista errores; no ejecuta SQL ni altera JSON ni archivos."""
    errores: list[str] = []

    def exigir(condicion: bool, mensaje: str) -> None:
        if not condicion:
            errores.append(mensaje)

    exigir(datos.get("formato") == "fashionstore.b3.p5q.contrato-acceso.v1",
           "formato incompatible")
    exigir(datos.get("estado") == "BORRADOR_NO_APLICABLE"
           and datos.get("desplegable") is False, "diseno marcado como aplicable")
    exigir(datos.get("branch") == "feat/saas-multitenant-parcial2",
           "rama incorrecta")
    exigir(datos.get("base_autorizada_solo_futuros_ensayos")
           == "fashionstore_saas_reservas_sandbox_5h", "base no autorizada")

    amenaza = datos.get("modelo_amenaza", {})
    exigir(amenaza.get("frontend_solo_http") is True
           and amenaza.get("acceso_sql_de_clientes_finales") == "DENEGADO",
           "acceso SQL externo permitido")
    exigir(amenaza.get("inyeccion_sql_con_credencial_compartida")
           == "NO_MITIGADA_CON_GUC"
           and amenaza.get("guc_es_identidad_autenticada") is False,
           "GUC usado como identidad autenticada")
    exigir(amenaza.get("cuenta_postgres_en_aplicacion") == "PROHIBIDA",
           "superusuario para API permitido")

    roles = datos.get("principales_futuros", {})
    exigir(set(roles) == {"bootstrap", "comercial", "auditoria"},
           "faltan perfiles separados")
    nombres: list[str] = []
    for clave in ("bootstrap", "comercial", "auditoria"):
        info = roles.get(clave, {})
        nombre = info.get("nombre_descriptivo", "")
        exigir(isinstance(nombre, str) and nombre.startswith("rol_sql_")
               and nombre != "postgres", f"{clave}: rol propuesto invalido")
        nombres.append(nombre)
        for atributo in ("existe_en_postgresql", "superuser", "bypassrls",
                         "grant_ejecutable_definido"):
            exigir(info.get(atributo) is False,
                   f"{clave}: {atributo} debe ser false")
        exigir(info.get("permiso_select_total_usuario") != "PERMITIR",
               f"{clave}: SELECT general de usuario")
    exigir(len(set(nombres)) == 3, "principales no separados")

    bootstrap = roles.get("bootstrap", {})
    exigir(set(bootstrap.get("tablas_dependencia_login", []))
           == REQUIRED_LOGIN_TABLES, "dependencias del login incompletas")
    exigir(bootstrap.get("hash_requerido_por_codigo_actual") is True,
           "necesidad de hash ignorada")
    exigir(bootstrap.get("permiso_select_total_usuario") == "PROHIBIDO",
           "bootstrap con SELECT usuario general")
    exigir(bootstrap.get("puede_efectuar_comercio") is False,
           "bootstrap autorizado a comercio")
    comercio = roles.get("comercial", {})
    exigir(comercio.get("puede_leer_password_hash") is False,
           "comercial puede leer hashes")
    exigir(comercio.get("permiso_select_total_usuario") == "PROHIBIDO",
           "comercial con SELECT usuario general")
    exigir(comercio.get("puede_llamar_sp_legacy") is False,
           "comercial puede ejecutar SP heredados")
    exigir(comercio.get("llamada_sp_tenant") == "BLOQUEADA",
           "CALL SaaS activado")
    exigir(set(comercio.get("sp_nuevos_solo_en_borrador", [])) == PROCEDURES,
           "SP tenant no coinciden con los tres borradores")
    exigir(roles.get("auditoria", {}).get("puede_leer_password_hash") is False,
           "auditor puede leer hashes")

    auth = datos.get("contrato_autenticacion", {})
    exigir(auth.get("hash_solo_verificacion_servidor") is True
           and auth.get("hash_en_http_respuesta") is False,
           "hash expuesto en respuestas")
    exigir(auth.get("requiere_refactor_consulta_orm_usuario") is True
           and auth.get("requiere_arquitectura_bootstrap_sin_rls_circular") is True
           and auth.get("requiere_proteger_acceso_al_hash") is True,
           "bootstrap declarado listo sin refactor y aislamiento")

    reservas = datos.get("contrato_reservas", {})
    exigir(reservas.get("identidad_http_desde_tenantcontext") is True,
           "IDs HTTP ajenos no rechazados")
    for clave in ("guc_no_valida_identidad_sql",
                  "requiere_ruta_privilegios_security_invoker",
                  "requiere_vincular_sp_al_principal_sql",
                  "requiere_politicas_select_y_with_check",
                  "requiere_privilegios_transitivos_trigger",
                  "requiere_revisar_execute_public_sp_legacy"):
        exigir(reservas.get(clave) is True, f"riesgo ignorado: {clave}")

    matriz = datos.get("matriz_politicas_futuras", [])
    exigir(len(matriz) == 8
           and len({fila.get("recurso") for fila in matriz}) == 8,
           "matriz no tiene 8 recursos distintos")
    filas = {f.get("recurso"): f for f in matriz}
    hash_access = filas.get("usuario.password_hash", {})
    exigir(hash_access.get("comercial") == "DENEGAR"
           and hash_access.get("auditoria") == "DENEGAR"
           and hash_access.get("bootstrap") == "SOLO_ACCESO_RESTRINGIDO_PENDIENTE",
           "hash liberado sin canal de autenticacion restringido")
    legacy = filas.get("sp_reservas_legacy", {})
    exigir(all(legacy.get(r) == "DENEGAR" for r in
               ("bootstrap", "comercial", "auditoria")),
           "SP legacy permitido en matriz")
    for fila in matriz:
        for rol in ("bootstrap", "comercial", "auditoria"):
            valor = fila.get(rol)
            exigir(isinstance(valor, str)
                   and (valor == "DENEGAR" or valor.endswith("_PENDIENTE")),
                   f"permiso amplio o ejecutable: {fila.get('recurso')}/{rol}")

    bloques = datos.get("controles_bloqueantes", [])
    ids = [x.get("id") for x in bloques]
    exigir(len(bloques) == len(REQUIRED_BLOCKS) and set(ids) == REQUIRED_BLOCKS,
           "bloqueantes no inventariados completamente")
    exigir(all(x.get("resuelto") is False and x.get("motivo")
               for x in bloques), "bloqueante cerrado sin evidencia SQL")
    exigir(set(datos.get("operaciones_prohibidas_hasta_autorizacion", []))
           == REQUIRED_FORBIDDEN, "se eliminaron operaciones prohibidas")

    # El manifiesto solo describe un futuro esquema: comprobar los BLOQUEOS
    # existentes sin importar dependencias que abran conexion al motor.
    config = (raiz / "app/core/config.py").read_text(encoding="utf-8")
    gate = (raiz / "app/core/saas_route_gate.py").read_text(encoding="utf-8")
    login = (raiz / "app/modules/autenticacion_seguridad/api/tenant_router.py").read_text(
        encoding="utf-8"
    )
    auth_service = (
        raiz / "app/modules/autenticacion_seguridad/services/service.py"
    ).read_text(encoding="utf-8")
    repositorio = (
        raiz / "app/modules/autenticacion_seguridad/repositories/tenant_repository.py"
    ).read_text(encoding="utf-8")
    exigir("saas_auth_enabled: bool = False" in config,
           "SaaS habilitado por defecto")
    exigir('("POST", "/tenant/reservas")' not in gate,
           "router transaccional de reservas habilitado")
    exigir("AuthService.autenticar_credenciales" in login
           and "obtener_membresia_vigente" in login
           and "verify_password(password, usuario.password_hash)" in auth_service,
           "flujo autenticacion cambio sin actualizar contrato")
    exigir(all(f"public.{t}" in repositorio for t in REQUIRED_LOGIN_TABLES
               if t != "rol") and "public.rol" in repositorio,
           "dependencias SQL de membresia cambiaron")
    for numero, nombre in (
        ("12", "sp_crear_reserva_tenant"),
        ("13", "sp_cancelar_reserva_tenant"),
        ("14", "sp_finalizar_reserva_sin_compra_tenant"),
    ):
        ruta = (
            raiz / f"database/saas/borradores/{numero}_{nombre}.sql.txt"
        )
        contenido = ruta.read_text(encoding="utf-8")
        exigir("SECURITY INVOKER" in contenido
               and f"PROCEDURE public.{nombre}" in contenido,
               f"borrador SP {numero} faltante o cambiado")

    return errores


def main() -> int:
    datos = json.loads(MANIFEST.read_text(encoding="utf-8"))
    errores = errores_contrato(datos)
    print("5Q VALIDACION ESTATICA OFFLINE — NO APLICA SQL NI ROLES")
    for mensaje in errores:
        print(f"ERROR CONTRATO: {mensaje}")
    if errores:
        print("5Q CONTRATO INVALIDO: no ejecutar ningun DDL ni habilitar SaaS")
        return 2
    print(f"5Q MATRIZ CONSISTENTE: {len(datos['matriz_politicas_futuras'])} recursos, "
          f"{len(datos['controles_bloqueantes'])} bloqueantes sin resolver")
    print("5Q BORRADOR NO DESPLEGABLE; RLS POSTGRESQL REAL NO VALIDADO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
