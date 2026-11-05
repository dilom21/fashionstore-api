"""5BO / Etapa F - Genera evidencias SQL del laboratorio (solo lectura).

Lee el manifiesto del laboratorio 5BO (127.0.0.1:55445 / fashionstore_lab_5bo)
y escribe tres archivos NUEVOS bajo ``database/saas/evidencias`` con sello
temporal unico. No modifica datos: todas las consultas son SELECT.

Uso:
    python database/saas/79_evidencias_5bo.py

Salidas:
    EVIDENCIA_RBAC_PERSONAL_5BO_<YYYYMMDD_HHMMSS>.txt
    EVIDENCIA_SUPERADMIN_5BO_<YYYYMMDD_HHMMSS>.txt
    EVIDENCIA_PLANES_SUCURSALES_5BO_<YYYYMMDD_HHMMSS>.txt
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCIAS = ROOT / "database" / "saas" / "evidencias"
MANIFIESTO = Path(
    os.environ.get(
        "FASHIONSTORE_LAB_5BO_MANIFEST",
        str(Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "Temp" / "opencode" / "fs_lab_5bo" / "lab_5bo_manifest.json"),
    )
)


def _ruta_unica(prefijo: str, marca: str) -> Path:
    ruta = EVIDENCIAS / f"{prefijo}_{marca}.txt"
    base, n = ruta, 2
    while ruta.exists():
        ruta = base.with_name(f"{prefijo}_{marca}_v{n:02d}.txt")
        n += 1
    return ruta


def _encabezado(titulo: str, datos: dict) -> list[str]:
    return [
        titulo,
        f"Fecha UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "Cluster: " + datos["cluster"].get("version", "?") + " en "
        + datos["cluster"]["host"] + ":" + str(datos["cluster"]["puerto"])
        + " / " + datos["cluster"]["base"],
        "Migraciones 5BO: " + ", ".join(datos.get("migraciones_5bo", [])),
        "Sin secretos: no se incluyen DSN, JWT, contrasenas, HMAC ni PII.",
        "=" * 78,
    ]


def main() -> int:
    if not MANIFIESTO.exists():
        print("No existe el manifiesto 5BO; ejecute 78_laboratorio_operativo_5bo.py",
              file=sys.stderr)
        return 2
    datos = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    if datos["cluster"]["puerto"] != 55445:
        print("El manifiesto no corresponde al laboratorio 5BO", file=sys.stderr)
        return 2

    import psycopg

    EVIDENCIAS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    escritas: list[Path] = []

    with psycopg.connect(datos["admin_dsn"]) as conn:
        # ------------------------------------------------------------------
        # RBAC / personal
        # ------------------------------------------------------------------
        lineas = _encabezado(
            "EVIDENCIA RBAC Y PERSONAL - ETAPA F (5BO) - SOLO LECTURA", datos)
        lineas.append("MATRIZ NOMINAL BASE (rol|funcion|accion|permitido):")
        for fila in conn.execute(
            "SELECT rol_nombre, funcion, accion, permitido "
            "  FROM saas.permiso_default ORDER BY rol_nombre, funcion, accion"
        ).fetchall():
            lineas.append("  {}|{}|{}|{}".format(*fila))
        lineas.append("")
        lineas.append("EXCEPCIONES POR EMPRESA (empresa|rol|funcion|accion|permitido):")
        filas = conn.execute(
            "SELECT pt.empresa_id, r.nombre, pt.funcion, pt.accion, pt.permitido "
            "  FROM saas.permiso_tenant pt JOIN public.rol r ON r.id = pt.rol_id "
            " ORDER BY pt.empresa_id, r.nombre, pt.funcion"
        ).fetchall()
        if not filas:
            lineas.append("  (sin excepciones persistidas)")
        for fila in filas:
            lineas.append("  {}|{}|{}|{}|{}".format(*fila))
        lineas.append("")
        lineas.append("INVITACIONES (total|usadas|vigentes|expiradas):")
        lineas.append("  " + str(conn.execute(
            "SELECT count(*), "
            "       count(*) FILTER (WHERE usada_en IS NOT NULL), "
            "       count(*) FILTER (WHERE usada_en IS NULL AND revocada_en IS NULL "
            "                        AND expira_en > now()), "
            "       count(*) FILTER (WHERE usada_en IS NULL AND expira_en <= now()) "
            "  FROM saas.invitacion_personal"
        ).fetchone()))
        lineas.append("")
        lineas.append("ROL GLOBAL vs ROL EMPRESARIAL (empresa|usuario|rol_global|rol_empresa|activo):")
        for fila in conn.execute(
            "SELECT ue.empresa_id, ue.usuario_id, rg.nombre, re.nombre, ue.estado "
            "  FROM public.usuario_empresa ue "
            "  JOIN public.usuario u ON u.id = ue.usuario_id "
            "  JOIN public.rol rg ON rg.id = u.rol_id "
            "  JOIN public.rol re ON re.id = ue.rol_id "
            " ORDER BY ue.empresa_id, ue.usuario_id LIMIT 60"
        ).fetchall():
            lineas.append("  {}|{}|{}|{}|{}".format(*fila))
        lineas.append("")
        lineas.append("EVENTOS DE AUDITORIA EMPRESARIAL (accion|total):")
        for fila in conn.execute(
            "SELECT accion, count(*) FROM public.bitacora "
            " WHERE accion IN ('PERSONAL_ALTA','PERSONAL_SUCURSAL','RBAC_ACTUALIZADO',"
            "                  'INVITACION_ACEPTADA') "
            " GROUP BY accion ORDER BY accion"
        ).fetchall():
            lineas.append("  {}|{}".format(*fila))
        lineas.append("")
        lineas.append("INVARIANTE: en las cuentas creadas por el alta 5BO, el rol GLOBAL")
        lineas.append("nunca es ADMINISTRADOR/ENCARGADO_SUCURSAL/CAJERO (siempre CLIENTE).")
        lineas.append("  usuarios_de_alta_con_rol_global_no_cliente = " + str(conn.execute(
            "SELECT count(*) FROM public.usuario u JOIN public.rol r ON r.id = u.rol_id "
            " WHERE upper(r.nombre) IN ('ADMINISTRADOR','ENCARGADO_SUCURSAL','CAJERO') "
            "   AND EXISTS (SELECT 1 FROM saas.invitacion_personal i "
            "                WHERE i.usuario_id = u.id)"
        ).fetchone()[0]))
        lineas.append("  (los usuarios sembrados por 5BL/5BK, anteriores a esta etapa, no")
        lineas.append("   se cuentan: su rol global es estado historico, no una escalada de F)")
        ruta = _ruta_unica("EVIDENCIA_RBAC_PERSONAL_5BO", marca)
        ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        escritas.append(ruta)

        # ------------------------------------------------------------------
        # Superadmin
        # ------------------------------------------------------------------
        lineas = _encabezado(
            "EVIDENCIA SUPERADMIN - ETAPA F (5BO) - SOLO LECTURA", datos)
        lineas.append("INTENTOS DE AUTENTICACION GLOBAL (resultado|total):")
        filas = conn.execute(
            "SELECT resultado, count(*) FROM saas_admin.intento_auth "
            " GROUP BY resultado ORDER BY resultado"
        ).fetchall()
        if not filas:
            lineas.append(
                "  (sin intentos: la suite de pruebas limpia los contadores al "
                "cerrar cada prueba para aislar el rate limit; la prueba "
                "test_p1_rate_limit_compartido_y_auditoria_sin_secretos verifica "
                "que se persisten en PostgreSQL durante la ventana)")
        for fila in filas:
            lineas.append("  {}|{}".format(*fila))
        lineas.append("")
        lineas.append("ULTIMOS EVENTOS AUDITADOS (fecha|metodo|ruta|codigo|resultado|empresa|huella):")
        for fila in conn.execute(
            "SELECT creado, metodo, ruta, codigo, resultado, empresa_id, fingerprint "
            "  FROM saas_admin.auditoria_operacion ORDER BY creado DESC LIMIT 25"
        ).fetchall():
            lineas.append("  " + "|".join("" if valor is None else str(valor)
                                          for valor in fila))
        lineas.append("")
        lineas.append("ACL DE LAS FUNCIONES DE SEGURIDAD (rutina|admin|tenant):")
        for fila in conn.execute(
            "SELECT p.proname, "
            "       has_function_privilege('saas_admin_runtime', p.oid, 'EXECUTE'), "
            "       has_function_privilege('fs_saas_runtime', p.oid, 'EXECUTE') "
            "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            " WHERE n.nspname = 'saas_admin' AND p.proname IN "
            "       ('estado_acceso','registrar_intento','auditar_operacion',"
            "        'listar_auditoria','limpiar_intentos') ORDER BY p.proname"
        ).fetchall():
            lineas.append("  {}|{}|{}".format(*fila))
        lineas.append("")
        lineas.append("SEPARACION DE PRINCIPALES:")
        fila = conn.execute(
            "SELECT has_table_privilege('saas_admin_runtime','public.empresa','SELECT'), "
            "       has_table_privilege('saas_admin_runtime','saas_admin.intento_auth','SELECT'), "
            "       has_schema_privilege('saas_admin_runtime','saas','USAGE'), "
            "       has_table_privilege('fs_saas_runtime','public.usuario','INSERT'), "
            "       has_schema_privilege('fs_saas_runtime','saas_admin','USAGE'), "
            "       has_function_privilege('fs_saas_runtime',"
            "         to_regprocedure('saas_admin.estado_acceso(text,text,integer,integer,integer)'),"
            "         'EXECUTE')"
        ).fetchone()
        lineas.append(
            "  admin_lee_empresa={0} admin_lee_intentos={1} admin_usa_saas={2} "
            "tenant_inserta_usuario={3} tenant_usa_saas_admin={4} "
            "tenant_ejecuta_estado_acceso={5}".format(*fila))
        lineas.append("")
        lineas.append("ROLES DEL PLANO GLOBAL:")
        for fila in conn.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolinherit "
            "  FROM pg_roles WHERE rolname IN ('fs_saas_runtime','saas_admin_runtime',"
            "   'saas_admin_owner','saas_personal_owner','saas_rls_owner',"
            "   'saas_actor_sql_owner') ORDER BY rolname"
        ).fetchall():
            lineas.append("  {}|login={}|super={}|bypassrls={}|inherit={}".format(*fila))
        ruta = _ruta_unica("EVIDENCIA_SUPERADMIN_5BO", marca)
        ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        escritas.append(ruta)

        # ------------------------------------------------------------------
        # Planes y sucursales
        # ------------------------------------------------------------------
        lineas = _encabezado(
            "EVIDENCIA PLANES Y SUCURSALES - ETAPA F (5BO) - SOLO LECTURA", datos)
        lineas.append("PLANES (codigo|max_sucursales|ia|ventas|reservas|offline|reportes|estado):")
        for fila in conn.execute(
            "SELECT codigo, max_sucursales_activas, permite_reportes_ia, permite_ventas, "
            "       permite_reservas, permite_offline, permite_reportes_convencionales, estado "
            "  FROM public.plan_saas ORDER BY id"
        ).fetchall():
            lineas.append("  {}|{}|{}|{}|{}|{}|{}|{}".format(*fila))
        lineas.append("")
        lineas.append("SUSCRIPCIONES (empresa|plan|estado|inicio|fin|vigente_hoy):")
        for fila in conn.execute(
            "SELECT s.empresa_id, p.codigo, s.estado, s.fecha_inicio, s.fecha_vencimiento, "
            "       (s.estado = 'ACTIVA' AND (timezone('America/La_Paz', now()))::date "
            "        BETWEEN s.fecha_inicio AND s.fecha_vencimiento) "
            "  FROM public.suscripcion s JOIN public.plan_saas p ON p.id = s.plan_saas_id "
            " ORDER BY s.empresa_id, s.id"
        ).fetchall():
            lineas.append("  {}|{}|{}|{}|{}|{}".format(*fila))
        lineas.append("")
        lineas.append("SUCURSALES ACTIVAS vs LIMITE (empresa|activas|limite|excede):")
        for fila in conn.execute(
            """
            WITH vigente AS (
              SELECT DISTINCT ON (s.empresa_id) s.empresa_id, p.max_sucursales_activas
                FROM public.suscripcion s JOIN public.plan_saas p ON p.id = s.plan_saas_id
               WHERE s.estado = 'ACTIVA' AND p.estado IS TRUE
               ORDER BY s.empresa_id, s.id DESC
            )
            SELECT e.id, COALESCE(v.max_sucursales_activas, 0),
                   (SELECT count(*) FROM public.sucursal su
                     WHERE su.empresa_id = e.id AND su.estado IS TRUE)
              FROM public.empresa e LEFT JOIN vigente v ON v.empresa_id = e.id
             ORDER BY e.id
            """
        ).fetchall():
            empresa_id, limite, activas = fila
            excede = bool(limite) and int(activas) > int(limite)
            lineas.append(f"  {empresa_id}|{activas}|{limite}|{excede}")
        lineas.append("")
        lineas.append("INVARIANTE: ninguna empresa supera su limite de sucursales activas")
        excedidas = conn.execute(
            """
            WITH vigente AS (
              SELECT DISTINCT ON (s.empresa_id) s.empresa_id, p.max_sucursales_activas
                FROM public.suscripcion s JOIN public.plan_saas p ON p.id = s.plan_saas_id
               WHERE s.estado = 'ACTIVA' AND p.estado IS TRUE
               ORDER BY s.empresa_id, s.id DESC
            )
            SELECT count(*) FROM public.empresa e JOIN vigente v ON v.empresa_id = e.id
             WHERE (SELECT count(*) FROM public.sucursal su
                     WHERE su.empresa_id = e.id AND su.estado IS TRUE) > v.max_sucursales_activas
            """
        ).fetchone()[0]
        lineas.append("  empresas_que_exceden = " + str(excedidas))
        lineas.append("")
        lineas.append("PLANES PERSONALIZADOS CREADOS EN LABORATORIO:")
        for fila in conn.execute(
            "SELECT codigo, max_sucursales_activas, permite_reportes_ia, estado "
            "  FROM public.plan_saas WHERE codigo NOT IN ('BASICO','PROFESIONAL') "
            " ORDER BY id"
        ).fetchall():
            lineas.append("  {}|{}|{}|{}".format(*fila))
        ruta = _ruta_unica("EVIDENCIA_PLANES_SUCURSALES_5BO", marca)
        ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        escritas.append(ruta)

    print(json.dumps([str(ruta) for ruta in escritas], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
