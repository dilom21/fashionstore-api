"""Contrato del proveedor de IA para Asistencia Inteligente.

El service de negocio depende de esta abstraccion, no del SDK ``openai``. Asi
los tests inyectan un fake y la suite no consume tokens ni depende de internet.

Nunca se exponen ni loguean API keys desde aqui.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class AsistenteIAError(Exception):
    """Base de errores del proveedor de IA (mapeados a HTTP en el router)."""


class AsistenteConfiguracionError(AsistenteIAError):
    """Falta provider/model/api key. El endpoint responde 503."""


class AsistenteTimeoutError(AsistenteIAError):
    """El proveedor no respondio a tiempo. El endpoint responde 504."""


class AsistenteProveedorError(AsistenteIAError):
    """El proveedor devolvio un error. El endpoint responde 502."""


class AsistenteRespuestaInvalidaError(AsistenteIAError):
    """El proveedor no devolvio un JSON valido segun el contrato. 502."""


@dataclass(frozen=True)
class RecomendacionIA:
    """Seleccion cruda del modelo: solo id candidato y motivo.

    No transporta datos comerciales (nombre, precio, stock, etc.): esos se
    reconstruyen siempre desde PostgreSQL.
    """

    inventario_id: int
    motivo: str


@dataclass(frozen=True)
class RespuestaIA:
    """Respuesta cruda normalizada del modelo."""

    titulo: str
    descripcion: str
    recomendaciones: list[RecomendacionIA]


class AsistenteIAProvider(ABC):
    """Operacion minima que Asistencia Inteligente necesita de un modelo."""

    @property
    @abstractmethod
    def nombre(self) -> str:
        """Identificador del proveedor (openai | deepseek)."""

    @abstractmethod
    def generar(self, *, system: str, usuario: str) -> str:
        """Devuelve el contenido de texto del modelo (JSON esperado).

        Debe lanzar los errores tipados de este modulo:
        ``AsistenteTimeoutError`` y ``AsistenteProveedorError``.
        """

    def extraer_intencion(self, *, system: str, usuario: str) -> str:
        """Extrae restricciones duras de la consulta libre (opcional).

        Es una operacion DISTINTA del ranking: solo devuelve intencion
        estructurada (presupuesto, talla, color, sucursal y preferencias) y
        NUNCA datos comerciales. La implementacion por defecto no extrae nada;
        los proveedores que la soportan la sobrescriben. Si falla, el backend
        continua con la extraccion determinista y el ranking normal.
        """
        return "{}"
