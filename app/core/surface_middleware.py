"""5C1: fija la superficie de la peticion antes de llegar al router.

La superficie decide CONTRA QUE BASE se abre la sesion de ``get_db``. No
autentica ni autoriza: las rutas tenant siguen exigiendo JWT + membresia y las
heredadas siguen usando su propio contrato. Tampoco se acepta la superficie
desde un encabezado, query o cookie: se deduce del path resuelto por Starlette.
"""
from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.database import superficie_actual
from app.core.surface_guard import superficie_de_ruta


class ResolucionSuperficie:
    """Middleware ASGI que publica la superficie de la peticion en un ContextVar.

    Se registra por dentro de CORS y por fuera del gate de rutas, de modo que
    tanto el gate como el router leen la misma decision. El token se reinicia
    en ``finally`` para que una conexion reutilizada por el pool de asyncio
    nunca herede la superficie de otra peticion.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        token = superficie_actual.set(superficie_de_ruta(scope.get("path", "")))
        try:
            await self.app(scope, receive, send)
        finally:
            superficie_actual.reset(token)
