from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.sucursales.models.models import Ciudad, Sucursal
from app.modules.sucursales.repositories.repository import (
    CiudadRepository,
    SucursalRepository,
)
from app.modules.sucursales.schemas.schemas import (
    CiudadCreate,
    CiudadEstadoUpdate,
    CiudadUpdate,
    SucursalCreate,
    SucursalEstadoUpdate,
    SucursalUpdate,
)

ACCION_CREAR = "CREAR"
ACCION_MODIFICAR = "MODIFICAR"
ENTIDAD_CIUDAD = "ciudad"


class CiudadNoEncontradaError(Exception):
    pass


class CiudadNombreInvalidoError(Exception):
    pass


class CiudadNombreDuplicadoError(Exception):
    pass


class CiudadEnUsoError(Exception):
    """La ciudad no puede deshabilitarse porque tiene sucursales activas."""


class SucursalNoEncontradaError(Exception):
    pass


class SucursalNombreInvalidoError(Exception):
    pass


class SucursalDireccionInvalidaError(Exception):
    pass


class SucursalNombreDuplicadoError(Exception):
    pass


class SucursalCiudadInexistenteError(Exception):
    pass


class SucursalCiudadInactivaError(Exception):
    """No se permite crear/mover una sucursal a una ciudad inactiva."""


class SucursalEnUsoError(Exception):
    """La sucursal no puede deshabilitarse por dependencias operativas activas."""


def _limpiar_texto(valor: str | None) -> str:
    if valor is None:
        return ""
    return " ".join(str(valor).split())


def _normalizar_telefono(valor: str | None) -> str | None:
    if valor is None:
        return None
    telefono = _limpiar_texto(valor)
    return telefono or None


