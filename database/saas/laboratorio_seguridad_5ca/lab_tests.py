"""Pruebas de seguridad 5CA contra el clúster desechable (sin dobles)."""
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


def tokenizar(cur, token: str) -> None:
    cur.execute("SELECT set_config('app.actor_token', %s, true)", (token,))


def uno(cur) -> object:
    fila = cur.fetchone()
    return None if fila is None else fila[0]


def actor(cur) -> tuple[int, int]:
    cur.execute("SELECT fashionstore_private.actor_empresa_id(),"
                "       fashionstore_private.actor_usuario_id()")
    return cur.fetchone()


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


def escalar(ctx: Ctx, rol: str, token: str | None, sentencia: str) -> object:
    with sesion(ctx, rol, token) as cur:
        cur.execute(sentencia)
        return uno(cur)


def intentar(ctx: Ctx, rol: str, token: str | None, sentencia: str) -> object:
    with sesion(ctx, rol, token) as cur:
        cur.execute(sentencia)
        return cur.fetchall()


def exigir(condicion: bool, mensaje: str):
    if not condicion:
        raise AssertionError(mensaje)
    return mensaje


def pruebas_estructura(ctx: Ctx, ev, resumen: dict) -> None:
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

    with abrir(ctx.dsn["admin"]) as conexion, conexion.cursor() as cur:
        cur.execute("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname LIKE 'fashionstore%'")
        roles = {f[0]: (f[1], f[2]) for f in cur.fetchall()}
    sospechosos = {r: v for r, v in roles.items() if v[0] or v[1]}
    ev.espera("EST-03", "Ningún rol fashionstore es SUPERUSER ni tiene BYPASSRLS",
              lambda: exigir(not sospechosos,
                             f"{len(roles)} roles examinados, 0 con bypass "
                             "ni superusuario"))
    ev.espera("EST-04", "Conexión API autentica con login propio y sin BYPASSRLS",
              lambda: _identidad_login(ctx))
    ev.espera("EST-05", "La API ve exactamente una empresa (la de su token)",
              lambda: exigir(
                  escalar(ctx, "api", ctx.tokens["a1"],
                          "SELECT count(*) FROM public.empresa") == 1,
                  "1 empresa visible para A1"))


def _identidad_login(ctx: Ctx) -> str:
    with sesion(ctx, "api", ctx.tokens["a1"]) as cur:
        cur.execute("SELECT session_user, current_user,"
                    " (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)")
        usuario, actual, bypass = cur.fetchone()
        exigir(usuario.startswith("fashionstore_5ca_"), f"session_user={usuario}")
        exigir(not bypass, "el login API tiene BYPASSRLS")
        return f"session_user={usuario} current_user={actual} bypassrls={bypass}"


