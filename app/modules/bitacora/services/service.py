from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.bitacora.repositories.repository import (
    BitacoraRegistro,
    BitacoraRepository,
)
from app.modules.bitacora.schemas.schemas import (
    BitacoraCatalogosResponse,
    BitacoraDetalleResponse,
    BitacoraListResponse,
    BitacoraResponse,
    BitacoraUsuarioFiltroResponse,
    BitacoraUsuarioResumen,
)

LIMITE_MAXIMO = 100
LIMITE_POR_DEFECTO = 50
DESPLAZAMIENTO_POR_DEFECTO = 0


class RangoFechasInvalidoError(Exception):
    """fecha_desde es posterior a fecha_hasta."""


class PaginacionInvalidaError(Exception):
    """limit fuera de rango o offset negativo."""


class BitacoraService:
    """Reglas de negocio de CU05 - Consultar bitacora del sistema.

    Modulo de solo lectura: ningun metodo escribe ni audita.
    """

    @staticmethod
    def _consciente(value: datetime | None) -> datetime | None:
        """Interpreta fechas sin zona horaria como UTC.

        La columna fecha_hora es TIMESTAMPTZ; normalizar a UTC evita
        comparaciones ambiguas contra fechas naive enviadas por el cliente.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def _serializar_usuario(registro: BitacoraRegistro) -> BitacoraUsuarioResumen | None:
        if registro.usuario_id is None or registro.usuario_correo is None:
            return None
        return BitacoraUsuarioResumen(
            id=registro.usuario_id,
            correo=registro.usuario_correo,
            rol=registro.rol_nombre or "",
        )

    @staticmethod
    def _serializar_item(
        registro: BitacoraRegistro, detalle: bool = False
    ) -> BitacoraResponse:
        modelo = BitacoraDetalleResponse if detalle else BitacoraResponse
        return modelo(
            id=registro.id,
            fecha_hora=registro.fecha_hora,
            ip=registro.ip,
            accion=registro.accion,
            entidad_afectada=registro.entidad_afectada,
            descripcion=registro.descripcion,
            usuario=BitacoraService._serializar_usuario(registro),
        )

    @staticmethod
    def _validar_rango(
        fecha_desde: datetime | None, fecha_hasta: datetime | None
    ) -> tuple[datetime | None, datetime | None]:
        desde = BitacoraService._consciente(fecha_desde)
        hasta = BitacoraService._consciente(fecha_hasta)
        if desde is not None and hasta is not None and desde > hasta:
            raise RangoFechasInvalidoError()
        return desde, hasta

    @staticmethod
    def _validar_paginacion(limit: int, offset: int) -> None:
        if limit < 1 or limit > LIMITE_MAXIMO or offset < 0:
            raise PaginacionInvalidaError()

    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None = None,
        usuario_id: int | None = None,
        accion: str | None = None,
        entidad_afectada: str | None = None,
        fecha_desde: datetime | None = None,
        fecha_hasta: datetime | None = None,
        limit: int = LIMITE_POR_DEFECTO,
        offset: int = DESPLAZAMIENTO_POR_DEFECTO,
    ) -> BitacoraListResponse:
        BitacoraService._validar_paginacion(limit, offset)
        desde, hasta = BitacoraService._validar_rango(fecha_desde, fecha_hasta)

        filtros = {
            "buscar": buscar,
            "usuario_id": usuario_id,
            "accion": accion,
            "entidad_afectada": entidad_afectada,
            "fecha_desde": desde,
            "fecha_hasta": hasta,
        }

        total = BitacoraRepository.contar(db, **filtros)
        registros = BitacoraRepository.listar(
            db, limit=limit, offset=offset, **filtros
        )
        items = [BitacoraService._serializar_item(registro) for registro in registros]

        return BitacoraListResponse(
            items=items, total=total, limit=limit, offset=offset
        )

    @staticmethod
    def obtener(db: Session, bitacora_id: int) -> BitacoraDetalleResponse | None:
        registro = BitacoraRepository.obtener_por_id(db, bitacora_id)
        if registro is None:
            return None
        return BitacoraService._serializar_item(registro, detalle=True)  # type: ignore[return-value]

    @staticmethod
    def catalogos(db: Session) -> BitacoraCatalogosResponse:
        """Catalogos reales desde BD para poblar los filtros del frontend."""
        usuarios = [
            BitacoraUsuarioFiltroResponse(id=usuario_id, correo=correo)
            for usuario_id, correo in BitacoraRepository.listar_usuarios_para_filtro(
                db
            )
        ]
        return BitacoraCatalogosResponse(
            acciones=BitacoraRepository.listar_acciones(db),
            entidades=BitacoraRepository.listar_entidades(db),
            usuarios=usuarios,
        )
