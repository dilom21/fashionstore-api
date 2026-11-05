"""CU01/CU02: credencial propia de superadministrador SaaS (opt-in).

Un JWT tenant (aunque tenga rol ADMINISTRADOR) NUNCA concede alcance
global. Este guard usa claves aleatorias de >=32 caracteres suministradas
fuera del repositorio y sus huellas SHA-256 en la configuración. No hay
credencial por defecto, login público, alta automática ni bypass debug.

Optimización 5BO (rotación sin romper contratos previos)
-------------------------------------------------------
`SAAS_SUPERADMIN_KEY_SHA256` acepta una huella o una lista de huellas:

  * `"<hex64>"`                     -> una sola clave, identificador "v1";
  * `"v2:<hex64>,v1:<hex64>"`       -> dos claves vigentes simultáneamente.

El encabezado opcional `X-SaaS-Admin-Key-Id` selecciona el identificador; si
se omite, la clave se compara contra todas las vigentes. La rotación es
"baja + alta" en la misma lista y reinicio del proceso. No se guarda ni se
registra nunca la clave en claro.

Rate limiting y auditoría NO viven aquí: los aplica
`app.core.saas_superadmin_guard` con contadores en PostgreSQL, de modo que la
protección es compartida entre procesos. Este módulo es puro (sin I/O) para
poder probarse de forma aislada.

Solo para entornos aislados con HTTPS y principal SQL restringido: no
habilitar en producción hasta completar la fase de activación.
"""
from __future__ import annotations

import hashlib
import re
import secrets

from fastapi import Header, HTTPException

from app.core.config import settings

_KID_PERMITIDO = re.compile(r"[A-Za-z0-9_-]{1,16}")
_HUELLA = re.compile(r"[0-9a-fA-F]{64}")


def get_superadmin_saas(
    x_saas_admin_key: str | None = Header(default=None),
    x_saas_admin_key_id: str | None = Header(default=None),
) -> bool:
    # Dos flags independientes: SaaS empresarial + módulo superadmin.
    if not settings.saas_auth_enabled or not settings.saas_superadmin_enabled:
        raise HTTPException(status_code=503,
                            detail="Administración SaaS no habilitada")
    configured=settings.saas_superadmin_key_sha256
    if not isinstance(configured,str) or not configured.strip():
        raise HTTPException(status_code=503,
                            detail="Credencial superadmin no configurada")
    claves=[]
    for parte in configured.split(","):
        parte=parte.strip()
        if not parte:
            continue
        if ":" in parte:
            kid,huella=parte.split(":",1)
        else:
            kid,huella="v1",parte
        kid=kid.strip()
        huella=huella.strip()
        if (re.fullmatch(r"[A-Za-z0-9_-]{1,16}",kid) is None
                or re.fullmatch(r"[0-9a-fA-F]{64}",huella) is None):
            raise HTTPException(status_code=503,
                                detail="Credencial superadmin no configurada")
        claves.append((kid,huella.lower()))
    if not claves:
        raise HTTPException(status_code=503,
                            detail="Credencial superadmin no configurada")
    if not x_saas_admin_key or len(x_saas_admin_key)<32:
        raise HTTPException(status_code=401,detail="Credencial inválida")
    # Nunca aceptar tokens tenant, user IDs, empresa_id o rol en su lugar.
    supplied=hashlib.sha256(x_saas_admin_key.encode("utf-8")).hexdigest()
    esperadas=[huella for _,huella in claves]
    if x_saas_admin_key_id is not None and x_saas_admin_key_id.strip():
        seleccion=[huella for kid,huella in claves
                   if kid==x_saas_admin_key_id.strip()]
        if len(seleccion)!=1:
            raise HTTPException(status_code=401,detail="Credencial inválida")
        esperadas=seleccion
    for huella in esperadas:
        if secrets.compare_digest(supplied,huella):
            return True
    raise HTTPException(status_code=401,detail="Credencial inválida")


def claves_configuradas() -> list[dict]:
    """Identificadores vigentes (nunca huellas) para verificación de rotación.

    No se usa dentro de `get_superadmin_saas`: ese guard se mantiene puro y
    probado de forma aislada. Este helper alimenta solo
    `GET /saas/superadmin/claves`.
    """
    configured = settings.saas_superadmin_key_sha256
    if not isinstance(configured, str) or not configured.strip():
        return []
    vigentes: list[dict] = []
    for parte in configured.split(","):
        parte = parte.strip()
        if not parte:
            continue
        if ":" in parte:
            kid, huella = parte.split(":", 1)
        else:
            kid, huella = "v1", parte
        kid = kid.strip()
        huella = huella.strip()
        if (_KID_PERMITIDO.fullmatch(kid) is None
                or _HUELLA.fullmatch(huella) is None):
            continue
        vigentes.append({"kid": kid, "huella_sha256_parcial": huella[:12].lower()})
    return vigentes


def huella_clave(clave: str | None) -> str:
    """Huella parcial no reversible (12 hex) para auditoría y rate limit."""
    if not clave:
        return "0" * 12
    return hashlib.sha256(clave.encode("utf-8")).hexdigest()[:12]