def pruebas_acl(ctx: Ctx, ev) -> None:
    ev.rechazo("ACL-01", "API no puede leer el Vault local", lambda: intentar(
        ctx, "api", ctx.tokens["a1"],
        "SELECT decrypted_secret FROM vault.decrypted_secrets"), sqlstate="42501")
    ev.rechazo("ACL-02", "API no puede leer vault.secrets", lambda: intentar(
        ctx, "api", ctx.tokens["a1"],
        "SELECT secret FROM vault.secrets"), sqlstate="42501")
    ev.rechazo("ACL-03", "API no puede insertar bitácora directamente", lambda: intentar(
        ctx, "api", ctx.tokens["a1"],
        "INSERT INTO public.bitacora(accion) VALUES ('X')"), sqlstate="42501")
    ev.rechazo("ACL-04", "API no puede crear objetos en public", lambda: intentar(
        ctx, "api", ctx.tokens["a1"],
        "CREATE TABLE public.intruso(id int)"), sqlstate="42501")
    ev.rechazo("ACL-05", "API no puede ejecutar los procedimientos legacy", lambda: intentar(
        ctx, "api", ctx.tokens["a1"],
        "CALL public.sp_crear_reserva(1, 1, now(), NULL, NULL, 1, 1)"), sqlstate="42501")
    ev.rechazo("ACL-06", "API no puede asumir el rol platform_admin", lambda: intentar(
        ctx, "api", ctx.tokens["a1"],
        "SET ROLE fashionstore_platform_admin"), sqlstate="42501")
    ev.rechazo("ACL-07", "Bootstrap no puede leer la tabla usuario", lambda: intentar(
        ctx, "bootstrap", None,
        "SELECT id, password_hash FROM public.usuario"), sqlstate="42501")
    ev.rechazo("ACL-08", "Bootstrap no puede ejecutar actor_claims()", lambda: intentar(
        ctx, "bootstrap", None,
        "SELECT fashionstore_private.actor_claims()"), sqlstate="42501")
    ev.rechazo("ACL-09", "Platform_admin no puede leer tablas comerciales", lambda: intentar(
        ctx, "platform", None,
        "SELECT count(*) FROM public.producto"), sqlstate="42501")
    ev.espera("ACL-10", "API no ve usuarios de otra empresa por id directo",
              lambda: exigir(
                  escalar(ctx, "api", ctx.tokens["a1"],
                          "SELECT count(*) FROM public.usuario WHERE id = 201") == 0,
                  "0 filas del usuario 201 de la empresa B"))
    ev.espera("ACL-11", "Bootstrap obtiene id+hash sólo por su función estrecha",
              lambda: exigir(
                  escalar(ctx, "bootstrap", None,
                          "SELECT count(*) FROM fashionstore_private"
                          ".bootstrap_credencial('a2.cliente@lab.test')") == 1,
                  "1 credencial devuelta"))
    ev.rechazo("ACL-12", "Bootstrap no puede abrir la vista jwt_keys", lambda: intentar(
        ctx, "bootstrap", None,
        "SELECT * FROM fashionstore_private.jwt_keys"), sqlstate="42501")


def _token(ctx: Ctx, usuario: int, empresa: int, contexto: str = "cliente",
           **extra) -> str:
    from lab_common import claims_tenant, jwt_hs256

    return jwt_hs256(ctx.secreto, claims_tenant(usuario, empresa, contexto, **extra))


def pruebas_identidad(ctx: Ctx, ev) -> None:
    consulta = "SELECT count(*) FROM public.producto"

    ev.rechazo("ID-01", "Sin token, toda consulta empresarial falla cerrada",
               lambda: escalar(ctx, "api", None, consulta), sqlstate="42501")
    ev.espera("ID-02", "Token válido A1 deriva empresa/usuario del JWT firmado",
              lambda: exigir(actor_con(ctx, ctx.tokens["a1"]) == (1, 101),
                             "actor=(1,101)"))

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
    ev.rechazo("ID-06", "Token con alg=none", lambda: escalar(
        ctx, "api", jwt_sin_firma(ctx), consulta), sqlstate="42501")
    ev.rechazo("ID-07", "Token sin jti", lambda: escalar(
        ctx, "api", _token_sin(ctx, "jti"), consulta), sqlstate="42501")
    ev.rechazo("ID-08", "Token con scope distinto de tenant", lambda: escalar(
        ctx, "api", _token_scope(ctx), consulta), sqlstate="42501")
    ev.rechazo("ID-09", "Token de A1 apuntando a empresa sin membresía", lambda: escalar(
        ctx, "api", _token(ctx, 101, 2, "personal"), consulta), sqlstate="42501")
    ev.rechazo("ID-10", "Token de usuario inexistente", lambda: escalar(
        ctx, "api", _token(ctx, 999, 1), consulta), sqlstate="42501")
    ev.rechazo("ID-11", "Token con iat en el futuro", lambda: escalar(
        ctx, "api", _token_iat_futuro(ctx), consulta), sqlstate="42501")
    ev.rechazo("ID-12", "Token desmesurado (>8192 caracteres)", lambda: escalar(
        ctx, "api", "x" * 9000, consulta), sqlstate="42501")
    ev.rechazo("ID-13", "Token personal de cliente (contexto incoherente)", lambda: escalar(
        ctx, "api", _token(ctx, 102, 1, "personal"), consulta), sqlstate="42501")

    ev.espera("ID-14", "Revocar la membresía invalida el token vigente en la siguiente sentencia",
              lambda: _membresia_revocada(ctx))
    ev.espera("ID-15", "Suspender la suscripción invalida el token vigente",
              lambda: _suscripcion_suspendida(ctx))
    ev.espera("ID-16", "Rotación de clave: token viejo sigue válido con 'previous'",
              lambda: _rotacion_clave(ctx))


