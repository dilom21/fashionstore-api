"""Identidad y contexto de empresa para la futura autenticacion SaaS.

Este modulo NO altera la autenticacion del primer parcial. Solo los endpoints
nuevos que usan estas funciones requieren claims de tenant.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from app.core.security import create_access_token, decode_access_token

ContextoUsuario = Literal["cliente", "personal"]


@dataclass(frozen=True, slots=True)
class TenantContext:
    usuario_id: int
    empresa_id: int
    empresa_slug: str
    empresa_nombre: str
    rol_id: int
    rol_nombre: str
    contexto: ContextoUsuario
    plan_codigo: str
    permite_reportes_ia: bool


def _id_positivo(value: object) -> int | None:
    """Evita aceptar bool, null, 0 o IDs no enteros en un JWT."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        result = int(value)
    else:
        return None
    return result if result > 0 else None


def emitir_token_empresa(
    *, usuario_id: int, empresa_id: int, contexto: ContextoUsuario
) -> str:
    if usuario_id <= 0 or empresa_id <= 0 or contexto not in ("cliente", "personal"):
        raise ValueError("Contexto de empresa invalido")
    return create_access_token(
        {
            "sub": str(usuario_id),
            "scope": "tenant",
            "empresa_id": str(empresa_id),
            "contexto": contexto,
            "iat": int(datetime.now(timezone.utc).timestamp()),
            "jti": str(uuid4()),
        }
    )


def leer_token_empresa(token: str) -> tuple[int, int, ContextoUsuario] | None:
    """Firma y expiracion las verifica decode_access_token (PyJWT).

    No confia en roles enviados en el JWT; se comprueban en PostgreSQL.
    """
    claims = decode_access_token(token)
    if not claims or claims.get("scope") != "tenant":
        return None
    usuario_id = _id_positivo(claims.get("sub"))
    empresa_id = _id_positivo(claims.get("empresa_id"))
    contexto = claims.get("contexto")
    if (
        usuario_id is None
        or empresa_id is None
        or contexto not in ("cliente", "personal")
        or not isinstance(claims.get("jti"), str)
        or not claims["jti"]
    ):
        return None
    return usuario_id, empresa_id, contexto
