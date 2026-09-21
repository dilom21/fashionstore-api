"""Adaptador OpenAI-compatible para Asistencia Inteligente.

El SDK oficial ``openai`` sirve tanto para OpenAI como para DeepSeek: este
ultimo se configura con ``base_url=https://api.deepseek.com``. Toda la
dependencia del SDK vive aqui; el service de negocio no importa ``openai``.

Seguridad: las API keys se leen solo del entorno del backend y nunca se
loguean, imprimen ni exponen al cliente.
"""

import logging

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI

from app.core.config import settings
from app.modules.asistencia_inteligente.providers.base import (
    AsistenteConfiguracionError,
    AsistenteIAProvider,
    AsistenteProveedorError,
    AsistenteTimeoutError,
)

logger = logging.getLogger(__name__)

PROVEEDOR_OPENAI = "openai"
PROVEEDOR_DEEPSEEK = "deepseek"
PROVEEDORES_VALIDOS = frozenset({PROVEEDOR_OPENAI, PROVEEDOR_DEEPSEEK})

# Modo JSON: ambos proveedores lo aceptan en sus modelos de chat. Si un modelo
# concreto no lo soporta, el error se traduce a 502 sin filtrar detalles.
_FORMATO_JSON = {"type": "json_object"}


class OpenAICompatibleProvider(AsistenteIAProvider):
    """Implementacion real sobre el SDK ``openai``."""

    def __init__(
        self,
        *,
        nombre: str,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        if not api_key:
            raise AsistenteConfiguracionError(
                "Falta la API key del proveedor de IA"
            )
        if not model:
            raise AsistenteConfiguracionError("Falta AI_MODEL")

        self._nombre = nombre
        self._model = model
        # max_retries=0: el timeout es explicito y el router decide el codigo.
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
        )

    @property
    def nombre(self) -> str:
        return self._nombre

    def _llamar(
        self, *, system: str, usuario: str, temperature: float
    ) -> str:
        try:
            respuesta = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": usuario},
                ],
                temperature=temperature,
                response_format=_FORMATO_JSON,
            )
        except APITimeoutError as exc:
            logger.error("IA timeout (%s)", self._nombre)
            raise AsistenteTimeoutError(
                "El asistente no respondio a tiempo"
            ) from exc
        except (APIConnectionError, APIError) as exc:
            # Solo el tipo de error; nunca el payload (puede traer detalles).
            logger.error(
                "IA error de proveedor (%s): %s",
                self._nombre,
                type(exc).__name__,
            )
            raise AsistenteProveedorError(
                "El proveedor de IA devolvio un error"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - no filtrar detalles tecnicos
            logger.error(
                "IA error inesperado (%s): %s",
                self._nombre,
                type(exc).__name__,
            )
            raise AsistenteProveedorError(
                "El proveedor de IA devolvio un error"
            ) from exc

        if not respuesta.choices:
            raise AsistenteProveedorError(
                "El proveedor de IA no devolvio contenido"
            )
        contenido = respuesta.choices[0].message.content
        if not contenido or not contenido.strip():
            raise AsistenteProveedorError(
                "El proveedor de IA no devolvio contenido"
            )
        return contenido

    def generar(self, *, system: str, usuario: str) -> str:
        return self._llamar(system=system, usuario=usuario, temperature=0.2)

    def extraer_intencion(self, *, system: str, usuario: str) -> str:
        # Deterministico: la extraccion de restricciones no debe "crear" datos.
        return self._llamar(system=system, usuario=usuario, temperature=0.0)


def crear_proveedor_ia() -> AsistenteIAProvider:
    """Fabrica del provider real a partir de la configuracion del backend.

    Es la unica funcion que decide entre OpenAI y DeepSeek. Los tests la
    sustituyen (via ``obtener_proveedor_asistente``) por un fake.
    """
    proveedor = (settings.ai_provider or "").strip().lower()
    if proveedor not in PROVEEDORES_VALIDOS:
        raise AsistenteConfiguracionError("AI_PROVIDER invalido")

    model = (settings.ai_model or "").strip()
    if not model:
        raise AsistenteConfiguracionError("Falta AI_MODEL")

    if proveedor == PROVEEDOR_OPENAI:
        if not settings.openai_api_key:
            raise AsistenteConfiguracionError("Falta OPENAI_API_KEY")
        return OpenAICompatibleProvider(
            nombre=PROVEEDOR_OPENAI,
            api_key=settings.openai_api_key,
            model=model,
            timeout=settings.ai_timeout_seconds,
        )

    if not settings.deepseek_api_key:
        raise AsistenteConfiguracionError("Falta DEEPSEEK_API_KEY")
    base_url = (
        settings.deepseek_base_url or "https://api.deepseek.com"
    ).strip()
    return OpenAICompatibleProvider(
        nombre=PROVEEDOR_DEEPSEEK,
        api_key=settings.deepseek_api_key,
        model=model,
        base_url=base_url,
        timeout=settings.ai_timeout_seconds,
    )