def actor_con(ctx: Ctx, token: str) -> tuple[int, int]:
    with sesion(ctx, "api", token) as cur:
        cur.execute("SELECT fashionstore_private.actor_empresa_id(),"
                    "       fashionstore_private.actor_usuario_id()")
        return cur.fetchone()


def jwt_sin_firma(ctx: Ctx) -> str:
    import base64
    import hashlib
    import hmac as hmac_mod
    import json

    cabecera = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    from lab_common import claims_tenant

    cuerpo = base64.urlsafe_b64encode(
        json.dumps(claims_tenant(101, 1, "cliente")).encode()).rstrip(b"=").decode()
    firma = hmac_mod.new(ctx.secreto.encode(), f"{cabecera}.{cuerpo}".encode(),
                         hashlib.sha256).digest()
    return f"{cabecera}.{cuerpo}.{base64.urlsafe_b64encode(firma).rstrip(b'=').decode()}"


def _token_sin(ctx: Ctx, campo: str) -> str:
    from lab_common import claims_tenant, jwt_hs256

    claims = claims_tenant(101, 1, "cliente")
    claims.pop(campo, None)
    return jwt_hs256(ctx.secreto, claims)


def _token_scope(ctx: Ctx) -> str:
    from lab_common import claims_tenant, jwt_hs256

    claims = claims_tenant(101, 1, "cliente")
    claims["scope"] = "platform"
    return jwt_hs256(ctx.secreto, claims)


def _token_iat_futuro(ctx: Ctx) -> str:
    from datetime import datetime, timedelta, timezone

    from lab_common import claims_tenant, jwt_hs256

    claims = claims_tenant(101, 1, "cliente")
    claims["iat"] = int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp())
    return jwt_hs256(ctx.secreto, claims)


def _admin(ctx: Ctx, sentencia: str, parametros: tuple = ()) -> None:
    with abrir(ctx.dsn["admin"]) as conexion:
        with conexion.cursor() as cur:
            cur.execute(sentencia, parametros)
        conexion.commit()


def _membresia_revocada(ctx: Ctx) -> str:
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
    from lab_common import claims_tenant, jwt_hs256

    nueva = "rotacion-" + ctx.secreto[-12:]
    viejo = ctx.tokens["a1"]
    _admin(ctx, "INSERT INTO vault.secrets(name, secret) VALUES (%s,%s)",
           ("fashionstore_tenant_jwt_hs256_previous", ctx.secreto))
    _admin(ctx, "UPDATE vault.secrets SET secret = %s "
                "WHERE name = 'fashionstore_tenant_jwt_hs256_active'", (nueva,))
    try:
        exigir(actor_con(ctx, viejo) == (1, 101),
               "el token anterior debe seguir válido durante la rotación")
        token_nuevo = jwt_hs256(nueva, claims_tenant(101, 1, "personal"))
        exigir(actor_con(ctx, token_nuevo) == (1, 101),
               "el token nuevo debe ser válido")
        _admin(ctx, "DELETE FROM vault.secrets "
                    "WHERE name = 'fashionstore_tenant_jwt_hs256_previous'")
        try:
            actor_con(ctx, viejo)
        except psycopg.Error as exc:
            exigir(exc.sqlstate == "42501", f"sqlstate={exc.sqlstate}")
            return "rotación: clave previa válida y revocada al eliminarla"
        raise AssertionError("el token de la clave retirada siguió válido")
    finally:
        _admin(ctx, "DELETE FROM vault.secrets "
                    "WHERE name = 'fashionstore_tenant_jwt_hs256_previous'")
        _admin(ctx, "UPDATE vault.secrets SET secret = %s "
                    "WHERE name = 'fashionstore_tenant_jwt_hs256_active'",
               (ctx.secreto,))


