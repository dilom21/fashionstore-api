"""Pruebas críticas 5CB contra el clúster desechable (sin dobles).

Sólo se ejecutan los bloques que el alcance 5CB exige:

* JWT/identidad (``ID-*``), ACL (``ACL-*``), aislamiento RLS (``AIS-*``),
  reservas por SP (``SP-*``), reutilización de pool (``POOL-*``) y bootstrap
  mínimo (incluido en ``ACL-11``/``ACL-12``).
* Bitácora de ``categoria`` (``BIT-*``) -- el hallazgo que motivó 5CB: un único
  evento por escritura y ``empresa_id`` tomado siempre de la fila afectada.
* Sonda de la aplicación (``SONDA-*``): el código real de
  ``app/core/bitacora_trigger.py`` ejecutado contra este clúster.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg


@dataclass
class Ctx:
    dsn: dict[str, str]
    secreto: str
    tokens: dict[str, str]


def abrir(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, connect_timeout=10)


def claims_de(token: str) -> dict:
    """Decodifica el payload del JWT del propio laboratorio (sin verificar)."""
    import base64
    import json

    cuerpo = token.split(".")[1]
    relleno = "=" * (-len(cuerpo) % 4)
    return json.loads(base64.urlsafe_b64decode(cuerpo + relleno))


def tokenizar(cur, token: str) -> None:
    """Replica lo que hace app/core/tenant_dependencies.py tras validar el JWT.

    Fija ``app.actor_token`` (lo que valida la base) y ``app.usuario_id`` (lo que
    consume fn_registrar_bitacora). Ninguno se hereda de la petición HTTP.
    """
    cur.execute("SELECT set_config('app.actor_token', %s, true)", (token,))
    try:
        sub = claims_de(token).get("sub", "")
    except Exception:  # noqa: BLE001 - tokens deliberadamente corruptos
        sub = ""
    cur.execute("SELECT set_config('app.usuario_id', %s, true)", (str(sub),))


def uno(cur) -> object:
    fila = cur.fetchone()
    return None if fila is None else fila[0]


def exigir(condicion: bool, mensaje: str):
    if not condicion:
        raise AssertionError(mensaje)
    return mensaje


class _Sesion:
    """Transacción única con token opcional; siempre hace rollback al salir."""

    def __init__(self, ctx: Ctx, rol: str, token: str | None) -> None:
        self.ctx, self.rol, self.token = ctx, rol, token
        self.conexion = None

    def __enter__(self):
        self.conexion = abrir(self.ctx.dsn[self.rol])
        cur = self.conexion.cursor()
        if self.token:
            tokenizar(cur, self.token)
        return cur

    def __exit__(self, tipo, valor, traza):
        self.conexion.rollback()
        self.conexion.close()
        return False


def sesion(ctx: Ctx, rol: str, token: str | None = None) -> _Sesion:
    return _Sesion(ctx, rol, token)


def escalar(ctx: Ctx, rol: str, token: str | None, sentencia: str,
            parametros: tuple = ()) -> object:
    with sesion(ctx, rol, token) as cur:
        cur.execute(sentencia, parametros)
        return uno(cur)


def intentar(ctx: Ctx, rol: str, token: str | None, sentencia: str) -> object:
    with sesion(ctx, rol, token) as cur:
        cur.execute(sentencia)
        return cur.fetchall()


def _admin(ctx: Ctx, sentencia: str, parametros: tuple = ()) -> None:
    with abrir(ctx.dsn["admin"]) as conexion:
        with conexion.cursor() as cur:
            cur.execute(sentencia, parametros)
        conexion.commit()


def actor_con(ctx: Ctx, token: str) -> tuple[int, int]:
    with sesion(ctx, "api", token) as cur:
        cur.execute("SELECT fashionstore_private.actor_empresa_id(),"
                    "       fashionstore_private.actor_usuario_id()")
        return cur.fetchone()


# -- estructura, triggers y auditoría -------------------------------------
CONSULTA_TRIGGER_CATEGORIA = """
SELECT count(*), coalesce(min(pg_get_triggerdef(t.oid)), '')
FROM pg_catalog.pg_trigger t
JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'categoria'
  AND t.tgname = 'trg_bitacora_categoria' AND NOT t.tgisinternal
