"""E1/5BM: motor y sesion del principal GLOBAL de aprovisionamiento SaaS.

El principal tenant (`DATABASE_URL`) sigue siendo un rol restringido sujeto a
RLS y NO puede insertar en `public.usuario`. CU01/CU02 se ejecutan con un
segundo DSN (`SAAS_ADMIN_DATABASE_URL`) cuyo rol solo tiene EXECUTE sobre las
funciones del esquema `saas_admin`; no posee privilegios de tabla.

Reglas:
  * Sin DSN configurado, el endpoint responde 503 (fail closed).
  * Esta sesion JAMAS se inyecta en rutas tenant ni en servicios comerciales.
  * No se adjunta la asercion HMAC ni `app.empresa_id`: el plano global no es
    un tenant y `saas.tenant_actual()` devuelve NULL para ese rol.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

_ENGINE = None


def motor_admin():
    """Engine perezoso del principal global. 503 si no esta configurado."""
    global _ENGINE
    dsn = settings.saas_admin_database_url
    if not dsn:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="Aprovisionamiento global no configurado",
        )
    if _ENGINE is None:
        _ENGINE = create_engine(
            dsn,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=2,
            future=True,
        )
    return _ENGINE


def get_saas_admin_db():
    """Sesion de corta vida para /saas/superadmin/*. Nunca se usa en tenant."""
    from fastapi import HTTPException

    dsn = settings.saas_admin_database_url
    if not dsn:
        raise HTTPException(
            status_code=503,
            detail="Aprovisionamiento global no configurado",
        )
    sesion = sessionmaker(
        bind=motor_admin(), autoflush=False, autocommit=False,
        expire_on_commit=False,
    )()
    try:
        yield sesion
    finally:
        sesion.close()