def pruebas_aislamiento(ctx: Ctx, ev) -> None:
    def filas(rol: str, token: str, sentencia: str) -> int:
        return int(escalar(ctx, rol, token, sentencia))

    ev.espera("AIS-01", "A1 sólo ve el producto de su empresa",
              lambda: exigir(filas("api", ctx.tokens["a1"],
                                   "SELECT count(*) FROM public.producto") == 1,
                             "1 producto"))
    ev.espera("AIS-02", "B1 sólo ve el producto de su empresa",
              lambda: exigir(filas("api", ctx.tokens["b1"],
                                   "SELECT count(*) FROM public.producto") == 1,
                             "1 producto"))
    ev.espera("AIS-03", "A1 no ve el cliente de la empresa B",
              lambda: exigir(filas("api", ctx.tokens["a1"],
                                   "SELECT count(*) FROM public.cliente "
                                   "WHERE id = 201") == 0, "0 filas de B"))
    ev.espera("AIS-04", "Administrador A1 ve los 2 clientes de su empresa",
              lambda: exigir(filas("api", ctx.tokens["a1"],
                                   "SELECT count(*) FROM public.cliente") == 2,
                             "2 clientes"))
    ev.espera("AIS-05", "A1 sólo ve sus 2 filas de inventario",
              lambda: exigir(filas("api", ctx.tokens["a1"],
                                   "SELECT count(*) FROM public.inventario") == 2,
                             "2 filas"))
    ev.espera("AIS-06", "A1 sólo ve su reserva y su venta",
              lambda: exigir(filas("api", ctx.tokens["a1"],
                                   "SELECT (SELECT count(*) FROM public.reserva)"
                                   " + (SELECT count(*) FROM public.venta)") == 2,
                             "1 reserva + 1 venta"))
    ev.espera("AIS-07", "B1 no ve pagos de la empresa A",
              lambda: exigir(filas("api", ctx.tokens["b1"],
                                   "SELECT count(*) FROM public.pago WHERE id = 1") == 0,
                             "0 pagos ajenos"))
    ev.rechazo("AIS-08", "A1 no puede insertar producto con empresa_id ajeno",
               lambda: intentar(ctx, "api", ctx.tokens["a1"],
                                "INSERT INTO public.producto(empresa_id, categoria_id,"
                                " nombre, precio) VALUES (2, 2, 'Intruso', 1)"),
               sqlstate="42501")
    ev.espera("AIS-09", "A1 no puede modificar el producto de la empresa B",
              lambda: _update_cero(ctx))
    ev.rechazo("AIS-10", "A1 no puede mover empresa_id de su propio producto",
               lambda: intentar(ctx, "api", ctx.tokens["a1"],
                                "UPDATE public.producto SET empresa_id = 2 WHERE id = 1"),
               sqlstate="42501")
    ev.espera("AIS-11", "A1 administrador sí puede crear producto en su empresa",
              lambda: _insert_ok(ctx))
    ev.espera("AIS-12", "El cliente A2 sólo ve su propia ficha de cliente",
              lambda: exigir(filas("api", ctx.tokens["a2"],
                                   "SELECT count(*) FROM public.cliente") == 1,
                             "1 cliente propio"))
    ev.espera("AIS-13", "El cliente A2 sólo ve su carrito, no el de otro cliente",
              lambda: exigir(filas("api", ctx.tokens["a2"],
                                   "SELECT count(*) FROM public.carrito") == 1,
                             "1 carrito propio"))
    ev.espera("AIS-14", "El cliente A2 no ve la reserva de otro cliente",
              lambda: exigir(filas("api", ctx.tokens["a2"],
                                   "SELECT count(*) FROM public.reserva") == 0,
                             "0 reservas ajenas"))
    ev.espera("AIS-15", "El cliente A2 no ve bitácora, empleados ni proveedores",
              lambda: exigir(filas("api", ctx.tokens["a2"],
                                   "SELECT (SELECT count(*) FROM public.bitacora)"
                                   " + (SELECT count(*) FROM public.empleado)"
                                   " + (SELECT count(*) FROM public.proveedor)") == 0,
                             "0 filas de personal"))
    ev.espera("AIS-16", "El administrador A1 no ve bitácora de otras empresas",
              lambda: exigir(filas("api", ctx.tokens["a1"],
                                   "SELECT count(*) FROM public.bitacora"
                                   " WHERE empresa_id <> 1") == 0, "sin filas ajenas"))
    ev.espera("AIS-17", "El cliente A2 no puede insertar en el carrito de otro cliente",
              lambda: _intento_carrito_ajeno(ctx))
    ev.rechazo("AIS-18", "El cliente A2 no puede crear un carrito para otro cliente",
               lambda: intentar(ctx, "api", ctx.tokens["a2"],
                                "INSERT INTO public.carrito(empresa_id, cliente_id,"
                                " sucursal_id, estado) VALUES (1, 103, 11, 'ACTIVO')"),
               sqlstate="42501")
    ev.rechazo("AIS-19", "El cliente A2 no puede escribir catálogo (sólo administrador)",
               lambda: intentar(ctx, "api", ctx.tokens["a2"],
                                "INSERT INTO public.producto(empresa_id, categoria_id,"
                                " nombre, precio) VALUES (1, 1, 'Cliente', 1)"),
               sqlstate="42501")
    ev.espera("AIS-20", "B1 no puede actualizar la sucursal de A",
              lambda: _update_ajeno(ctx))


