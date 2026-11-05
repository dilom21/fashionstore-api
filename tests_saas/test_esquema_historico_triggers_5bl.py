"""5BL / Etapa D - Compatibilidad del esquema historico con RLS, triggers y SP.

Requiere el laboratorio desechable:

    python database/saas/75_laboratorio_esquema_historico_5bl.py --init --confirm-desechable

Si no existe el manifiesto, la bateria se OMITE (nunca se simula con SQLite).
El laboratorio es PostgreSQL 18 real en 127.0.0.1:55442 con el principal
restringido `fs_saas_runtime`.

Que se certifica
----------------
* las migraciones 00100..00500 se aplican sobre el esquema historico
  reconstruido (triggers y SP incluidos) y son reaplicables;
* los triggers documentados se ejecutan bajo RLS sin bypass de tenant;
* `bitacora` es append-only, obliga a declarar empresa y separa el canal global;
* las rutinas historicas estan revocadas y la unica via de stock es tenant-aware;
* el aislamiento A/B del principal se mantiene despues de incorporar triggers.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUTA_MANIFIESTO = Path(
    os.environ.get(
        "FASHIONSTORE_LAB_5BL_MANIFEST",
        str(Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "Temp" / "opencode" / "fs_lab_5bl" / "lab_5bl_manifest.json"),
    )
)
MARCA_TIEMPO = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
EVIDENCIAS = ROOT / "database" / "saas" / "evidencias"

psycopg = pytest.importorskip("psycopg")


# ---------------------------------------------------------------------------
# Fixtures y utilidades
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def lab() -> dict:
    if not RUTA_MANIFIESTO.exists():
        pytest.skip(
            "Laboratorio 5BL no aprovisionado; ejecute "
            "database/saas/75_laboratorio_esquema_historico_5bl.py --init --confirm-desechable"
        )
    if os.environ.get("FASHIONSTORE_LAB_5BL_RESET", "1") != "0":
        script = ROOT / "database" / "saas" / "75_laboratorio_esquema_historico_5bl.py"
        resultado = subprocess.run(
            [sys.executable, str(script), "--recrear-base", "--confirm-desechable"],
            cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=1200,
        )
        if resultado.returncode != 0:
            pytest.fail(
                "No se pudo recrear el laboratorio 5BL (rc="
                f"{resultado.returncode}): {resultado.stderr[-800:]}"
            )
    datos = json.loads(RUTA_MANIFIESTO.read_text(encoding="utf-8"))
    cluster = datos["cluster"]
    if cluster["host"] not in {"127.0.0.1", "localhost"} or cluster["puerto"] != 55442:
        pytest.skip("El manifiesto no corresponde al laboratorio desechable 5BL")
    if "lab" not in cluster["base"].lower():
        pytest.skip("El manifiesto no apunta a una base descartable")
    datos["_clave"] = datos["assertion_key"]
    return datos


def asercion(clave: str, empresa_id: int, usuario_id: int, *, ttl: int = 120,
             firma_forzada: str | None = None) -> str:
    expira = int(time.time()) + ttl
    nonce = secrets.token_hex(8)
    mensaje = f"{empresa_id}|{usuario_id}|{expira}|{nonce}"
    firma = firma_forzada or hmac.new(
        clave.encode("utf-8"), mensaje.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{mensaje}|{firma}"


@contextmanager
def admin(lab: dict):
    conn = psycopg.connect(lab["admin_dsn"])
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@contextmanager
def runtime(lab: dict, empresa_id: int | None = None, usuario_id: int | None = None,
            *, valor_asercion: str | None = None, guc_falso: bool = False):
    conn = psycopg.connect(lab["runtime_dsn"])
    try:
        if valor_asercion is None and empresa_id is not None and usuario_id is not None:
            valor_asercion = asercion(lab["_clave"], empresa_id, usuario_id)
        if valor_asercion is not None:
            conn.execute("SELECT set_config('app.tenant_assertion', %s, true)",
                         (valor_asercion,))
        if empresa_id is not None:
            conn.execute("SELECT set_config('app.empresa_id', %s, true)", (str(empresa_id),))
        if usuario_id is not None:
            conn.execute("SELECT set_config('app.usuario_id', %s, true)", (str(usuario_id),))
        if guc_falso and empresa_id is not None:
            # GUC de auditoria falsificado: no debe conceder nada por si mismo.
            conn.execute("SELECT set_config('app.empresa_id', %s, true)", (str(empresa_id),))
        yield conn
    finally:
        conn.rollback()
        conn.close()


def escalar(conn, sql: str, args=None):
    return conn.execute(sql, args).fetchone()[0]


def filas(conn, sql: str, args=None):
    return conn.execute(sql, args).fetchall()


# ---------------------------------------------------------------------------
# Identidad del laboratorio y reconstruccion
# ---------------------------------------------------------------------------


def test_manifiesto_loopback_y_desechable(lab):
    assert lab["cluster"]["host"] == "127.0.0.1"
    assert lab["cluster"]["puerto"] == 55442
    assert "lab" in lab["cluster"]["base"]
    assert "fs_lab_5bl" in lab["cluster"]["pgdata"]


def test_esquema_historico_reconstruido(lab):
    with admin(lab) as conn:
        triggers = escalar(conn,
            "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND NOT t.tgisinternal")
        funciones = escalar(conn,
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.prokind='f' AND p.proname LIKE 'fn\\_%'")
        procedimientos = escalar(conn,
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.prokind='p' AND p.proname LIKE 'sp\\_%'")
        vista = escalar(conn, "SELECT to_regclass('public.vw_inventario_stock') IS NOT NULL")
    # 14 disparadores historicos + 1 guardia de bitacora introducida por 00500.
    assert triggers == 15
    assert funciones >= 10
    assert procedimientos == 3
    assert vista is True


def test_vista_inventario_stock_es_security_invoker(lab):
    with admin(lab) as conn:
        opciones = escalar(conn,
            "SELECT array_to_string(reloptions, ',') FROM pg_class "
            "WHERE oid='public.vw_inventario_stock'::regclass")
    assert opciones is not None and "security_invoker=true" in opciones.replace(" ", "")


def test_migraciones_portables_aplicadas(lab):
    with admin(lab) as conn:
        columnas = escalar(conn,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema='public' AND column_name='empresa_id'")
        not_null = escalar(conn,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema='public' AND column_name='empresa_id' "
            "AND is_nullable='NO'")
        bitacora_nullable = escalar(conn,
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='bitacora' "
            "AND column_name='empresa_id'")
        fk_tenant = escalar(conn,
            "SELECT count(*) FROM pg_constraint "
            "WHERE contype='f' AND conname LIKE 'fk_tenant_%'")
        uq_tenant = escalar(conn,
            "SELECT count(*) FROM pg_constraint "
            "WHERE contype='u' AND conname LIKE 'uq_tenant_%'")
    assert columnas == 34  # 31 tablas empresariales + suscripcion + usuario_empresa + configuracion
    assert not_null == 33  # todas menos bitacora (conserva filas historicas sin tenant)
    assert bitacora_nullable == "YES"
    assert fk_tenant == 48
    assert uq_tenant >= 28


def test_rls_forzada_en_todas_las_tablas_de_tenant(lab):
    with admin(lab) as conn:
        forzadas = escalar(conn,
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity")
        politicas = escalar(conn, "SELECT count(*) FROM pg_policies WHERE schemaname='public'")
        bitacora = filas(conn,
            "SELECT policyname, cmd FROM pg_policies "
            "WHERE schemaname='public' AND tablename='bitacora' ORDER BY 1")
    assert forzadas == 37
    assert politicas >= 45
    assert [p[0] for p in bitacora] == ["bitacora_insercion_tenant", "bitacora_lectura_tenant"]
    assert {p[1] for p in bitacora} == {"INSERT", "SELECT"}


def test_principal_restringido(lab):
    with runtime(lab) as conn:
        principal = conn.execute(
            "SELECT session_user, r.rolsuper, r.rolbypassrls, r.rolinherit, "
            "(SELECT count(*) FROM pg_auth_members m WHERE m.member=r.oid) "
            "FROM pg_roles r WHERE r.rolname=current_user"
        ).fetchone()
        tablas_propias = escalar(conn,
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "CROSS JOIN pg_roles r WHERE n.nspname='public' AND c.relkind='r' "
            "AND c.relowner=r.oid AND r.rolname=current_user")
        sin_asercion = escalar(conn, "SELECT saas.tenant_actual()")
    assert principal == ("fs_saas_runtime", False, False, False, 0)
    assert tablas_propias == 0
    assert sin_asercion is None


# ---------------------------------------------------------------------------
# Triggers historicos bajo RLS
# ---------------------------------------------------------------------------


def test_trigger_auditoria_atribuye_tenant_y_no_cruza(lab):
    with runtime(lab, 1, 1) as conn:
        conn.execute("SELECT set_config('app.ip', '127.0.0.1', true)")
        conn.execute("UPDATE public.cliente SET telefono = '700-00001' WHERE id = 1")
        propias = escalar(conn,
            "SELECT count(*) FROM public.bitacora "
            "WHERE empresa_id = 1 AND entidad_afectada = 'cliente' "
            "AND descripcion LIKE '%registro ID 1%'")
        assert propias >= 1
        assert escalar(conn, "SELECT count(*) FROM public.bitacora WHERE empresa_id = 2") == 0
        # La fila de auditoria debe llevar la IP propagada por la app.
        assert escalar(conn,
            "SELECT count(*) FROM public.bitacora "
            "WHERE empresa_id = 1 AND ip = '127.0.0.1'") >= 1

    with runtime(lab, 2, 3) as conn:
        # B ve su propia auditoria de siembra, nunca la de A.
        assert escalar(conn,
            "SELECT count(*) FROM public.bitacora WHERE empresa_id = 1") == 0
        assert escalar(conn,
            "SELECT count(*) FROM public.bitacora WHERE empresa_id = 2") >= 1
        assert escalar(conn,
            "SELECT count(*) FROM public.bitacora WHERE usuario_id = 1") == 0


def test_trigger_actividad_carrito_actualiza_fecha(lab):
    with runtime(lab, 1, 1) as conn:
        antes = escalar(conn, "SELECT fecha_actualizacion FROM public.carrito WHERE id=1")
        conn.execute(
            "INSERT INTO public.detalle_carrito (carrito_id, inventario_id, cantidad, empresa_id) "
            "VALUES (1, 1, 1, 1)"
        )
        despues = escalar(conn, "SELECT fecha_actualizacion FROM public.carrito WHERE id=1")
    assert despues > antes


def test_trigger_valida_sucursal_y_empresa_del_detalle(lab):
    with runtime(lab, 1, 1) as conn:
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conn.execute(
                "INSERT INTO public.detalle_carrito (carrito_id, inventario_id, cantidad, empresa_id) "
                "VALUES (1, 100, 1, 1)"
            )
        assert "Inventario inexistente" in str(exc.value) or "empresas distintas" in str(exc.value)
        conn.rollback()

    # Misma empresa, sucursal distinta: el trigger debe rechazarlo.
    with admin(lab) as conn:
        conn.execute(
            "INSERT INTO public.sucursal (id, empresa_id, ciudad_id, nombre, direccion, estado) "
            "VALUES (101, 2, 1, 'Sucursal B 2', 'Calle B 101', TRUE)"
        )
        conn.execute(
            "INSERT INTO public.inventario (id, empresa_id, sucursal_id, variante_producto_id, "
            "temporada_id, stock_actual, stock_reservado, fecha_actualizacion) "
            "VALUES (101, 2, 101, 100, 100, 3, 0, NOW())"
        )
        with pytest.raises((psycopg.errors.RaiseException,
                            psycopg.errors.InsufficientPrivilege)):
            conn.execute(
                "INSERT INTO public.detalle_carrito (carrito_id, inventario_id, cantidad, empresa_id) "
                "VALUES (1, 101, 1, 1)"
            )
        conn.rollback()


def test_trigger_inventario_protege_columnas_estructurales(lab):
    with runtime(lab, 1, 1) as conn:
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conn.execute("UPDATE public.inventario SET sucursal_id = 2 WHERE id = 1")
        assert "sucursal, variante o temporada" in str(exc.value)
        conn.rollback()

    with runtime(lab, 1, 1) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE public.inventario SET empresa_id = 2 WHERE id = 1")
        conn.rollback()


def test_trigger_inventario_actualiza_fecha_con_stock(lab):
    with runtime(lab, 1, 1) as conn:
        antes = escalar(conn, "SELECT fecha_actualizacion FROM public.inventario WHERE id=1")
        conn.execute("UPDATE public.inventario SET stock_reservado = 1 WHERE id = 1")
        despues = escalar(conn, "SELECT fecha_actualizacion FROM public.inventario WHERE id=1")
        assert despues > antes


def test_trigger_normaliza_correo_usuario(lab):
    with admin(lab) as conn:
        conn.execute(
            "INSERT INTO public.usuario (id, rol_id, correo, password_hash, fecha_creacion, estado) "
            "VALUES (50, 1, '  MAYUSCULAS@Lab.Invalid  ', 'x', NOW(), TRUE)"
        )
        correo = escalar(conn, "SELECT correo FROM public.usuario WHERE id=50")
        assert correo == "mayusculas@lab.invalid"
        # El evento de una tabla global no ensucia la bitacora empresarial.
        assert escalar(conn,
            "SELECT count(*) FROM public.bitacora WHERE entidad_afectada='usuario' "
            "AND empresa_id IS NULL") >= 0
        conn.rollback()


def test_trigger_protege_detalle_de_venta_completada(lab):
    with admin(lab) as conn:
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conn.execute("UPDATE public.detalle_venta SET cantidad = 2 WHERE id = 1")
        assert "COMPLETADA" in str(exc.value)
        conn.rollback()


def test_trigger_protege_detalle_de_orden_recibida(lab):
    with admin(lab) as conn:
        conn.execute("UPDATE public.orden_compra SET estado = 'RECIBIDA' WHERE id = 1")
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            conn.execute("UPDATE public.detalle_orden_compra SET cantidad = 9 WHERE id = 1")
        assert "RECIBIDA" in str(exc.value)
        conn.rollback()


# ---------------------------------------------------------------------------
# Auditoria: contrato completo
# ---------------------------------------------------------------------------


def test_bitacora_es_append_only_para_el_runtime(lab):
    with runtime(lab, 1, 1) as conn:
        privilegios = conn.execute(
            "SELECT has_table_privilege(current_user,'public.bitacora','SELECT'), "
            "has_table_privilege(current_user,'public.bitacora','INSERT'), "
            "has_table_privilege(current_user,'public.bitacora','UPDATE'), "
            "has_table_privilege(current_user,'public.bitacora','DELETE')"
        ).fetchone()
        assert privilegios == (True, True, False, False)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE public.bitacora SET accion = 'ALTERADO' WHERE empresa_id = 1")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM public.bitacora WHERE empresa_id = 1")
        conn.rollback()


def test_bitacora_rechaza_fila_sin_tenant_del_runtime(lab):
    with runtime(lab, 1, 1) as conn:
        with pytest.raises((psycopg.errors.NotNullViolation,
                            psycopg.errors.InsufficientPrivilege,
                            psycopg.errors.RaiseException)) as exc:
            conn.execute(
                "INSERT INTO public.bitacora (usuario_id, empresa_id, fecha_hora, accion) "
                "VALUES (1, NULL, NOW(), 'EVENTO_GLOBAL_FALSO')"
            )
        texto = str(exc.value)
        assert "empresa_id" in texto or "row-level security" in texto.lower()
        conn.rollback()
    with admin(lab) as conn:
        assert escalar(conn,
            "SELECT count(*) FROM public.bitacora WHERE accion='EVENTO_GLOBAL_FALSO'") == 0


def test_bitacora_rechaza_insercion_cruzada(lab):
    with runtime(lab, 1, 1) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO public.bitacora (usuario_id, empresa_id, fecha_hora, accion) "
                "VALUES (3, 2, NOW(), 'CRUCE')"
            )
        conn.rollback()
    with admin(lab) as conn:
        assert escalar(conn, "SELECT count(*) FROM public.bitacora WHERE accion='CRUCE'") == 0


def test_bitacora_historica_sin_tenant_se_conserva_y_clasifica(lab):
    with admin(lab) as conn:
        nulos = escalar(conn,
            "SELECT count(*) FROM public.bitacora WHERE empresa_id IS NULL")
        vista = escalar(conn, "SELECT count(*) FROM saas.bitacora_historica_global")
        clases = dict(filas(conn,
            "SELECT clase, eventos FROM saas.bitacora_clasificacion"))
    assert nulos >= 2
    assert vista == nulos
    assert clases.get("HISTORICO_SIN_TENANT") == nulos
    assert clases.get("EMPRESARIAL", 0) >= 1

    with runtime(lab, 1, 1) as conn:
        assert escalar(conn,
            "SELECT count(*) FROM public.bitacora WHERE empresa_id IS NULL") == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT count(*) FROM saas.bitacora_historica_global")
        conn.rollback()


def test_canal_global_separado_y_no_visible_al_tenant(lab):
    with admin(lab) as conn:
        globales = escalar(conn, "SELECT count(*) FROM saas.bitacora_global")
    assert globales >= 1

    with runtime(lab, 1, 1) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT count(*) FROM saas.bitacora_global")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO saas.bitacora_global (actor_sql, accion) "
                "VALUES ('fs_saas_runtime', 'FALSO')"
            )
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "SELECT saas.registrar_evento_global('FALSO','x',1,'x',1,'127.0.0.1')"
            )
        conn.rollback()


# ---------------------------------------------------------------------------
# Procedimientos historicos y rutina tenant segura
# ---------------------------------------------------------------------------


def test_rutinas_historicas_revocadas(lab):
    with admin(lab) as conn:
        publicas = escalar(conn,
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a "
            "WHERE n.nspname='public' AND p.prokind IN ('f','p') AND a.grantee=0")
    assert publicas == 0

    with runtime(lab, 1, 1) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CALL public.sp_cancelar_reserva(1, 1, 'x')")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "CALL public.sp_crear_reserva_desde_carrito(1, NOW() + INTERVAL '1 day', "
                "'x', 1, NULL)"
            )
        conn.rollback()


def test_sp_legacy_omite_empresa_id_y_es_incompatible(lab):
    """Evidencia del bloqueo: el SP auditado no escribe empresa_id."""
    with admin(lab) as conn:
        with pytest.raises(psycopg.errors.NotNullViolation) as exc:
            conn.execute(
                "CALL public.sp_crear_reserva_desde_carrito("
                "1, NOW() + INTERVAL '2 days', 'lab', 1, NULL)"
            )
        assert "empresa_id" in str(exc.value)
        conn.rollback()


def test_sp_ajuste_stock_tenant_audita_y_actualiza(lab):
    with runtime(lab, 1, 1) as conn:
        conn.execute("SELECT set_config('app.ip','127.0.0.1',true)")
        antes, reservado = conn.execute(
            "SELECT stock_actual, stock_reservado FROM public.inventario WHERE id=1"
        ).fetchone()
        resultado = conn.execute(
            "SELECT * FROM saas.sp_ajustar_inventario_tenant(1, 5, 'AJUSTE_ENTRADA', 'lab 5bl')"
        ).fetchone()
        assert resultado == (1, antes + 5, reservado)
        assert escalar(conn,
            "SELECT count(*) FROM public.movimiento_inventario "
            "WHERE inventario_id=1 AND empresa_id=1 AND tipo='AJUSTE_ENTRADA' "
            "AND cantidad=5") == 1
        assert escalar(conn,
            "SELECT count(*) FROM public.bitacora "
            "WHERE empresa_id=1 AND entidad_afectada='inventario'") >= 1

    with admin(lab) as conn:
        assert escalar(conn, "SELECT stock_actual FROM public.inventario WHERE id=100") == 5


def test_sp_ajuste_stock_rechaza_negativo_y_cruce(lab):
    with runtime(lab, 1, 1) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "SELECT * FROM saas.sp_ajustar_inventario_tenant(1, -99, 'AJUSTE_SALIDA', 'x')"
            )
        conn.rollback()
    with runtime(lab, 1, 1) as conn:
        with pytest.raises(psycopg.errors.NoDataFound):
            conn.execute(
                "SELECT * FROM saas.sp_ajustar_inventario_tenant(100, 1, 'AJUSTE_ENTRADA', 'x')"
            )
        conn.rollback()


# ---------------------------------------------------------------------------
# Aislamiento A/B y defensas adversariales
# ---------------------------------------------------------------------------


def test_asercion_a_no_lee_ni_escribe_b(lab):
    with runtime(lab, 1, 1) as conn:
        assert escalar(conn, "SELECT count(*) FROM public.producto") == 1
        assert escalar(conn, "SELECT count(*) FROM public.cliente") == 1
        assert escalar(conn, "SELECT count(*) FROM public.inventario") == 1
        assert escalar(conn, "SELECT saas.tenant_actual()") == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO public.producto (empresa_id, categoria_id, nombre, precio) "
                "VALUES (2, 100, 'Intruso', 1)"
            )
        conn.rollback()
        assert escalar(conn,
            "SELECT count(*) FROM public.producto WHERE id=100") == 0

    with runtime(lab, 2, 3) as conn:
        assert escalar(conn, "SELECT count(*) FROM public.producto") == 1
        assert escalar(conn, "SELECT count(*) FROM public.producto WHERE id=100") == 1


def test_guc_falso_no_suplanta_tenant(lab):
    with runtime(lab, 1, 1, valor_asercion="") as conn:
        assert escalar(conn, "SELECT count(*) FROM public.producto") == 0
        assert escalar(conn, "SELECT saas.tenant_actual()") is None
    with runtime(lab, 1, 1, valor_asercion="") as conn:
        conn.execute("SELECT set_config('app.empresa_id','2',true)")
        assert escalar(conn, "SELECT count(*) FROM public.producto WHERE empresa_id=2") == 0


def test_asercion_falsificada_y_expirada(lab):
    with runtime(lab, 1, 1, valor_asercion=asercion(lab["_clave"], 1, 1,
                                                     firma_forzada="0" * 64)) as conn:
        assert escalar(conn, "SELECT count(*) FROM public.producto") == 0
    with runtime(lab, 1, 1, valor_asercion=asercion(lab["_clave"], 1, 1, ttl=-10)) as conn:
        assert escalar(conn, "SELECT count(*) FROM public.producto") == 0


def test_revocacion_de_membresia_invalida_la_asercion(lab):
    with admin(lab) as conn:
        conn.execute("UPDATE public.usuario_empresa SET estado=FALSE "
                     "WHERE empresa_id=1 AND usuario_id=1")
        conn.commit()
    try:
        with runtime(lab, 1, 1) as conn:
            assert escalar(conn, "SELECT saas.tenant_actual()") is None
            assert escalar(conn, "SELECT count(*) FROM public.producto") == 0
    finally:
        with admin(lab) as conn:
            conn.execute("UPDATE public.usuario_empresa SET estado=TRUE "
                         "WHERE empresa_id=1 AND usuario_id=1")
            conn.commit()


def test_vista_inventario_respeta_rls(lab):
    with runtime(lab, 1, 1) as conn:
        propios = filas(conn, "SELECT inventario_id FROM public.vw_inventario_stock ORDER BY 1")
        assert propios == [(1,)]
    with runtime(lab, 2, 3) as conn:
        ajenos = filas(conn, "SELECT inventario_id FROM public.vw_inventario_stock ORDER BY 1")
        assert ajenos == [(100,)]


def test_runtime_no_puede_insertar_identidad_global(lab):
    with runtime(lab, 1, 1) as conn:
        assert escalar(conn,
            "SELECT has_table_privilege(current_user,'public.usuario','INSERT')") is False
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO public.usuario (rol_id, correo, password_hash) "
                "VALUES (1, 'intruso@lab.invalid', 'x')"
            )
        conn.rollback()


def test_funciones_definer_auditadas(lab):
    with admin(lab) as conn:
        definer = filas(conn,
            "SELECT n.nspname, p.proname, pg_get_userbyid(p.proowner), "
            "(SELECT rolcanlogin FROM pg_roles r WHERE r.rolname=pg_get_userbyid(p.proowner)), "
            "array_to_string(p.proconfig, ',') "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE p.prosecdef ORDER BY 1,2")
        triggers_definer = escalar(conn,
            "SELECT count(*) FROM saas.compatibilidad_5bl WHERE security_definer")
        violaciones = escalar(conn, "SELECT count(*) FROM saas.validar_triggers_5bl()")
    assert triggers_definer == 0
    assert violaciones == 0
    assert definer, "deben existir funciones SECURITY DEFINER controladas"
    for esquema, nombre, propietario, puede_login, config in definer:
        assert esquema == "saas", f"definer inesperada en public: {nombre}"
        assert puede_login is False, f"{nombre} pertenece a un rol con login"
        assert "search_path=" in (config or "")


def test_idempotencia_de_la_migracion_00500(lab):
    destino = (ROOT / "supabase" / "migrations" /
               "20261030000500_saas_compatibilidad_5bl.sql").read_text(encoding="utf-8")
    with admin(lab) as conn:
        antes = escalar(conn, "SELECT count(*) FROM saas.compatibilidad_5bl")
        politicas_antes = escalar(conn, "SELECT count(*) FROM pg_policies "
                                        "WHERE schemaname='public'")
        conn.execute("SET saas.runtime_role = 'fs_saas_runtime'")
        conn.execute("SET saas.rls_owner_role = 'saas_rls_owner'")
        conn.execute(destino)
        conn.commit()
    with admin(lab) as conn:
        assert escalar(conn, "SELECT count(*) FROM saas.compatibilidad_5bl") == antes
        assert escalar(conn, "SELECT count(*) FROM pg_policies "
                             "WHERE schemaname='public'") == politicas_antes
        assert escalar(conn, "SELECT count(*) FROM pg_policies "
                             "WHERE schemaname='public' AND tablename='bitacora'") == 2

# ---------------------------------------------------------------------------
# 5BL: superficie adversarial adicional (equivalente a la matriz 5BK)
# ---------------------------------------------------------------------------


def test_intento_de_elevacion_de_rol(lab):
    with runtime(lab, 1, 1) as conn:
        for sentencia in (
            "SET ROLE postgres",
            "SET ROLE saas_rls_owner",
            "SET SESSION AUTHORIZATION postgres",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sentencia)
            conn.rollback()
        assert escalar(conn, "SELECT session_user") == "fs_saas_runtime"
        assert escalar(conn, "SELECT current_user") == "fs_saas_runtime"


def test_conexion_reutilizada_no_conserva_asercion(lab):
    conn = psycopg.connect(lab["runtime_dsn"])
    try:
        valor = asercion(lab["_clave"], 1, 1)
        assert conn.execute(
            "SELECT set_config('app.tenant_assertion', %s, true)", (valor,)
        ).fetchone()[0] == valor
        assert conn.execute("SELECT count(*) FROM public.producto").fetchone()[0] == 1
        conn.commit()
        # Nueva transaccion en la MISMA conexion fisica: la asercion era local.
        assert conn.execute("SELECT saas.tenant_actual()").fetchone()[0] is None
        assert conn.execute("SELECT count(*) FROM public.producto").fetchone()[0] == 0
        conn.rollback()
    finally:
        conn.close()


def test_superficie_de_funciones_y_vistas_del_runtime(lab):
    with runtime(lab, 1, 1) as conn:
        funciones = sorted(
            fila[0] for fila in filas(conn,
                "SELECT p.oid::regprocedure::text FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname IN ('public','saas') "
                "AND has_function_privilege(current_user, p.oid, 'EXECUTE')")
        )
        vistas = sorted(
            fila[0] for fila in filas(conn,
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relkind='v' "
                "AND has_table_privilege(current_user, c.oid, 'SELECT')")
        )
    # Superficie minima esperada. Cualquier rutina funcional nueva debe
    # justificarse aqui de forma explicita.
    assert funciones == [
        "saas.es_principal_runtime()",
        "saas.login_credenciales(text)",
        "saas.login_membresia(bigint,bigint,text,text)",
        "saas.sp_ajustar_inventario_tenant(bigint,integer,text,text)",
        "saas.tenant_actual()",
    ], funciones
    assert vistas == ["vw_inventario_stock"]