class CiudadService:
    """Reglas de negocio CU06 - Gestionar ciudades."""

    @staticmethod
    def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
        db.execute(
            text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
            {"usuario_id": str(usuario_id)},
        )

    @staticmethod
    def _enriquecer(db: Session, ciudades: list[Ciudad]) -> list[dict]:
        conteos = CiudadRepository.agrupar_conteo_sucursales(db)
        return [
            {
                "id": ciudad.id,
                "nombre": ciudad.nombre,
                "estado": ciudad.estado,
                "sucursales_total": conteos.get(ciudad.id, {}).get(
                    "sucursales_total", 0
                ),
                "sucursales_activas": conteos.get(ciudad.id, {}).get(
                    "sucursales_activas", 0
                ),
            }
            for ciudad in ciudades
        ]

    @staticmethod
    def listar(
        db: Session,
        buscar: str | None = None,
        estado: bool | None = None,
    ) -> list[dict]:
        """Lista ciudades para administracion.

        Por compatibilidad con la convencion del listado de sucursales de
        CU03, si no se indica estado se devuelven solo las activas.
        """
        if estado is None:
            estado = True
        ciudades = CiudadRepository.listar(
            db, buscar=buscar, estado=estado
        )
        return CiudadService._enriquecer(db, ciudades)

    @staticmethod
    def obtener(db: Session, ciudad_id: int) -> dict | None:
        ciudad = CiudadRepository.obtener_por_id(db, ciudad_id)
        if ciudad is None:
            return None
        return CiudadService._enriquecer(db, [ciudad])[0]

    @staticmethod
    def crear(
        db: Session, datos: CiudadCreate, admin: Usuario
    ) -> dict:
        nombre = _limpiar_texto(datos.nombre)
        if not nombre:
            raise CiudadNombreInvalidoError()

        if CiudadRepository.buscar_por_nombre_normalizado(db, nombre) is not None:
            raise CiudadNombreDuplicadoError()

        try:
            CiudadService._establecer_contexto_bitacora(db, admin.id)
            ciudad = CiudadRepository.crear(db, nombre=nombre)
            CiudadRepository.registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_CREAR,
                entidad_afectada=ENTIDAD_CIUDAD,
                descripcion=f"Ciudad creada: {ciudad.nombre} (id {ciudad.id})",
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise CiudadNombreDuplicadoError() from exc

        ciudad = CiudadRepository.obtener_por_id(db, ciudad.id)
        if ciudad is None:
            raise CiudadNoEncontradaError()
        return CiudadService._enriquecer(db, [ciudad])[0]

    @staticmethod
    def actualizar(
        db: Session,
        ciudad_id: int,
        datos: CiudadUpdate,
        admin: Usuario,
    ) -> dict:
        ciudad = CiudadRepository.obtener_por_id(db, ciudad_id)
        if ciudad is None:
            raise CiudadNoEncontradaError()

        hay_cambios = False
        if datos.nombre is not None:
            nuevo_nombre = _limpiar_texto(datos.nombre)
            if not nuevo_nombre:
                raise CiudadNombreInvalidoError()
            if nuevo_nombre != ciudad.nombre:
                existente = CiudadRepository.buscar_por_nombre_normalizado(
                    db, nuevo_nombre
                )
                if existente is not None and existente.id != ciudad.id:
                    raise CiudadNombreDuplicadoError()
                ciudad.nombre = nuevo_nombre
                hay_cambios = True

        if not hay_cambios:
            return CiudadService._enriquecer(db, [ciudad])[0]

        try:
            CiudadService._establecer_contexto_bitacora(db, admin.id)
            CiudadRepository.registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_CIUDAD,
                descripcion=(
                    f"Ciudad actualizada: {ciudad.nombre} (id {ciudad.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise CiudadNombreDuplicadoError() from exc

        ciudad = CiudadRepository.obtener_por_id(db, ciudad.id)
        if ciudad is None:
            raise CiudadNoEncontradaError()
        return CiudadService._enriquecer(db, [ciudad])[0]

    @staticmethod
    def cambiar_estado(
        db: Session,
        ciudad_id: int,
        datos: CiudadEstadoUpdate,
        admin: Usuario,
    ) -> dict:
        ciudad = CiudadRepository.obtener_por_id(db, ciudad_id)
        if ciudad is None:
            raise CiudadNoEncontradaError()

        estado = datos.estado
        if ciudad.estado == estado:
            return CiudadService._enriquecer(db, [ciudad])[0]

        if not estado:
            if CiudadRepository.contar_sucursales_activas(db, ciudad.id) > 0:
                raise CiudadEnUsoError()

        ciudad.estado = estado
        try:
            CiudadService._establecer_contexto_bitacora(db, admin.id)
            CiudadRepository.registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_CIUDAD,
                descripcion=(
                    f"Ciudad {'habilitada' if estado else 'deshabilitada'}: "
                    f"{ciudad.nombre} (id {ciudad.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise CiudadEnUsoError() from exc

        ciudad = CiudadRepository.obtener_por_id(db, ciudad.id)
        if ciudad is None:
            raise CiudadNoEncontradaError()
        return CiudadService._enriquecer(db, [ciudad])[0]


class SucursalService:
    """Reglas de negocio CU06 - Gestionar sucursales.

    Mantiene los metodos usados por CU03 (listar_sucursales, obtener_sucursal)
    con la misma semantica de solo sucursales activas.
    """

    @staticmethod
    def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
        db.execute(
            text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
            {"usuario_id": str(usuario_id)},
        )

    @staticmethod
    def listar_sucursales(db: Session) -> list[Sucursal]:
        """Contrato CU03: lista unicamente sucursales activas."""
        return SucursalService.listar(db)

    @staticmethod
    def listar(
        db: Session,
        buscar: str | None = None,
        ciudad_id: int | None = None,
        estado: bool | None = None,
    ) -> list[Sucursal]:
        """Lista sucursales con filtros.

        Sin parametro estado devuelve solo activas (compatible CU03). Con
        estado=false permite listar las deshabilitadas en administracion.
        """
        if estado is None:
            estado = True
        return SucursalRepository.listar(
            db,
            buscar=buscar,
            ciudad_id=ciudad_id,
            estado=estado,
        )

    @staticmethod
    def obtener_sucursal(db: Session, sucursal_id: int) -> Sucursal | None:
        """Contrato CU03/inventario: obtiene una sucursal activa."""
        return SucursalRepository.obtener_activa_por_id(db, sucursal_id)

    @staticmethod
    def _validar_ciudad_para_sucursal(
        db: Session, ciudad_id: int
    ) -> Ciudad:
        ciudad = CiudadRepository.obtener_por_id(db, ciudad_id)
        if ciudad is None:
            raise SucursalCiudadInexistenteError()
        if not ciudad.estado:
            raise SucursalCiudadInactivaError()
        return ciudad

    @staticmethod
    def crear(
        db: Session, datos: SucursalCreate, admin: Usuario
    ) -> Sucursal:
        nombre = _limpiar_texto(datos.nombre)
        if not nombre:
            raise SucursalNombreInvalidoError()
        direccion = _limpiar_texto(datos.direccion)
        if not direccion:
            raise SucursalDireccionInvalidaError()
        telefono = _normalizar_telefono(datos.telefono)

        SucursalService._validar_ciudad_para_sucursal(db, datos.ciudad_id)

        if (
            SucursalRepository.buscar_duplicada(
                db,
                nombre=nombre,
                ciudad_id=datos.ciudad_id,
            )
            is not None
        ):
            raise SucursalNombreDuplicadoError()

        try:
            SucursalService._establecer_contexto_bitacora(db, admin.id)
            sucursal = SucursalRepository.crear(
                db,
                ciudad_id=datos.ciudad_id,
                nombre=nombre,
                direccion=direccion,
                telefono=telefono,
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise SucursalNombreDuplicadoError() from exc

        sucursal = SucursalRepository.obtener_por_id(db, sucursal.id)
        if sucursal is None:
            raise SucursalNoEncontradaError()
        return sucursal

    @staticmethod
    def actualizar(
        db: Session,
        sucursal_id: int,
        datos: SucursalUpdate,
        admin: Usuario,
    ) -> Sucursal:
        sucursal = SucursalRepository.obtener_por_id(db, sucursal_id)
        if sucursal is None:
            raise SucursalNoEncontradaError()

        nombre_efectivo = sucursal.nombre
        if datos.nombre is not None:
            nuevo_nombre = _limpiar_texto(datos.nombre)
            if not nuevo_nombre:
                raise SucursalNombreInvalidoError()
            nombre_efectivo = nuevo_nombre

        direccion_efectiva = sucursal.direccion
        if datos.direccion is not None:
            nueva_direccion = _limpiar_texto(datos.direccion)
            if not nueva_direccion:
                raise SucursalDireccionInvalidaError()
            direccion_efectiva = nueva_direccion

        telefono_efectivo = sucursal.telefono
        if datos.telefono is not None:
            telefono_efectivo = _normalizar_telefono(datos.telefono)

        ciudad_efectiva_id = sucursal.ciudad_id
        if datos.ciudad_id is not None and datos.ciudad_id != sucursal.ciudad_id:
            SucursalService._validar_ciudad_para_sucursal(db, datos.ciudad_id)
            ciudad_efectiva_id = datos.ciudad_id

        hay_cambios = (
            nombre_efectivo != sucursal.nombre
            or direccion_efectiva != sucursal.direccion
            or telefono_efectivo != sucursal.telefono
            or ciudad_efectiva_id != sucursal.ciudad_id
        )
        if not hay_cambios:
            return sucursal

        if (
            SucursalRepository.buscar_duplicada(
                db,
                nombre=nombre_efectivo,
                ciudad_id=ciudad_efectiva_id,
                excluir_id=sucursal.id,
            )
            is not None
        ):
            raise SucursalNombreDuplicadoError()

        sucursal.nombre = nombre_efectivo
        sucursal.direccion = direccion_efectiva
        sucursal.telefono = telefono_efectivo
        sucursal.ciudad_id = ciudad_efectiva_id

        try:
            SucursalService._establecer_contexto_bitacora(db, admin.id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise SucursalNombreDuplicadoError() from exc

        sucursal = SucursalRepository.obtener_por_id(db, sucursal.id)
        if sucursal is None:
            raise SucursalNoEncontradaError()
        return sucursal

    @staticmethod
    def cambiar_estado(
        db: Session,
        sucursal_id: int,
        datos: SucursalEstadoUpdate,
        admin: Usuario,
    ) -> Sucursal:
        sucursal = SucursalRepository.obtener_por_id(db, sucursal_id)
        if sucursal is None:
            raise SucursalNoEncontradaError()

        estado = datos.estado
        if sucursal.estado == estado:
            return sucursal

        if not estado:
            empleados = SucursalRepository.contar_empleados_activos(
                db, sucursal.id
            )
            inventario = SucursalRepository.contar_inventario_pendiente(
                db, sucursal.id
            )
            if empleados > 0 or inventario > 0:
                raise SucursalEnUsoError()

        sucursal.estado = estado
        try:
            SucursalService._establecer_contexto_bitacora(db, admin.id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise SucursalEnUsoError() from exc

        sucursal = SucursalRepository.obtener_por_id(db, sucursal.id)
        if sucursal is None:
            raise SucursalNoEncontradaError()
        return sucursal