def _intento_carrito_ajeno(ctx: Ctx) -> str:
    """El intento debe fallar; RLS y el trigger de sucursal dan códigos distintos."""
    with sesion(ctx, "api", ctx.tokens["a2"]) as cur:
        cur.execute("SELECT fashionstore_private.actor_posee_carrito(4)")
        exigir(cur.fetchone()[0] is False, "actor_posee_carrito(4) debería ser falso")
    try:
        intentar(ctx, "api", ctx.tokens["a2"],
                 "INSERT INTO public.detalle_carrito"
                 "(empresa_id, carrito_id, cantidad, inventario_id)"
                 " VALUES (1, 4, 1, 2)")
    except psycopg.Error as exc:
        exigir(exc.sqlstate in ("42501", "P0001"),
               f"sqlstate inesperado {exc.sqlstate}")
        return (f"insert rechazado con sqlstate={exc.sqlstate}; "
                "actor_posee_carrito(4)=false")
    raise AssertionError("el cliente A2 pudo escribir en el carrito de otro cliente")


def _update_cero(ctx: Ctx) -> str:
    with sesion(ctx, "api", ctx.tokens["a1"]) as cur:
        cur.execute("UPDATE public.producto SET precio = 1 WHERE id = 2")
        exigir(cur.rowcount == 0, f"rowcount={cur.rowcount}")
        return "0 filas afectadas sobre el producto de B"


def _update_ajeno(ctx: Ctx) -> str:
    with sesion(ctx, "api", ctx.tokens["b1"]) as cur:
        cur.execute("UPDATE public.sucursal SET nombre = 'pirata' WHERE id = 11")
        exigir(cur.rowcount == 0, f"rowcount={cur.rowcount}")
        return "0 filas afectadas sobre la sucursal de A"


