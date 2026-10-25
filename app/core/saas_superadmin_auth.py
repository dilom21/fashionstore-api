"""CU01/CU02: credencial propia de superadministrador SaaS (opt-in).

Un JWT tenant (aunque tenga rol ADMINISTRADOR) NUNCA concede alcance
global. Este guard usa clave aleatoria de >=32 caracteres suministrada
fuera del repositorio y su hash SHA256 en la configuración. No hay
credencial por defecto, login público, alta automática ni bypass debug.

Solo para entornos aislados con HTTPS y principal SQL restringido:
no habilitar en producción hasta integrar RBAC, audit log, rotación
y rate limiting. No tocar PG comercial ni Supabase.
"""
from __future__ import annotations

import hashlib
import re
import secrets

from fastapi import Header, HTTPException

from app.core.config import settings


def get_superadmin_saas(
    x_saas_admin_key: str | None = Header(default=None),
) -> bool:
    # Dos flags independientes: SaaS empresarial + módulo superadmin.
    if not settings.saas_auth_enabled or not settings.saas_superadmin_enabled:
        raise HTTPException(status_code=503,
                            detail="Administración SaaS no habilitada")
    configured=settings.saas_superadmin_key_sha256
    if (not isinstance(configured,str)
        or re.fullmatch(r"[0-9a-fA-F]{64}",configured) is None):
        raise HTTPException(status_code=503,
                            detail="Credencial superadmin no configurada")
    if not x_saas_admin_key or len(x_saas_admin_key)<32:
        raise HTTPException(status_code=401,detail="Credencial inválida")
    # Nunca aceptar tokens tenant, user IDs, empresa_id o rol en su lugar.
    supplied=hashlib.sha256(x_saas_admin_key.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(supplied,configured.lower()):
        raise HTTPException(status_code=401,detail="Credencial inválida")
    return True