"""


def _identidad_login(ctx: Ctx) -> str:
    with sesion(ctx, "api", ctx.tokens["a1"]) as cur:
        cur.execute("SELECT session_user, current_user,"
                    " (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)")
        usuario, actual, bypass = cur.fetchone()
        exigir(usuario.startswith("fashionstore_5cb_"), f"session_user={usuario}")
        exigir(not bypass, "el login API tiene BYPASSRLS")
        return f"session_user={usuario} current_user={actual} bypassrls={bypass}"


def pruebas_estructura(ctx: Ctx, ev, resumen: dict, instalacion: dict) -> None:
    ev.espera("EST-01", "46 tablas y 145 FK validadas, igual que la etapa 2",
              lambda: exigir(
                  resumen["tablas"] == 46 and resumen["fk"] == 145
                  and resumen["fk_no_validadas"] == 0,
                  f"{resumen['tablas']} tablas, {resumen['fk']} FK válidas, "
                  f"{resumen['fk_no_validadas']} sin validar"))
    ev.espera("EST-02", "FORCE RLS en las 46 tablas y políticas instaladas",
              lambda: exigir(
                  resumen["tablas_force_rls"] == 46 and resumen["politicas"] > 100,
                  f"force={resumen['tablas_force_rls']} "
                  f"políticas={resumen['politicas']}"))
    ev.espera("EST-03",
              "categoria NO tenía trigger de auditoría antes de 040 y tiene 1 después",
              lambda: exigir(
                  instalacion["trigger_categoria_antes"] == 0
                  and instalacion["trigger_categoria_despues"] == 1,
                  f"antes={instalacion['trigger_categoria_antes']} "
                  f"después={instalacion['trigger_categoria_despues']}"))
    ev.espera("EST-04", "Re-ejecutar el bloque DO de 040 no duplica el trigger",
              lambda: exigir(instalacion["trigger_categoria_idempotente"] is True,
                             "2 ejecuciones del bloque literal -> 1 trigger"))

    def definicion():
        with abrir(ctx.dsn["admin"]) as conexion, conexion.cursor() as cur:
            cur.execute(CONSULTA_TRIGGER_CATEGORIA)
            total, texto = cur.fetchone()
        exigir(total == 1, f"triggers={total}")
        # pg_get_triggerdef enumera los eventos en orden alfabético
        # (INSERT OR DELETE OR UPDATE); se compara la semántica, no el orden.
        normal = " ".join(texto.split()).replace(
            "INSERT OR DELETE OR UPDATE", "INSERT OR UPDATE OR DELETE")
        exigir("trg_bitacora_categoria AFTER INSERT OR UPDATE OR DELETE ON "
               "public.categoria" in normal, texto)
        exigir("FOR EACH ROW" in normal, texto)
        exigir("fn_registrar_bitacora()" in normal, texto)
        return texto
    ev.espera("EST-05", "El trigger es AFTER INSERT/UPDATE/DELETE con fn_registrar_bitacora()",
              definicion)

    def owner_auditoria():
        with abrir(ctx.dsn["admin"]) as conexion, conexion.cursor() as cur:
            cur.execute(
                "SELECT p.prosecdef, pg_get_userbyid(p.proowner), r.rolcanlogin,"
                " coalesce(array_to_string(p.proconfig, ','), '')"
                " FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner"
                " WHERE p.oid = 'public.fn_registrar_bitacora()'::regprocedure")
            definer, owner, puede_login, config = cur.fetchone()
        exigir(definer is True, "fn_registrar_bitacora no es SECURITY DEFINER")
        exigir(owner == "fashionstore_audit_owner", f"owner={owner}")
        exigir(puede_login is False, f"{owner} puede iniciar sesión")
        exigir("search_path=" in config and "public" in config,
               f"search_path={config}")
        return f"definer={definer} owner={owner} login={puede_login} {config}"
    ev.espera("EST-06", "La auditoría la escribe un owner NOLOGIN con search_path fijo",
              owner_auditoria)

    def roles_seguros():
        with abrir(ctx.dsn["admin"]) as conexion, conexion.cursor() as cur:
            cur.execute("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles"
                        " WHERE rolname LIKE 'fashionstore%'")
            roles = {f[0]: (f[1], f[2]) for f in cur.fetchall()}
        sospechosos = {r: v for r, v in roles.items() if v[0] or v[1]}
        exigir(not sospechosos, f"roles con privilegios: {sospechosos}")
        return f"{len(roles)} roles fashionstore sin SUPERUSER ni BYPASSRLS"
    ev.espera("EST-07", "Ningún rol fashionstore es SUPERUSER ni tiene BYPASSRLS",
              roles_seguros)
    ev.espera("EST-08", "Conexión API autentica con login propio y sin BYPASSRLS",
              lambda: _identidad_login(ctx))
    ev.espera("EST-09", "La API ve exactamente una empresa (la de su token)",
              lambda: exigir(
                  escalar(ctx, "api", ctx.tokens["a1"],
                          "SELECT count(*) FROM public.empresa") == 1,
                  "1 empresa visible para A1"))


# -- identidad JWT ---------------------------------------------------------
def _token(ctx: Ctx, usuario: int, empresa: int, contexto: str = "cliente",
           **extra) -> str:
    from lab_comun import claims_tenant, jwt_hs256

    return jwt_hs256(ctx.secreto, claims_tenant(usuario, empresa, contexto, **extra))


def _token_sin(ctx: Ctx, campo: str) -> str:
    from lab_comun import claims_tenant, jwt_hs256

    claims = claims_tenant(101, 1, "cliente")
    claims.pop(campo, None)
    return jwt_hs256(ctx.secreto, claims)


def _token_scope(ctx: Ctx) -> str:
    from lab_comun import claims_tenant, jwt_hs256

    claims = claims_tenant(101, 1, "cliente")
    claims["scope"] = "platform"
    return jwt_hs256(ctx.secreto, claims)


def _token_iat_futuro(ctx: Ctx) -> str:
    from datetime import datetime, timedelta, timezone

    from lab_comun import claims_tenant, jwt_hs256

    claims = claims_tenant(101, 1, "cliente")
    claims["iat"] = int((datetime.now(timezone.utc)
                         + timedelta(minutes=10)).timestamp())
    return jwt_hs256(ctx.secreto, claims)


def jwt_sin_firma(ctx: Ctx) -> str:
    """Firma calculada con cabecera alg=none: debe rechazarse por el algoritmo."""
    import base64
    import hashlib
    import hmac as hmac_mod
    import json

    from lab_comun import claims_tenant

    cabecera = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    cuerpo = base64.urlsafe_b64encode(
        json.dumps(claims_tenant(101, 1, "cliente")).encode()).rstrip(b"=").decode()
    firma = hmac_mod.new(ctx.secreto.encode(), f"{cabecera}.{cuerpo}".encode(),
                         hashlib.sha256).digest()
    return (f"{cabecera}.{cuerpo}."
            f"{base64.urlsafe_b64encode(firma).rstrip(b'=').decode()}")


def _membresia_revocada(ctx: Ctx) -> str:
    exigir(escalar(ctx, "api", ctx.tokens["a3"],
                   "SELECT count(*) FROM public.producto") == 1,
           "línea base: a3 debía leer el catálogo antes de la revocación")
    _admin(ctx, "UPDATE public.usuario_empresa SET estado = false "
                "WHERE usuario_id = 103 AND empresa_id = 1")
    try:
        try:
            escalar(ctx, "api", ctx.tokens["a3"], "SELECT count(*) FROM public.producto")
        except psycopg.Error as exc:
            exigir(exc.sqlstate == "42501", f"sqlstate={exc.sqlstate}")
            return "42501 en la sentencia siguiente a la revocación"
        raise AssertionError("el token siguió siendo aceptado tras revocar la membresía")
    finally:
        _admin(ctx, "UPDATE public.usuario_empresa SET estado = true "
                    "WHERE usuario_id = 103 AND empresa_id = 1")


def _suscripcion_suspendida(ctx: Ctx) -> str:
    _admin(ctx, "UPDATE public.suscripcion SET estado = 'SUSPENDIDA' "
                "WHERE empresa_id = 2")
    try:
        try:
            escalar(ctx, "api", ctx.tokens["b1"], "SELECT count(*) FROM public.producto")
        except psycopg.Error as exc:
            exigir(exc.sqlstate == "42501", f"sqlstate={exc.sqlstate}")
            return "42501 con la suscripción de la empresa B suspendida"
        raise AssertionError("el token de B siguió válido sin suscripción ACTIVA")
    finally:
        _admin(ctx, "UPDATE public.suscripcion SET estado = 'ACTIVA' "
                    "WHERE empresa_id = 2")


def _rotacion_clave(ctx: Ctx) -> str:
    from lab_comun import claims_tenant, jwt_hs256

    nueva = "rotacion-" + ctx.secreto[-12:]
    viejo = ctx.tokens["a1"]
    _admin(ctx, "INSERT INTO vault.secrets(name, secret) VALUES (%s, %s) "
                "ON CONFLICT (name) DO UPDATE SET secret = EXCLUDED.secret",
           ("fashionstore_tenant_jwt_hs256_previous", ctx.secreto))
    _admin(ctx, "UPDATE vault.secrets SET secret = %s "
                "WHERE name = 'fashionstore_tenant_jwt_hs256_active'", (nueva,))
    try:
        exigir(actor_con(ctx, viejo) == (1, 101),
               "el token de la clave anterior debe seguir válido durante la rotación")
        token_nuevo = jwt_hs256(nueva, claims_tenant(101, 1, "personal"))
        exigir(actor_con(ctx, token_nuevo) == (1, 101),
               "el token firmado con la clave nueva debe ser válido")
        _admin(ctx, "DELETE FROM vault.secrets "
                    "WHERE name = 'fashionstore_tenant_jwt_hs256_previous'")
        try:
            actor_con(ctx, viejo)
        except psycopg.Error as exc:
            exigir(exc.sqlstate == "42501", f"sqlstate={exc.sqlstate}")
            return "clave previa aceptada mientras existe y revocada al eliminarla"
        raise AssertionError("el token de la clave retirada siguió siendo válido")
    finally:
        _admin(ctx, "DELETE FROM vault.secrets "
                    "WHERE name = 'fashionstore_tenant_jwt_hs256_previous'")
        _admin(ctx, "UPDATE vault.secrets SET secret = %s "
                    "WHERE name = 'fashionstore_tenant_jwt_hs256_active'",
               (ctx.secreto,))


def pruebas_identidad(ctx: Ctx, ev) -> None:
    consulta = "SELECT count(*) FROM public.producto"

    ev.rechazo("ID-01", "Sin token, toda consulta empresarial falla cerrada",
               lambda: escalar(ctx, "api", None, consulta), sqlstate="42501")
    ev.espera("ID-02", "Token válido A1 deriva empresa y usuario del JWT firmado",
              lambda: exigir(actor_con(ctx, ctx.tokens["a1"]) == (1, 101),
                             "actor=(empresa 1, usuario 101)"))

    def guc_manipulado():
        with sesion(ctx, "api", ctx.tokens["a1"]) as cur:
            cur.execute("SELECT set_config('app.empresa_id','9223372036854775807',true)")
            cur.execute("SELECT set_config('app.usuario_id','9223372036854775807',true)")
            cur.execute("SELECT fashionstore_private.actor_empresa_id(),"
                        "       fashionstore_private.actor_usuario_id(),"
                        "       (SELECT count(*) FROM public.producto)")
            return cur.fetchone()
    ev.espera("ID-03", "Manipular app.empresa_id/app.usuario_id NO cambia la identidad",
              lambda: exigir(guc_manipulado() == (1, 101, 1),
                             "actor sigue (1,101) y 1 producto visible"))

    alterado = ctx.tokens["a1"][:-2] + ("AA" if ctx.tokens["a1"][-2:] != "AA" else "BB")
    ev.rechazo("ID-04", "Token con firma alterada", lambda: escalar(
        ctx, "api", alterado, consulta), sqlstate="42501")
    ev.rechazo("ID-05", "Token expirado", lambda: escalar(
        ctx, "api", _token(ctx, 101, 1, minutos=-5), consulta), sqlstate="42501")
    ev.rechazo("ID-06", "Token con cabecera alg=none", lambda: escalar(
        ctx, "api", jwt_sin_firma(ctx), consulta), sqlstate="42501")
    ev.rechazo("ID-07", "Token sin jti", lambda: escalar(
        ctx, "api", _token_sin(ctx, "jti"), consulta), sqlstate="42501")
    ev.rechazo("ID-08", "Token con scope distinto de tenant", lambda: escalar(
        ctx, "api", _token_scope(ctx), consulta), sqlstate="42501")
    ev.rechazo("ID-09", "Token de A1 declarando una empresa donde no es miembro",
               lambda: escalar(ctx, "api", _token(ctx, 101, 2, "personal"), consulta),
               sqlstate="42501")
    ev.rechazo("ID-10", "Token de usuario inexistente", lambda: escalar(
        ctx, "api", _token(ctx, 999, 1), consulta), sqlstate="42501")
    ev.rechazo("ID-11", "Token con iat en el futuro", lambda: escalar(
        ctx, "api", _token_iat_futuro(ctx), consulta), sqlstate="42501")
    ev.rechazo("ID-12", "Token desmesurado (>8192 caracteres)", lambda: escalar(
        ctx, "api", "x" * 9000, consulta), sqlstate="42501")
    ev.rechazo("ID-13", "Contexto 'personal' con un usuario que sólo es cliente",
               lambda: escalar(ctx, "api", _token(ctx, 102, 1, "personal"), consulta),
               sqlstate="42501")
    ev.espera("ID-14",
              "Revocar la membresía invalida el token vigente en la siguiente sentencia",
              lambda: _membresia_revocada(ctx))
    ev.espera("ID-15", "Suspender la suscripción invalida el token vigente",
              lambda: _suscripcion_suspendida(ctx))
    ev.espera("ID-16", "Rotación de clave: el token previo sigue válido con 'previous'",
              lambda: _rotacion_clave(ctx))


# -- ACL: mínimo privilegio ------------------------------------------------
def pruebas_acl(ctx: Ctx, ev) -> None:
    a1, a2 = ctx.tokens["a1"], ctx.tokens["a2"]

    # 050c SÍ concede SELECT sobre public.bitacora a fashionstore_api: la lectura
    # es deliberada para el personal del tenant y la limitan las policies de 050b
    # (p_api_select_staff_bitacora + actor_empresa_id/actor_es_personal). Lo que
    # debe seguir fallando cerrado es la lectura SIN token (AIS-10 comprueba el
    # aislamiento por empresa y que un cliente no vea nada).
    ev.rechazo("ACL-01",
               "El GRANT SELECT sobre bitácora no basta: sin token falla cerrada",
               lambda: escalar(ctx, "api", None,
                               "SELECT count(*) FROM public.bitacora"),
               sqlstate="42501")
    ev.rechazo("ACL-02",
               "El login API no puede INSERTAR en bitácora (por eso audita el trigger)",
               lambda: escalar(ctx, "api", a1,
                               "INSERT INTO public.bitacora(empresa_id,accion,"
                               "entidad_afectada,descripcion) VALUES (1,'CREAR','x','y')"),
               sqlstate="42501")
    ev.rechazo("ACL-03", "El login API no puede BORRAR eventos de bitácora",
               lambda: escalar(ctx, "api", a1, "DELETE FROM public.bitacora"),
               sqlstate="42501")

    def admin_escribe():
        with sesion(ctx, "api", a1) as cur:
            cur.execute("INSERT INTO public.categoria(empresa_id, nombre)"
                        " VALUES (1, 'Categoria ACL 5CB')")
            creadas = cur.rowcount
            cur.execute("UPDATE public.categoria SET nombre = 'Categoria ACL 5CB v2'"
                        " WHERE empresa_id = 1 AND nombre = 'Categoria ACL 5CB'")
            return creadas, cur.rowcount
    ev.espera("ACL-04",
              "El administrador del tenant sí puede crear y actualizar categorías",
              lambda: exigir(admin_escribe() == (1, 1), "1 INSERT y 1 UPDATE aplicados"))

    ev.rechazo("ACL-05", "Un cliente no puede crear categorías",
               lambda: escalar(ctx, "api", a2,
                               "INSERT INTO public.categoria(empresa_id, nombre)"
                               " VALUES (1, 'Categoria cliente 5CB')"),
               sqlstate="42501")
    ev.rechazo("ACL-06", "Nadie puede borrar categorías (falta DELETE)",
               lambda: escalar(ctx, "api", a1,
                               "DELETE FROM public.categoria WHERE id = 1"),
               sqlstate="42501")
    ev.rechazo("ACL-07", "El login de bootstrap no lee tablas de negocio directamente",
               lambda: escalar(ctx, "bootstrap", None,
                               "SELECT count(*) FROM public.empresa"), sqlstate="42501")
    ev.rechazo("ACL-08", "El login de bootstrap no ve credenciales completas de usuario",
               lambda: escalar(ctx, "bootstrap", None,
                               "SELECT count(*) FROM public.usuario"), sqlstate="42501")
    ev.rechazo("ACL-09", "El login API no puede ejecutar bootstrap_empresa_id()",
               lambda: escalar(ctx, "api", None,
                               "SELECT empresa_id FROM"
                               " fashionstore_private.bootstrap_empresa_id('vanter-men')"),
               sqlstate="42501")

    def sin_ddl():
        try:
            escalar(ctx, "api", a1, "CREATE TABLE public.lab_5cb_intruso(id int)")
        except psycopg.Error as exc:
            exigir(exc.sqlstate == "42501", f"sqlstate={exc.sqlstate}")
            return "CREATE TABLE rechazado con 42501"
        raise AssertionError("el login API pudo crear una tabla")
    ev.espera("ACL-10", "El login API no tiene CREATE en el esquema public", sin_ddl)


# -- Bootstrap: bootstrap_empresa_id / bootstrap_credencial ----------------
def _bootstrap_empresa(ctx: Ctx, slug: str) -> list[tuple]:
    with sesion(ctx, "bootstrap", None) as cur:
        cur.execute("SELECT empresa_id FROM"
                    " fashionstore_private.bootstrap_empresa_id(%s)", (slug,))
        return cur.fetchall()


def pruebas_bootstrap(ctx: Ctx, ev) -> None:
    ev.espera("BST-01", "bootstrap_empresa_id('vanter-men') resuelve la empresa 1",
              lambda: exigir(_bootstrap_empresa(ctx, "vanter-men") == [(1,)],
                             "devolvió la empresa 1"))
    ev.espera("BST-02", "Un slug inexistente devuelve 0 filas, no error",
              lambda: exigir(_bootstrap_empresa(ctx, "no-existe") == [], "0 filas"))
    ev.espera("BST-03", "El slug se normaliza (mayúsculas y espacios)",
              lambda: exigir(_bootstrap_empresa(ctx, "  VanTeR-Men  ") == [(1,)],
                             "normalización lower(btrim()) aplicada"))
    ev.espera("BST-04", "Inyección SQL en el slug no devuelve ninguna empresa",
              lambda: exigir(_bootstrap_empresa(ctx, "x' OR true--") == [],
                             "0 filas con x' OR true--"))
    ev.espera("BST-05", "Un slug válido no devuelve más de una empresa",
              lambda: exigir(len(_bootstrap_empresa(ctx, "vanter-men")) <= 1,
                             "como máximo una fila"))

    def inactiva():
        exigir(_bootstrap_empresa(ctx, "tienda-b") == [(2,)], "línea base tienda-b")
        _admin(ctx, "UPDATE public.empresa SET estado = false WHERE id = 2")
        try:
            exigir(_bootstrap_empresa(ctx, "tienda-b") == [],
                   "una empresa inactiva no debe resolverse")
        finally:
            _admin(ctx, "UPDATE public.empresa SET estado = true WHERE id = 2")
        return "empresa inactiva -> 0 filas"
    ev.espera("BST-06", "Una empresa inactiva no se resuelve por slug", inactiva)

    def credencial():
        with sesion(ctx, "bootstrap", None) as cur:
            cur.execute("SELECT usuario_id, password_hash FROM"
                        " fashionstore_private.bootstrap_credencial(%s)",
                        ("A1.Admin@Lab.Test",))
            filas = cur.fetchall()
            exigir(len(filas) == 1 and filas[0][0] == 101,
                   f"se esperaba la credencial de 101, obtenido {filas}")
            exigir(filas[0][1] and "$argon2" in filas[0][1],
                   "debe devolverse el hash Argon2 real")
            cur.execute("SELECT usuario_id FROM"
                        " fashionstore_private.bootstrap_credencial(%s)",
                        ("no.existe@lab.test",))
            exigir(cur.fetchall() == [], "un correo desconocido debe dar 0 filas")
        return "correo normalizado, sólo id+hash, 0 filas si no existe"
    ev.espera("BST-07", "bootstrap_credencial devuelve sólo id y hash del correo exacto",
              credencial)

    def propuesta_050a():
        """BST-01…06 fallan porque 050a sólo concede SELECT(id) sobre public.empresa
        a fashionstore_bootstrap_owner, pero bootstrap_empresa_id() filtra por
        slug y estado: el privilegio por columnas no cubre esas dos columnas y la
        función SECURITY DEFINER aborta con 42501. Se aplica la corrección mínima
        para dejar PROBADO el parche que 050a debe incorporar."""
        _admin(ctx, "GRANT SELECT(id, slug, estado) ON public.empresa"
                    " TO fashionstore_bootstrap_owner")
        exigir(_bootstrap_empresa(ctx, "vanter-men") == [(1,)],
               "la concesión por columnas id/slug/estado repara el login por slug")
        exigir(_bootstrap_empresa(ctx, "no-existe") == [],
               "un slug inexistente sigue devolviendo 0 filas")
        return "GRANT SELECT(id, slug, estado) repara bootstrap_empresa_id()"
    ev.espera("BST-08", "Corrección propuesta para 050a: SELECT(id, slug, estado) "
                        "en empresa", propuesta_050a)


# -- aislamiento entre empresas (RLS) --------------------------------------
def _update(cur, sentencia: str) -> int:
    cur.execute(sentencia)
    return cur.rowcount


def _cuenta(ctx: Ctx, token: str, tabla: str) -> int:
    return escalar(ctx, "api", token, f"SELECT count(*) FROM public.{tabla}")


def pruebas_aislamiento(ctx: Ctx, ev) -> None:
    a1, b1, b2 = ctx.tokens["a1"], ctx.tokens["b1"], ctx.tokens["b2"]

    ev.espera("AIS-01", "A1 sólo ve el catálogo de su empresa",
              lambda: exigir((_cuenta(ctx, a1, "producto"), _cuenta(ctx, a1, "cliente"),
                              _cuenta(ctx, a1, "categoria")) == (1, 2, 1),
                             "producto=1 cliente=2 categoria=1"))
    ev.espera("AIS-02", "B1 sólo ve el catálogo de su empresa",
              lambda: exigir((_cuenta(ctx, b1, "producto"), _cuenta(ctx, b1, "cliente"),
                              _cuenta(ctx, b1, "categoria")) == (1, 1, 1),
                             "producto=1 cliente=1 categoria=1"))
    ev.espera("AIS-03", "A1 no puede leer por id una fila de B",
              lambda: exigir(scalar_aislado(ctx, a1, "SELECT nombre FROM public.producto"
                                                    " WHERE id = 2") is None,
                             "producto 2 de B invisible"))
    ev.espera("AIS-04", "A1 no puede ACTUALIZAR la fila de otra empresa",
              lambda: exigir(_update_en(ctx, a1, "UPDATE public.producto SET nombre"
                                                 " = nombre WHERE id = 2") == 0,
                             "0 filas afectadas sobre producto 2"))
    ev.espera("AIS-05", "A1 sólo ve su propio inventario",
              lambda: exigir(_cuenta(ctx, a1, "inventario") == 2,
                             "2 inventarios en A, ninguno de B"))
    ev.rechazo("AIS-06", "A1 no puede INSERTAR filas con empresa_id ajeno",
               lambda: escalar(ctx, "api", a1,
                               "INSERT INTO public.categoria(empresa_id, nombre)"
                               " VALUES (2, 'Intruso 5CB')"), sqlstate="42501")
    ev.rechazo("AIS-07", "B2 tampoco puede INSERTAR en la empresa 1",
               lambda: escalar(ctx, "api", b2,
                               "INSERT INTO public.categoria(empresa_id, nombre)"
                               " VALUES (1, 'Intruso B2 5CB')"), sqlstate="42501")
    ev.rechazo("AIS-08", "Cambiar empresa_id de una fila se bloquea (inmutable)",
               lambda: escalar(ctx, "api", a1,
                               "UPDATE public.categoria SET empresa_id = 2 WHERE id = 1"),
               sqlstate="42501")
    ev.espera("AIS-09", "B2 sólo ve las ventas de su empresa",
              lambda: exigir(_cuenta(ctx, b2, "venta") == 1, "1 venta en B"))
    ev.espera("AIS-10", "La bitácora ve sólo la empresa del token y el cliente no ve nada",
              lambda: _bitacora_por_empresa(ctx))


def scalar_aislado(ctx: Ctx, token: str, sentencia: str) -> object:
    try:
        return escalar(ctx, "api", token, sentencia)
    except psycopg.Error as exc:
        if exc.sqlstate == "42501":
            return None
        raise


def _update_en(ctx: Ctx, token: str, sentencia: str) -> int:
    with sesion(ctx, "api", token) as cur:
        return _update(cur, sentencia)


def _bitacora_por_empresa(ctx: Ctx) -> str:
    """050c concede SELECT sobre bitacora al login API: la lectura es deliberada
    para el personal del tenant y la limitan las policies de 050b. Se comprueba
    que la RLS la restringe a la empresa del token y que un cliente no lee nada.
    """
    a1, a2 = ctx.tokens["a1"], ctx.tokens["a2"]
    propias = escalar(ctx, "api", a1,
                      "SELECT count(*) FROM public.bitacora WHERE empresa_id = 1")
    exigir(propias >= 2, f"A1 debía ver los eventos de su empresa; vio {propias}")
    ajenas = escalar(ctx, "api", a1,
                     "SELECT count(*) FROM public.bitacora WHERE empresa_id <> 1")
    exigir(ajenas == 0, f"A1 vio {ajenas} eventos de otra empresa")
    del_cliente = escalar(ctx, "api", a2, "SELECT count(*) FROM public.bitacora")
    exigir(del_cliente == 0,
           f"un cliente no es personal y no debe ver la bitácora: {del_cliente}")
    b2 = ctx.tokens["b2"]
    ajenas_b = escalar(ctx, "api", b2,
                       "SELECT count(*) FROM public.bitacora WHERE empresa_id <> 2")
    exigir(ajenas_b == 0, f"B2 vio {ajenas_b} eventos de la empresa A")
    propias_b = escalar(ctx, "api", b2,
                        "SELECT count(*) FROM public.bitacora WHERE empresa_id = 2")
    exigir(propias_b >= 1, f"B2 debía ver al menos un evento de B; vio {propias_b}")
    return (f"A1: {propias} propios y 0 ajenos; cliente A2: 0; "
            f"B2: {propias_b} propios y 0 ajenos")


# -- bitácora de categoria: un evento por escritura, empresa desde la fila --
def _eventos(ctx: Ctx, categoria_id: int) -> list[tuple]:
    with abrir(ctx.dsn["admin"]) as conexion:
        with conexion.cursor() as cur:
            cur.execute(
                "SELECT empresa_id, usuario_id, accion FROM public.bitacora"
                " WHERE entidad_afectada = 'categoria' AND descripcion LIKE %s"
                " ORDER BY id", (f"%registro ID {categoria_id}%",))
            return cur.fetchall()


def _confirmar(ctx: Ctx, token: str, sentencia: str,
               parametros: tuple = ()) -> tuple:
    """Ejecuta una escritura y CONFIRMA (la bitácora se lee desde otra sesión)."""
    with abrir(ctx.dsn["api"]) as conexion:
        with conexion.cursor() as cur:
            tokenizar(cur, token)
            cur.execute(sentencia, parametros)
            fila = cur.fetchone() if cur.description else None
            filas = cur.rowcount
        conexion.commit()
    return filas, (fila[0] if fila else None)


def _limpiar_categoria(ctx: Ctx, categoria_id: int) -> None:
    _admin(ctx, "DELETE FROM public.categoria WHERE id = %s", (categoria_id,))
    _admin(ctx, "DELETE FROM public.bitacora WHERE entidad_afectada = 'categoria'"
                " AND descripcion LIKE %s", (f"%registro ID {categoria_id}%",))


def _total_eventos_categoria(ctx: Ctx) -> int:
    with abrir(ctx.dsn["admin"]) as conexion, conexion.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.bitacora"
                    " WHERE entidad_afectada = 'categoria'")
        return cur.fetchone()[0]


def pruebas_bitacora(ctx: Ctx, ev) -> None:
    a1 = ctx.tokens["a1"]
    estado: dict[str, int] = {}

    def linea_base():
        with abrir(ctx.dsn["admin"]) as conexion, conexion.cursor() as cur:
            cur.execute("SELECT empresa_id, usuario_id, count(*) FROM public.bitacora"
                        " WHERE entidad_afectada = 'categoria' GROUP BY 1,2 ORDER BY 1")
            return cur.fetchall()
    ev.espera("BIT-01",
              "Las fixtures dejan 2 eventos de categoría, uno por empresa y sin usuario",
              lambda: exigir(linea_base() == [(1, None, 1), (2, None, 1)],
                             "una fila por empresa sembrada en las fixtures"))

    def crear():
        _, cid = _confirmar(ctx, a1, "INSERT INTO public.categoria(empresa_id, nombre)"
                                     " VALUES (1, %s) RETURNING id", ("Cat BIT-02",))
        estado["cid"] = cid
        exigir(isinstance(cid, int) and cid > 2, f"id inesperado {cid}")
        return f"categoría {cid} creada y confirmada"
    ev.espera("BIT-02", "El administrador crea una categoría y confirma la transacción",
              crear)

    ev.espera("BIT-03", "El INSERT de categoría deja EXACTAMENTE un evento CREAR",
              lambda: exigir(_eventos(ctx, estado["cid"]) == [(1, 101, "CREAR")],
                             f"eventos={_eventos(ctx, estado['cid'])}"))

    def modificar():
        _confirmar(ctx, a1, "UPDATE public.categoria SET nombre = %s WHERE id = %s",
                   ("Cat BIT-03", estado["cid"]))
        eventos = _eventos(ctx, estado["cid"])
        exigir(len(eventos) == 2, f"eventos={eventos}")
        exigir(eventos[1] == (1, 101, "MODIFICAR"), f"eventos={eventos}")
        return "CREAR + MODIFICAR: un evento por escritura, dos en total"
    ev.espera("BIT-04",
              "El UPDATE de categoría añade UN evento MODIFICAR (no duplica el CREAR)",
              modificar)

    def otra_empresa():
        _, cid = _confirmar(ctx, ctx.tokens["b2"],
                            "INSERT INTO public.categoria(empresa_id, nombre)"
                            " VALUES (2, %s) RETURNING id", ("Cat BIT-05",))
        estado["cid_b"] = cid
        exigir(_eventos(ctx, cid) == [(2, 202, "CREAR")],
               f"eventos={_eventos(ctx, cid)}")
        return "el evento de B2 lleva empresa 2 y usuario 202"
    ev.espera("BIT-05", "El evento se atribuye a la empresa de la FILA, no a la sesión",
              otra_empresa)

    def sin_evento_cruzado():
        with sesion(ctx, "api", ctx.tokens["b2"]) as cur:
            cur.execute("UPDATE public.categoria SET nombre = 'ajeno' WHERE id = %s",
                        (estado["cid"],))
            filas = cur.rowcount
        exigir(filas == 0, f"B2 modificó {filas} filas de la empresa 1")
        exigir(len(_eventos(ctx, estado["cid"])) == 2,
               "un UPDATE bloqueado por RLS no debe generar auditoría fantasma")
        return "0 filas afectadas y ningún evento nuevo"
    ev.espera("BIT-06", "Una escritura bloqueada por RLS no genera evento",
              sin_evento_cruzado)

    def guc_manipulado():
        with abrir(ctx.dsn["api"]) as conexion:
            with conexion.cursor() as cur:
                tokenizar(cur, a1)
                cur.execute("SELECT set_config('app.empresa_id','2',true)")
                cur.execute("INSERT INTO public.categoria(empresa_id, nombre)"
                            " VALUES (1, %s) RETURNING id", ("Cat BIT-07",))
                cid = cur.fetchone()[0]
            conexion.commit()
        estado["cid_guc"] = cid
        eventos = _eventos(ctx, cid)
        exigir(eventos == [(1, 101, "CREAR")],
               f"evento con empresa_id manipulado: {eventos}")
        return "app.empresa_id=2 no altera el empresa_id=1 del evento"
    ev.espera("BIT-07", "Manipular app.empresa_id no falsifica la empresa del evento",
              guc_manipulado)

    def eliminar():
        _admin(ctx, "DELETE FROM public.categoria WHERE id = %s", (estado["cid_guc"],))
        eventos = _eventos(ctx, estado["cid_guc"])
        exigir(len(eventos) == 2, f"eventos={eventos}")
        exigir(eventos[-1] == (1, None, "ELIMINAR"), f"último evento={eventos[-1]}")
        return "ELIMINAR auditado con empresa 1 y usuario NULL (sin sesión de API)"
    ev.espera("BIT-08", "El DELETE también se audita y conserva la empresa de la fila",
              eliminar)

    def sin_residuo():
        _limpiar_categoria(ctx, estado["cid"])
        _limpiar_categoria(ctx, estado["cid_b"])
        _admin(ctx, "DELETE FROM public.bitacora WHERE entidad_afectada = 'categoria'"
                    " AND descripcion LIKE %s",
               (f"%registro ID {estado['cid_guc']}%",))
        return exigir(_total_eventos_categoria(ctx) == 2,
                      f"quedan {_total_eventos_categoria(ctx)} eventos de categoría")
    ev.espera("BIT-09", "Las pruebas no dejan residuo en la bitácora", sin_residuo)


# -- SP transaccional de reservas ------------------------------------------
SQL_SP = ("SELECT public.sp_crear_reserva_tenant(%s, %s, %s, %s, %s)")


def _futuro(dias: int = 2):
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) + timedelta(days=dias)


def pruebas_sp(ctx: Ctx, ev) -> None:
    a2 = ctx.tokens["a2"]

    def crear():
        with abrir(ctx.dsn["api"]) as conexion:
            with conexion.cursor() as cur:
                tokenizar(cur, a2)
                cur.execute(SQL_SP, (1, 1, 102, _futuro(), "lab 5CB"))
                reserva_id = cur.fetchone()[0]
                cur.execute("SELECT stock_reservado FROM public.inventario WHERE id = 1")
                stock = cur.fetchone()[0]
                cur.execute("SELECT estado FROM public.carrito WHERE id = 1")
                estado = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM public.detalle_reserva"
                            " WHERE reserva_id = %s", (reserva_id,))
                detalles = cur.fetchone()[0]
            conexion.rollback()
        exigir(reserva_id > 0, f"reserva={reserva_id}")
        exigir((stock, estado, detalles) == (1, "CONVERTIDO", 1),
               f"stock={stock} estado={estado} detalles={detalles}")
        return f"reserva {reserva_id}: stock reservado, carrito convertido, 1 detalle"
    ev.espera("SP-01", "El SP crea la reserva y descuenta disponibilidad de forma atómica",
              crear)

    ev.espera("SP-02", "El SP exige una sola reserva por carrito (SP-02 usado)",
              lambda: _segundo_intento(ctx, a2))

    ev.rechazo("SP-03", "El SP exige que la empresa coincida con el token",
               lambda: escalar(ctx, "api", a2, SQL_SP,
                               (2, 1, 102, _futuro(), "lab 5CB")), sqlstate="42501")
    ev.rechazo("SP-04", "El SP exige que el usuario coincida con el token",
               lambda: escalar(ctx, "api", a2, SQL_SP,
                               (1, 1, 103, _futuro(), "lab 5CB")), sqlstate="42501")
    ev.rechazo("SP-05", "El SP rechaza una fecha de atención pasada",
               lambda: escalar(ctx, "api", a2, SQL_SP,
                               (1, 1, 102, _futuro(-1), "lab 5CB")), sqlstate="22007")


# -- reutilización de conexiones (pool) ------------------------------------
def _url_sqlalchemy(dsn: str) -> str:
    return dsn.replace("postgresql://", "postgresql+psycopg://", 1)


def _motor(ctx: Ctx, rol: str = "api"):
    from sqlalchemy import create_engine

    return create_engine(_url_sqlalchemy(ctx.dsn[rol]), pool_size=1,
                         max_overflow=0, pool_pre_ping=True)


def entorno_app(ctx: Ctx) -> None:
    """Alinea la configuración de la app con el clúster desechable.

    Se fija ANTES de importar ``app``: ``app.core.config`` lee el entorno al
    importarse y ``app.core.database`` crea sus engines en ese momento. Los tres
    DSN se apuntan al laboratorio para que ningún import pueda alcanzar otra base.
    """
    import os

    api = _url_sqlalchemy(ctx.dsn["api"])
    os.environ["DATABASE_URL"] = api
    os.environ["DATABASE_BOOTSTRAP_URL"] = _url_sqlalchemy(ctx.dsn["bootstrap"])
    os.environ["DATABASE_PLATFORM_URL"] = _url_sqlalchemy(ctx.dsn["platform"])
    # Pools dedicados y flags que consume la app (y fastapi_lab) en el import.
    os.environ["FASHIONSTORE_BOOTSTRAP_DSN"] = ctx.dsn["bootstrap"]
    os.environ["FASHIONSTORE_PLATFORM_DSN"] = ctx.dsn["platform"]
    os.environ["SAAS_AUTH_ENABLED"] = "true"
    os.environ["SECRET_KEY"] = ctx.secreto
    os.environ["APP_ENV"] = "test"
    os.environ["SAAS_BITACORA_POR_TRIGGER"] = "false"


def pruebas_pool(ctx: Ctx, ev) -> None:
    a1 = ctx.tokens["a1"]

    def sin_fuga():
        with abrir(ctx.dsn["api"]) as conexion:
            with conexion.cursor() as cur:
                tokenizar(cur, a1)
                cur.execute("SELECT count(*) FROM public.categoria")
                visible = uno(cur)
            conexion.rollback()
            with conexion.cursor() as cur:
                cur.execute("SELECT current_setting('app.actor_token', true),"
                            "       current_setting('app.usuario_id', true)")
                resto = cur.fetchone()
            conexion.rollback()
        exigir(visible == 1, f"A1 no vio su catálogo: {visible}")
        exigir(all(v in (None, "") for v in resto),
               f"las GUC sobrevivieron al rollback: {resto}")
        return "la conexión física queda sin actor tras el rollback"
    ev.espera("POOL-01", "El token no sobrevive al fin de la transacción", sin_fuga)

    def reuso_sqlalchemy():
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError

        motor = _motor(ctx)
        try:
            with motor.connect() as primera:
                primera.execute(text("SELECT set_config('app.actor_token', :t, true)"),
                                {"t": a1})
                primera.execute(text("SELECT set_config('app.usuario_id','101',true)"))
                vistas = primera.execute(
                    text("SELECT count(*) FROM public.categoria")).scalar()
                primera.commit()
            with motor.connect() as segunda:
                residuo = segunda.execute(text(
                    "SELECT coalesce(current_setting('app.actor_token', true), '')"
                )).scalar()
                try:
                    filas = segunda.execute(
                        text("SELECT count(*) FROM public.categoria")).scalar()
                except DBAPIError as exc:
                    estado = getattr(exc.orig, "sqlstate", None)
                    exigir(estado == "42501", f"sqlstate={estado}")
                    filas = None
                segunda.rollback()
        finally:
            motor.dispose()
        exigir(vistas == 1, f"A1 no vio su catálogo: {vistas}")
        exigir(residuo == "", f"el token quedó en la conexión reutilizada: {residuo!r}")
        exigir(filas in (0, None), f"sin token se leyeron {filas} filas")
        return "la conexión devuelta al pool no hereda el actor anterior"
    ev.espera("POOL-02", "El pool de SQLAlchemy reutiliza la conexión sin fugar el actor",
              reuso_sqlalchemy)


# -- sonda del código real de la aplicación --------------------------------
def pruebas_sonda(ctx: Ctx, ev) -> None:
    """Ejecuta app/core/bitacora_trigger.py y el repositorio real contra el clúster."""
    entorno_app(ctx)
    estado: dict[str, int] = {}

    def detecta():
        from sqlalchemy.orm import Session

        from app.core.bitacora_trigger import (
            bitacora_categoria_por_trigger, limpiar_cache_bitacora_trigger,
        )
        from app.core.config import settings

        exigir(settings.saas_bitacora_por_trigger is False,
               "el flag SAAS_BITACORA_POR_TRIGGER debe llegar apagado")
        motor = _motor(ctx)
        try:
            with Session(motor) as db:
                limpiar_cache_bitacora_trigger()
                primero = bitacora_categoria_por_trigger(db)
                segundo = bitacora_categoria_por_trigger(db)
        finally:
            motor.dispose()
        exigir(primero is True and segundo is True, f"sonda={primero}/{segundo}")
        return "pg_trigger detectado en la primera consulta y memorizado"
    ev.espera("SONDA-01", "app/core/bitacora_trigger.py ve trg_bitacora_categoria",
              detecta)

    def crear_con_repositorio():
        from app.modules.catalogo.repositories.tenant_write_repository import (
            TenantCatalogoWriteRepository,
        )

        motor = _motor(ctx)
        try:
            with _sesion_app(motor, ctx.tokens["a1"], "101") as db:
                cid = TenantCatalogoWriteRepository.crear_categoria(
                    db, empresa_id=1, usuario_id=101,
                    nombre="Cat SONDA 5CB", descripcion="laboratorio")
                db.commit()
        finally:
            motor.dispose()
        estado["cid"] = cid
        eventos = _eventos(ctx, cid)
        exigir(eventos == [(1, 101, "CREAR")], f"eventos={eventos}")
        return f"categoría {cid} con un único evento CREAR (sin duplicado del repo)"
    ev.espera("SONDA-02",
              "El repositorio real delega la auditoría al trigger y no la duplica",
              crear_con_repositorio)

    def modificar_con_repositorio():
        from app.modules.catalogo.repositories.tenant_write_repository import (
            TenantCatalogoWriteRepository,
        )

        motor = _motor(ctx)
        try:
            with _sesion_app(motor, ctx.tokens["a1"], "101") as db:
                ok = TenantCatalogoWriteRepository.actualizar_categoria(
                    db, empresa_id=1, usuario_id=101, categoria_id=estado["cid"],
                    cambios={"nombre": "Cat SONDA 5CB v2"})
                db.commit()
        finally:
            motor.dispose()
        exigir(ok is True, "el repositorio no actualizó la categoría")
        eventos = _eventos(ctx, estado["cid"])
        exigir(eventos == [(1, 101, "CREAR"), (1, 101, "MODIFICAR")],
               f"eventos={eventos}")
        return "CREAR + MODIFICAR desde el repositorio, un evento cada uno"
    ev.espera("SONDA-03",
              "El repositorio real tampoco duplica el evento del UPDATE",
              modificar_con_repositorio)

    def limpiar():
        _limpiar_categoria(ctx, estado["cid"])
        return exigir(_total_eventos_categoria(ctx) == 2,
                      f"quedan {_total_eventos_categoria(ctx)} eventos")
    ev.espera("SONDA-04", "La sonda deja la base como la encontró", limpiar)


def _sesion_app(motor, token: str, usuario: str):
    from contextlib import contextmanager

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    @contextmanager
    def abrir_sesion():
        with Session(motor) as db:
            db.execute(text("SELECT set_config('app.actor_token', :t, true)"),
                       {"t": token})
            db.execute(text("SELECT set_config('app.usuario_id', :u, true)"),
                       {"u": usuario})
            yield db
    return abrir_sesion()


def _segundo_intento(ctx: Ctx, token: str) -> str:
    with abrir(ctx.dsn["api"]) as conexion:
        with conexion.cursor() as cur:
            tokenizar(cur, token)
            cur.execute(SQL_SP, (1, 1, 102, _futuro(), "lab 5CB"))
            cur.fetchone()
            try:
                cur.execute(SQL_SP, (1, 1, 102, _futuro(), "lab 5CB"))
            except psycopg.Error as exc:
                exigir(exc.sqlstate == "23514", f"sqlstate={exc.sqlstate}")
                conexion.rollback()
                return "segundo intento rechazado con 23514"
        conexion.rollback()
    raise AssertionError("el segundo intento sobre el mismo carrito fue aceptado")