def _insert_ok(ctx: Ctx) -> str:
    with sesion(ctx, "api", ctx.tokens["a1"]) as cur:
        cur.execute("INSERT INTO public.producto(id, empresa_id, categoria_id,"
                    " nombre, precio) VALUES (999, 1, 1, 'Producto nuevo', 10)")
        exigir(cur.rowcount == 1, "no se insertó")
        return "1 producto insertado y revertido al cerrar la transacción"


# -- reutilización de conexión y concurrencia ------------------------------
def pruebas_pool(ctx: Ctx, ev) -> None:
    def residuo(conexion) -> str | None:
        with conexion.cursor() as cur:
            cur.execute("SELECT nullif(current_setting('app.actor_token', true), '')")
            valor = cur.fetchone()[0]
        # Cerrar la transacción implícita de la comprobación: si no se cierra,
        # el siguiente conexion.transaction() sería un SAVEPOINT y el GUC local
        # parecería sobrevivir al commit.
        conexion.rollback()
        return valor

    def un_request(conexion, token: str) -> tuple[int, int, int]:
        with conexion.transaction():
            with conexion.cursor() as cur:
                tokenizar(cur, token)
                cur.execute("SELECT fashionstore_private.actor_empresa_id(),"
                            " fashionstore_private.actor_usuario_id(),"
                            " (SELECT count(*) FROM public.empresa)")
                return cur.fetchone()

    def secuencia():
        with abrir(ctx.dsn["api"]) as conexion:
            conexion.autocommit = False
            vistos = []
            pids = set()
            for token in (ctx.tokens["a1"], ctx.tokens["b1"], ctx.tokens["a3"]):
                exigir(residuo(conexion) is None,
                       "quedó un token residual entre transacciones")
                vistos.append(un_request(conexion, token))
                with conexion.cursor() as cur:
                    cur.execute("SELECT pg_backend_pid()")
                    pids.add(cur.fetchone()[0])
            exigir(residuo(conexion) is None, "el token sobrevivió al commit")
            exigir(len(pids) == 1, "no se reutilizó la misma conexión física")
            exigir([v[0] for v in vistos] == [1, 2, 1], f"empresas vistas {vistos}")
            exigir(len({v[1] for v in vistos}) == 3, "usuarios vistos repetidos")
            for _ in range(20):
                for token, empresa in ((ctx.tokens["b1"], 2), (ctx.tokens["a2"], 1)):
                    exigir(un_request(conexion, token)[0] == empresa,
                           "fuga de empresa en la reutilización")
            return f"3 identidades + 40 reutilizaciones, 1 conexión física ({pids})"
    ev.espera("POOL-01", "Conexión reutilizada A1->B1->A3 sin fugas de identidad",
              secuencia)

    def concurrencia():
        from concurrent.futures import ThreadPoolExecutor

        def trabajo(args):
            token, esperado = args
            with abrir(ctx.dsn["api"]) as conexion:
                empresa, usuario, _ = un_request(conexion, token)
                exigir(empresa == esperado[0] and usuario == esperado[1],
                       f"identidad cruzada: {(empresa, usuario)} != {esperado}")
                return empresa, usuario

        casos = [(ctx.tokens["a1"], (1, 101)), (ctx.tokens["b1"], (2, 201)),
                 (ctx.tokens["a3"], (1, 103))]
        with ThreadPoolExecutor(max_workers=3) as ejecutor:
            resultados = list(ejecutor.map(trabajo, casos))
        exigir(len(set(resultados)) == 3, f"resultados repetidos {resultados}")
        return f"3 hilos simultáneos sin intercambiar identidad: {resultados}"
    ev.espera("POOL-02", "Conexiones concurrentes no comparten identidad", concurrencia)

    def guc_global():
        with abrir(ctx.dsn["api"]) as conexion:
            conexion.autocommit = True
            with conexion.cursor() as cur:
                cur.execute("SELECT set_config('app.actor_token', %s, false)",
                            (ctx.tokens["a1"],))
            with conexion.cursor() as cur:
                cur.execute("SELECT nullif(current_setting('app.actor_token', true), '')")
                persistente = cur.fetchone()[0]
                cur.execute("RESET app.actor_token")
        exigir(persistente is not None,
               "se esperaba demostrar el riesgo: set_config(...,false) persiste")
        return ("RIESGO CONFIRMADO: set_config con is_local=false deja el token en la "
                "sesión; FastAPI DEBE usar siempre el tercer argumento true")
    ev.espera("POOL-03", "GUC no local persiste: la aplicación debe usar is_local=true",
              guc_global)


