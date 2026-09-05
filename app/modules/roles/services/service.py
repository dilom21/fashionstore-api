from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Rol
from app.modules.roles.repositories.repository import RolRepository

NOMBRE_ROL_CLIENTE = "CLIENTE"
NOMBRE_ROL_ADMINISTRADOR = "ADMINISTRADOR"

ROLES_BASE = frozenset(
    {
        NOMBRE_ROL_ADMINISTRADOR,
        "ENCARGADO_SUCURSAL",
        "CAJERO",
        NOMBRE_ROL_CLIENTE,
    }
)

ACCION_MODIFICAR = "MODIFICAR"
ENTIDAD_ROL_FUNCION = "rol_funcion"


class RolNoEncontradoError(Exception):
    pass


class RolNombreDuplicadoError(Exception):
    pass


class RolNombreInvalidoError(Exception):
    pass


class RolBaseProtegidoError(Exception):
    pass


class RolAdministradorProtegidoError(Exception):
    pass


class RolEnUsoError(Exception):
    pass


class PermisoReferenciaInvalidaError(Exception):
    pass


class RolClienteNoAsignableError(Exception):
    """El rol CLIENTE no puede asignarse a un usuario interno con empleado."""


class RolService:
    """Reglas de negocio de CU04 - Gestionar roles y permisos.

    Los roles base se identifican por NOMBRE, nunca por ID fijo.
    """

    @staticmethod
    def es_rol_categoria_cliente(nombre: str) -> bool:
        return str(nombre).strip().upper() == NOMBRE_ROL_CLIENTE

    @staticmethod
    def es_rol_asignable_interno(nombre: str) -> bool:
        return not RolService.es_rol_categoria_cliente(nombre)

    @staticmethod
    def es_rol_base(nombre: str) -> bool:
        return str(nombre).strip().upper() in ROLES_BASE

    @staticmethod
    def es_rol_administrador(nombre: str) -> bool:
        return str(nombre).strip().upper() == NOMBRE_ROL_ADMINISTRADOR

    @staticmethod
    def normalizar_nombre(nombre: str) -> str:
        return str(nombre).strip().upper()

    @staticmethod
    def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
        db.execute(
            text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
            {"usuario_id": str(usuario_id)},
        )

    @staticmethod
    def listar(
        db: Session,
        asignable_interno: bool | None = None,
    ) -> list[Rol]:
        """Devuelve todos los roles para administracion.

        Si asignable_interno es True devuelve unicamente roles activos
        asignables a usuarios internos (excluye CLIENTE y deshabilitados).
        """
        roles = RolRepository.listar(db)
        if asignable_interno is True:
            roles = [
                rol
                for rol in roles
                if rol.estado and RolService.es_rol_asignable_interno(rol.nombre)
            ]
        return roles

    @staticmethod
    def obtener(db: Session, rol_id: int) -> Rol | None:
        return RolRepository.obtener_por_id(db, rol_id)

    @staticmethod
    def detalle(db: Session, rol_id: int) -> dict | None:
        rol = RolRepository.obtener_por_id(db, rol_id)
        if rol is None:
            return None
        return {
            "id": rol.id,
            "nombre": rol.nombre,
            "descripcion": rol.descripcion,
            "estado": rol.estado,
            "es_base": RolService.es_rol_base(rol.nombre),
            "usuarios_activos": RolRepository.contar_usuarios_activos(db, rol.id),
        }

    @staticmethod
    def validar_rol_para_usuario_interno(rol: Rol) -> None:
        """Rechaza asignar un rol de categoria CLIENTE a un usuario interno.

        Regla de negocio centralizada para POST /usuarios y PATCH /usuarios.
        """
        if RolService.es_rol_categoria_cliente(rol.nombre):
            raise RolClienteNoAsignableError()

    @staticmethod
    def crear(db: Session, datos, admin: Rol) -> Rol:
        """Crea un rol personalizado. Los roles base no pueden crearse porque
        ya existen y su nombre es unico.
        """
        nombre = RolService.normalizar_nombre(datos.nombre)
        if not nombre:
            raise RolNombreInvalidoError()

        if RolRepository.obtener_por_nombre(db, nombre) is not None:
            raise RolNombreDuplicadoError()

        descripcion = (
            datos.descripcion.strip() if datos.descripcion is not None else None
        )
        descripcion = descripcion or None

        try:
            RolService._establecer_contexto_bitacora(db, admin.id)
            rol = RolRepository.crear(db, nombre, descripcion)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise RolNombreDuplicadoError() from exc

        rol = RolRepository.obtener_por_id(db, rol.id)
        if rol is None:
            raise RolNoEncontradoError()
        return rol

    @staticmethod
    def actualizar(db: Session, rol_id: int, datos, admin: Rol) -> Rol:
        rol = RolRepository.obtener_por_id(db, rol_id)
        if rol is None:
            raise RolNoEncontradoError()

        hay_cambios = False

        if datos.nombre is not None:
            nuevo_nombre = RolService.normalizar_nombre(datos.nombre)
            if nuevo_nombre != rol.nombre:
                if RolService.es_rol_base(rol.nombre):
                    raise RolBaseProtegidoError()
                existente = RolRepository.obtener_por_nombre(db, nuevo_nombre)
                if existente is not None and existente.id != rol.id:
                    raise RolNombreDuplicadoError()
                rol.nombre = nuevo_nombre
                hay_cambios = True

        if datos.descripcion is not None:
            descripcion = datos.descripcion.strip() or None
            if descripcion != rol.descripcion:
                rol.descripcion = descripcion
                hay_cambios = True

        if not hay_cambios:
            return rol

        try:
            RolService._establecer_contexto_bitacora(db, admin.id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise RolNombreDuplicadoError() from exc

        rol = RolRepository.obtener_por_id(db, rol.id)
        if rol is None:
            raise RolNoEncontradoError()
        return rol

    @staticmethod
    def cambiar_estado(
        db: Session, rol_id: int, estado: bool, admin: Rol
    ) -> Rol:
        rol = RolRepository.obtener_por_id(db, rol_id)
        if rol is None:
            raise RolNoEncontradoError()

        if rol.estado == estado:
            return rol

        if not estado:
            if RolService.es_rol_administrador(rol.nombre):
                raise RolAdministradorProtegidoError()
            if RolRepository.contar_usuarios_activos(db, rol.id) > 0:
                raise RolEnUsoError()

        rol.estado = estado
        RolService._establecer_contexto_bitacora(db, admin.id)
        db.commit()

        rol = RolRepository.obtener_por_id(db, rol.id)
        if rol is None:
            raise RolNoEncontradoError()
        return rol

    @staticmethod
    def _construir_arbol_permisos(
        db: Session, otorgadas: set[tuple[int, int]] | None
    ) -> list[dict]:
        modulos, funciones, acciones = RolRepository.obtener_catalogo_activo(db)
        modulos_activos = {modulo.id for modulo in modulos}
        acciones_por_id = {
            accion.id: accion
            for accion in acciones
        }
        funciones_por_modulo: dict[int, list[dict]] = {}
        for funcion in funciones:
            if funcion.modulo_id not in modulos_activos:
                continue
            funciones_por_modulo.setdefault(funcion.modulo_id, []).append(funcion)

        arbol: list[dict] = []
        for modulo in modulos:
            funciones_json = []
            for funcion in funciones_por_modulo.get(modulo.id, []):
                acciones_json = []
                for accion_id, accion in acciones_por_id.items():
                    item: dict = {
                        "id": accion.id,
                        "nombre": accion.nombre,
                        "descripcion": accion.descripcion,
                    }
                    if otorgadas is not None:
                        item["otorgada"] = (funcion.id, accion.id) in otorgadas
                    acciones_json.append(item)
                funciones_json.append(
                    {
                        "id": funcion.id,
                        "nombre": funcion.nombre,
                        "descripcion": funcion.descripcion,
                        "acciones": acciones_json,
                    }
                )
            arbol.append(
                {
                    "id": modulo.id,
                    "nombre": modulo.nombre,
                    "descripcion": modulo.descripcion,
                    "funciones": funciones_json,
                }
            )
        return arbol

    @staticmethod
    def catalogo_permisos(db: Session) -> list[dict]:
        return RolService._construir_arbol_permisos(db, None)

    @staticmethod
    def obtener_permisos_rol(db: Session, rol_id: int) -> dict:
        rol = RolRepository.obtener_por_id(db, rol_id)
        if rol is None:
            raise RolNoEncontradoError()

        otorgadas = set(RolRepository.listar_permisos(db, rol_id))
        return {
            "rol_id": rol.id,
            "rol_nombre": rol.nombre,
            "modulos": RolService._construir_arbol_permisos(db, otorgadas),
        }

    @staticmethod
    def reemplazar_permisos(
        db: Session,
        rol_id: int,
        pares: list[tuple[int, int]],
        admin: Rol,
    ) -> Rol:
        """Reemplaza la matriz completa del rol de forma atomica.

        ADMINISTRADOR tiene su matriz en solo lectura (anti-lockout).
        La operacion es idempotente: si la matriz no cambia no se escribe.
        """
        rol = RolRepository.obtener_por_id(db, rol_id)
        if rol is None:
            raise RolNoEncontradoError()

        if RolService.es_rol_administrador(rol.nombre):
            raise RolAdministradorProtegidoError()

        pares_sin_duplicados = list(dict.fromkeys(pares))
        RolService._validar_referencias(db, pares_sin_duplicados)

        actuales = set(RolRepository.listar_permisos(db, rol_id))
        if actuales == set(pares_sin_duplicados):
            return rol

        RolService._establecer_contexto_bitacora(db, admin.id)
        RolRepository.reemplazar_permisos(db, rol_id, pares_sin_duplicados)
        RolRepository.registrar_evento_bitacora(
            db,
            usuario_id=admin.id,
            accion=ACCION_MODIFICAR,
            entidad_afectada=ENTIDAD_ROL_FUNCION,
            descripcion=(
                f"Rol {rol.nombre} (id {rol.id}): matriz de permisos "
                f"actualizada a {len(pares_sin_duplicados)} permiso(s)"
            ),
        )
        db.commit()

        rol = RolRepository.obtener_por_id(db, rol.id)
        if rol is None:
            raise RolNoEncontradoError()
        return rol

    @staticmethod
    def _validar_referencias(
        db: Session, pares: list[tuple[int, int]]
    ) -> None:
        modulos, funciones, acciones = RolRepository.obtener_catalogo_activo(db)
        modulos_activos = {modulo.id for modulo in modulos}
        accion_ids_activos = {accion.id for accion in acciones}
        funcion_ids_validos = {
            funcion.id
            for funcion in funciones
            if funcion.modulo_id in modulos_activos
        }

        for funcion_id, accion_id in pares:
            if (
                funcion_id not in funcion_ids_validos
                or accion_id not in accion_ids_activos
            ):
                raise PermisoReferenciaInvalidaError()

    @staticmethod
    def tiene_permiso(
        db: Session,
        rol_id: int,
        nombre_funcion: str,
        nombre_accion: str,
    ) -> bool:
        return RolRepository.tiene_permiso(
            db, rol_id, nombre_funcion, nombre_accion
        )
