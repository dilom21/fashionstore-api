"""5BO / CU03-CU04: alta de personal y RBAC nominal de la empresa autenticada.

Reglas duras
------------
* `empresa_id` y `actor_id` provienen SIEMPRE del contexto tenant verificado
  (JWT + membresia + asercion HMAC), nunca del cuerpo HTTP.
* El alta de una cuenta NUEVA la ejecuta `public.sp_alta_personal_tenant`, una
  funcion SECURITY DEFINER con propietario NOLOGIN dedicado. El runtime tenant
  no recibe INSERT directo en `public.usuario` y revalida todo en PostgreSQL.
* No se vincula ninguna cuenta existente: un correo ya registrado (en esta
  empresa o en otra) devuelve el MISMO error neutro, sin revelar el tenant
  propietario ni el usuario.
* `public.usuario.rol_id` se fuerza a CLIENTE en la funcion; el rol
  empresarial vive solo en `usuario_empresa`. No hay escalada global.
* La matriz de permisos es nominal: excepcion por empresa > default por
  nombre de rol > denegado. `ADMINISTRADOR` no es editable (anti-lockout).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger, Boolean, String, and_, column, select, table, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

_DETALLE_403 = "Operacion no autorizada"
_DETALLE_404 = "Recurso no encontrado"
_DETALLE_409 = "Conflicto en la administracion de personal"
_DETALLE_422 = "Datos de personal invalidos"

ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROLES_BASE_ASIGNABLES = frozenset({"ADMINISTRADOR", "ENCARGADO_SUCURSAL", "CAJERO"})

empresa = table(
    "empresa",
    column("id", BigInteger),
    column("estado", Boolean),
    schema="public",
)
usuario_empresa = table(
    "usuario_empresa",
    column("id", BigInteger),
    column("empresa_id", BigInteger),
    column("usuario_id", BigInteger),
    column("rol_id", BigInteger),
    column("estado", Boolean),
    schema="public",
)
usuario = table(
    "usuario",
    column("id", BigInteger),
    column("correo", String),
    column("estado", Boolean),
    schema="public",
)
rol = table(
    "rol",
    column("id", BigInteger),
    column("nombre", String),
    column("estado", Boolean),
    schema="public",
)
empleado = table(
    "empleado",
    column("id", BigInteger),
    column("usuario_id", BigInteger),
    column("empresa_id", BigInteger),
    column("sucursal_id", BigInteger),
    column("nombres", String),
    column("apellidos", String),
    column("estado", Boolean),
    schema="public",
)
sucursal = table(
    "sucursal",
    column("id", BigInteger),
    column("empresa_id", BigInteger),
    column("estado", Boolean),
    schema="public",
)


class PersonalTenantError(Exception):
    def __init__(self, codigo: int, detalle: str, codigo_error: str | None = None):
        super().__init__(detalle)
        self.codigo = codigo
        self.detalle = detalle
        self.codigo_error = codigo_error


def _traducir(exc: DBAPIError) -> PersonalTenantError:
    estado = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if estado == "42501":
        return PersonalTenantError(403, _DETALLE_403)
    if estado in {"23505", "40001", "40P01"}:
        return PersonalTenantError(409, _DETALLE_409)
    if estado and estado.startswith("22"):
        return PersonalTenantError(422, _DETALLE_422)
    raise exc


def _json(valor) -> dict:
    if isinstance(valor, dict):
        return valor
    raise PersonalTenantError(503, "Administracion de personal no disponible")


def _respuesta(valor, *, defecto: str) -> dict:
    datos = _json(valor)
    if not datos.get("ok"):
        codigo = int(datos.get("codigo") or 409)
        raise PersonalTenantError(
            codigo,
            datos.get("mensaje") or defecto,
            datos.get("codigo_error"),
        )
    return datos


def _bloquear_empresa(db: Session, empresa_id: int) -> None:
    existente = db.scalar(
        select(empresa.c.id).where(
            empresa.c.id == empresa_id, empresa.c.estado.is_(True)
        ).with_for_update()
    )
    if existente is None:
        raise PersonalTenantError(404, "Empresa no disponible")


def _registrar_bitacora(db: Session, *, empresa_id: int, usuario_id: int,
                        accion: str, entidad: str, descripcion: str) -> None:
    db.execute(
        text(
            "INSERT INTO public.bitacora "
            "(usuario_id, empresa_id, fecha_hora, accion, entidad_afectada, descripcion) "
            "VALUES (CAST(:usuario AS bigint), CAST(:empresa AS bigint), NOW(), "
            "CAST(:accion AS text), CAST(:entidad AS text), CAST(:descripcion AS text))"
        ),
        {
            "usuario": usuario_id,
            "empresa": empresa_id,
            "accion": accion,
            "entidad": entidad,
            "descripcion": descripcion,
        },
    )


def expiracion_invitacion(horas: int = 72) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=int(horas))


class PersonalTenantRepository:
    @staticmethod
    def crear_personal(
        db: Session,
        *,
        empresa_id: int,
        actor_id: int,
        correo: str,
        nombres: str,
        apellidos: str,
        ci: str,
        telefono: str | None,
        sucursal_id: int,
        rol_id: int,
        password_hash: str,
        token_hash: str,
        expira_en: datetime,
    ) -> dict:
        try:
            valor = db.scalar(
                text(
                    "SELECT public.sp_alta_personal_tenant("
                    "CAST(:empresa AS bigint), CAST(:actor AS bigint), "
                    "CAST(:correo AS text), CAST(:nombres AS text), "
                    "CAST(:apellidos AS text), CAST(:ci AS text), "
                    "CAST(:telefono AS text), CAST(:sucursal AS bigint), "
                    "CAST(:rol AS bigint), CAST(:hash AS text), "
                    "CAST(:token AS text), CAST(:expira AS timestamptz))"
                ),
                {
                    "empresa": empresa_id,
                    "actor": actor_id,
                    "correo": correo,
                    "nombres": nombres,
                    "apellidos": apellidos,
                    "ci": ci,
                    "telefono": telefono,
                    "sucursal": sucursal_id,
                    "rol": rol_id,
                    "hash": password_hash,
                    "token": token_hash,
                    "expira": expira_en,
                },
            )
        except DBAPIError as exc:
            raise _traducir(exc) from exc
        return _respuesta(valor, defecto=_DETALLE_409)

    @staticmethod
    def cambiar_sucursal(
        db: Session,
        *,
        empresa_id: int,
        actor_id: int,
        usuario_id: int,
        sucursal_id: int,
    ) -> dict:
        _bloquear_empresa(db, empresa_id)
        fila = db.execute(
            select(
                usuario_empresa.c.id.label("membresia_id"),
                usuario_empresa.c.estado.label("membresia_activa"),
                empleado.c.id.label("empleado_id"),
                empleado.c.sucursal_id,
            ).select_from(
                usuario_empresa.outerjoin(
                    empleado,
                    and_(
                        empleado.c.usuario_id == usuario_empresa.c.usuario_id,
                        empleado.c.empresa_id == usuario_empresa.c.empresa_id,
                    ),
                )
            ).where(
                usuario_empresa.c.empresa_id == empresa_id,
                usuario_empresa.c.usuario_id == usuario_id,
            )
        ).mappings().one_or_none()
        if fila is None:
            raise PersonalTenantError(404, "Membresía de usuario no encontrada en esta empresa")
        if fila["empleado_id"] is None:
            raise PersonalTenantError(409, "Usuario sin perfil de empleado en esta empresa")
        destino = db.scalar(
            select(sucursal.c.id).where(
                sucursal.c.id == sucursal_id,
                sucursal.c.empresa_id == empresa_id,
                sucursal.c.estado.is_(True),
            )
        )
        if destino is None:
            # No se distingue "existe en otra empresa" de "no existe".
            raise PersonalTenantError(404, "Sucursal no disponible")
        db.execute(
            empleado.update().where(
                empleado.c.id == fila["empleado_id"],
                empleado.c.empresa_id == empresa_id,
            ).values(sucursal_id=destino)
        )
        _registrar_bitacora(
            db,
            empresa_id=empresa_id,
            usuario_id=actor_id,
            accion="PERSONAL_SUCURSAL",
            entidad="empleado",
            descripcion=(
                f"Sucursal del usuario {usuario_id} cambiada a {destino}"
            ),
        )
        return {
            "usuario_id": usuario_id,
            "empleado_id": fila["empleado_id"],
            "sucursal_id": destino,
            "membresia_activa": bool(fila["membresia_activa"]),
        }

    @staticmethod
    def roles_disponibles(db: Session, empresa_id: int) -> list[dict]:
        usados = select(usuario_empresa.c.id).where(
            usuario_empresa.c.empresa_id == empresa_id,
            usuario_empresa.c.rol_id == rol.c.id,
        ).exists()
        filas = db.execute(
            select(rol.c.id, rol.c.nombre, rol.c.estado)
            .where(
                rol.c.estado.is_(True),
                rol.c.nombre.in_(ROLES_BASE_ASIGNABLES)
                | usados,
            )
            .order_by(rol.c.nombre)
        ).mappings().all()
        return [dict(fila) for fila in filas]

    @staticmethod
    def permisos_empresa(db: Session, empresa_id: int) -> dict:
        roles = PersonalTenantRepository.roles_disponibles(db, empresa_id)
        if not roles:
            return {"empresa_id": empresa_id, "catalogo": [], "roles": []}
        filas = db.execute(
            text(
                """
                WITH catalogo AS (
                    SELECT DISTINCT funcion, accion
                      FROM saas.permiso_default
                ), roles AS (
                    SELECT unnest(CAST(:roles AS bigint[])) AS rol_id
                ), combinaciones AS (
                    SELECT CAST(:empresa AS bigint) AS empresa_id,
                           r.rol_id, c.funcion, c.accion
                      FROM roles r CROSS JOIN catalogo c
                )
                SELECT comb.rol_id, comb.funcion, comb.accion,
                       saas.tiene_permiso_tenant(
                           comb.empresa_id, comb.rol_id, comb.funcion, comb.accion
                       ) AS permitido,
                       EXISTS (
                           SELECT 1 FROM saas.permiso_tenant pt
                            WHERE pt.empresa_id = comb.empresa_id
                              AND pt.rol_id = comb.rol_id
                              AND pt.funcion = comb.funcion
                              AND pt.accion = comb.accion
                       ) AS excepcion
                  FROM combinaciones comb
                 ORDER BY comb.rol_id, comb.funcion, comb.accion
                """
            ),
            {"empresa": empresa_id, "roles": [int(r["id"]) for r in roles]},
        ).mappings().all()
        agrupados: dict[int, dict] = {
            int(r["id"]): {"rol_id": int(r["id"]), "rol": r["nombre"],
                           "permisos": []}
            for r in roles
        }
        catalogo: set[tuple[str, str]] = set()
        for fila in filas:
            rol_id = int(fila["rol_id"])
            catalogo.add((fila["funcion"], fila["accion"]))
            agrupados[rol_id]["permisos"].append(
                {
                    "funcion": fila["funcion"],
                    "accion": fila["accion"],
                    "permitido": bool(fila["permitido"]),
                    "origen": "EXCEPCION" if fila["excepcion"] else "DEFAULT",
                }
            )
        return {
            "empresa_id": empresa_id,
            "catalogo": [
                {"funcion": f, "accion": a} for f, a in sorted(catalogo)
            ],
            "roles": [agrupados[int(r["id"])] for r in roles],
        }

    @staticmethod
    def actualizar_permisos_rol(
        db: Session,
        *,
        empresa_id: int,
        actor_id: int,
        rol_id: int,
        permisos: list[dict],
    ) -> dict:
        _bloquear_empresa(db, empresa_id)
        disponible = db.execute(
            select(rol.c.id, rol.c.nombre).where(
                rol.c.id == rol_id,
                rol.c.estado.is_(True),
            )
        ).mappings().one_or_none()
        if disponible is None:
            raise PersonalTenantError(404, "Rol no disponible")
        nombre = str(disponible["nombre"]).upper()
        if nombre == ROL_ADMINISTRADOR:
            raise PersonalTenantError(
                409, "La matriz del administrador es de solo lectura"
            )
        usados = db.scalar(
            select(usuario_empresa.c.id).where(
                usuario_empresa.c.empresa_id == empresa_id,
                usuario_empresa.c.rol_id == rol_id,
            ).limit(1)
        )
        if nombre not in ROLES_BASE_ASIGNABLES and usados is None:
            raise PersonalTenantError(404, "Rol no disponible")

        catalogo = {
            (str(f), str(a))
            for f, a in db.execute(
                text("SELECT DISTINCT funcion, accion FROM saas.permiso_default")
            ).all()
        }
        pares: list[tuple[str, str, bool]] = []
        for item in permisos:
            funcion = str(item["funcion"]).strip().upper()
            accion = str(item["accion"]).strip().upper()
            if (funcion, accion) not in catalogo:
                raise PersonalTenantError(422, "Permiso no reconocido")
            pares.append((funcion, accion, bool(item["permitido"])))
        if not pares:
            raise PersonalTenantError(422, "Permisos vacios")

        for funcion, accion, permitido in pares:
            db.execute(
                text(
                    """
                    INSERT INTO saas.permiso_tenant
                        (empresa_id, rol_id, funcion, accion, permitido)
                    VALUES (CAST(:empresa AS bigint), CAST(:rol AS bigint),
                            CAST(:funcion AS text), CAST(:accion AS text),
                            CAST(:permitido AS boolean))
                    ON CONFLICT (empresa_id, rol_id, funcion, accion)
                    DO UPDATE SET permitido = EXCLUDED.permitido,
                                  actualizado_en = NOW()
                    """
                ),
                {
                    "empresa": empresa_id,
                    "rol": rol_id,
                    "funcion": funcion,
                    "accion": accion,
                    "permitido": permitido,
                },
            )
        _registrar_bitacora(
            db,
            empresa_id=empresa_id,
            usuario_id=actor_id,
            accion="RBAC_ACTUALIZADO",
            entidad="permiso_tenant",
            descripcion=(
                f"Rol {nombre} (id {rol_id}): {len(pares)} permiso(s) ajustado(s)"
            ),
        )
        return {
            "rol_id": rol_id,
            "rol": nombre,
            "actualizados": len(pares),
        }