# -- SP privilegiado, stock y concurrencia --------------------------------
def pruebas_sp(ctx: Ctx, ev) -> None:
    llamada = ("SELECT public.sp_crear_reserva_tenant(%s, %s, %s,"
               " now() + interval '2 days', %s)")

    def exito():
        # Se confirma la reserva para poder verificarla con el superusuario:
        # el cliente no tiene política sobre movimiento_inventario (es tabla de
        # personal). Al final se restaura el estado de las fixtures.
        try:
            with abrir(ctx.dsn["api"]) as conexion:
                conexion.autocommit = False
                with conexion.cursor() as cur:
                    tokenizar(cur, ctx.tokens["a2"])
                    cur.execute(llamada, (1, 1, 102, "lab"))
                    reserva_id = cur.fetchone()[0]
                conexion.commit()
            with abrir(ctx.dsn["admin"]) as conexion, conexion.cursor() as cur:
                cur.execute("SELECT estado FROM public.carrito WHERE id = 1")
                estado = cur.fetchone()[0]
                cur.execute("SELECT stock_reservado, stock_actual"
                            " FROM public.inventario WHERE id = 1")
                reservado, actual = cur.fetchone()
                cur.execute("SELECT count(*) FROM public.movimiento_inventario"
                            " WHERE referencia_id = %s AND tipo = 'RESERVA'",
                            (reserva_id,))
                movimientos = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM public.detalle_reserva"
                            " WHERE reserva_id = %s", (reserva_id,))
                detalles = cur.fetchone()[0]
            exigir(estado == "CONVERTIDO", f"carrito quedó en {estado}")
            exigir((reservado, actual) == (1, 1), f"stock {reservado}/{actual}")
            exigir(movimientos == 1, f"movimientos={movimientos}")
            exigir(detalles == 1, f"detalles={detalles}")
            return (f"reserva {reserva_id}, carrito CONVERTIDO, stock_reservado=1,"
                    " 1 movimiento de inventario, 1 detalle")
        finally:
            _reset_reservas(ctx)
    ev.espera("SP-01", "SP tenant con actor correcto crea reserva y reserva stock",
              exito)

    ev.rechazo("SP-02", "SP tenant rechaza suplantar a otro usuario del mismo tenant",
               lambda: intentar(ctx, "api", ctx.tokens["a3"],
                                llamada % (1, 4, 102, "'x'")), sqlstate="42501")
    ev.rechazo("SP-03", "SP tenant rechaza una empresa distinta a la del token",
               lambda: intentar(ctx, "api", ctx.tokens["a2"],
                                llamada % (2, 1, 102, "'x'")), sqlstate="42501")
    ev.rechazo("SP-04", "La API no puede ajustar inventario por UPDATE directo",
               lambda: intentar(ctx, "api", ctx.tokens["a1"],
                                "UPDATE public.inventario SET stock_actual = 99"
                                " WHERE id = 1"), sqlstate="42501")
    ev.rechazo("SP-05", "La API no puede insertar ventas directamente",
               lambda: intentar(ctx, "api", ctx.tokens["a1"],
                                "INSERT INTO public.venta(empresa_id, sucursal_id,"
                                " canal, estado, total) VALUES (1, 11, 'WEB',"
                                " 'PENDIENTE', 1)"), sqlstate="42501")
    ev.rechazo("SP-06", "El login platform_admin no puede ejecutar el SP tenant",
               lambda: intentar(ctx, "platform", None,
                                llamada % (1, 1, 102, "'x'")), sqlstate="42501")
    ev.espera("SP-07", "Carrera por la última unidad: exactamente una reserva gana",
              lambda: _carrera_ultima_unidad(ctx))
    ev.espera("SP-08", "Tras el SP no queda identidad residual en conexiones nuevas",
              lambda: _sin_residuo(ctx))


def _sin_residuo(ctx: Ctx) -> str:
    try:
        escalar(ctx, "api", None, "SELECT count(*) FROM public.producto")
    except psycopg.Error as exc:
        exigir(exc.sqlstate == "42501", f"sqlstate={exc.sqlstate}")
        return "sin token la siguiente conexión falla con 42501"
    raise AssertionError("una conexión nueva pudo consultar sin token")


def _carrera_ultima_unidad(ctx: Ctx) -> str:
    from concurrent.futures import ThreadPoolExecutor

    llamada = ("SELECT public.sp_crear_reserva_tenant(%s, %s, %s,"
               " now() + interval '2 days', %s)")

    def intento(args):
        token, carrito, usuario = args
        try:
            with abrir(ctx.dsn["api"]) as conexion:
                conexion.autocommit = False
                with conexion.cursor() as cur:
                    tokenizar(cur, token)
                    cur.execute(llamada, (1, carrito, usuario, "carrera"))
                    reserva = cur.fetchone()[0]
                conexion.commit()
                return ("ok", reserva)
        except psycopg.Error as exc:
            return ("error", exc.sqlstate)

    with ThreadPoolExecutor(max_workers=2) as ejecutor:
        resultados = list(ejecutor.map(
            intento, [(ctx.tokens["a2"], 1, 102), (ctx.tokens["a3"], 4, 103)]))
    exitos = [r for r in resultados if r[0] == "ok"]
    errores = [r for r in resultados if r[0] == "error"]
    try:
        exigir(len(exitos) == 1, f"resultados inesperados: {resultados}")
        exigir(len(errores) == 1 and errores[0][1] in ("23514", "40001"),
               f"el perdedor falló con {errores}")
        with abrir(ctx.dsn["admin"]) as conexion, conexion.cursor() as cur:
            cur.execute("SELECT stock_actual, stock_reservado,"
                        " stock_actual - stock_reservado FROM public.inventario"
                        " WHERE id = 1")
            actual, reservado, disponible = cur.fetchone()
            exigir(disponible >= 0, "stock negativo")
            exigir(reservado == 1, f"stock_reservado={reservado}")
            cur.execute("SELECT count(*) FROM public.reserva"
                        " WHERE carrito_id IN (1, 4)")
            reservas = cur.fetchone()[0]
            exigir(reservas == 1, f"reservas creadas={reservas}")
        return (f"1 éxito / 1 rechazo {errores[0][1]}; stock_actual={actual} "
                f"reservado={reservado} disponible={disponible}")
    finally:
        _reset_reservas(ctx)


def _reset_reservas(ctx: Ctx) -> None:
    _admin(ctx, """
        DELETE FROM public.detalle_reserva WHERE reserva_id NOT IN (1, 2);
        DELETE FROM public.reserva WHERE id NOT IN (1, 2);
        DELETE FROM public.movimiento_inventario WHERE tipo = 'RESERVA';
        UPDATE public.inventario SET stock_reservado =
            CASE id WHEN 1 THEN 0 WHEN 2 THEN 1 ELSE 1 END WHERE id IN (1, 2, 3);
        UPDATE public.carrito SET estado =
            CASE WHEN id IN (1, 4) THEN 'ACTIVO' ELSE 'CONVERTIDO' END
        WHERE id IN (1, 2, 3, 4);
    """)
