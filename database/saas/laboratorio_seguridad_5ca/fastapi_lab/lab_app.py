"""Aplicación mínima de laboratorio: mismos repositorios, pools separados.

Se usa sólo para verificar el parche de identidad sobre HTTP real; no se importa
desde app/main.py ni se despliega.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.core.tenant_context import TenantContext, emitir_token_empresa
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    TenantAuthRepository,
)
from fastapi_lab.database_lab import get_bootstrap_db, get_db
from fastapi_lab.tenant_dependencies_lab import (
    get_current_tenant_admin,
    get_current_tenant_cliente,
    get_current_tenant_context,
    obtener_credenciales_bootstrap,
    publicar_token,
)

app = FastAPI(title="FashionStore lab 5CA")


class LoginLab(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correo: str
    password: str
    empresa_id: int
    contexto: str = "cliente"


@app.post("/lab/login")
def login(datos: LoginLab,
          db_bootstrap: Session = Depends(get_bootstrap_db),
          db: Session = Depends(get_db)):
    credenciales = obtener_credenciales_bootstrap(db_bootstrap, datos.correo)
    if credenciales is None or not verify_password(datos.password,
                                                  credenciales.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Credenciales invalidas")
    if datos.contexto not in ("cliente", "personal"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Contexto invalido")
    token = emitir_token_empresa(usuario_id=credenciales.usuario_id,
                                 empresa_id=datos.empresa_id,
                                 contexto=datos.contexto)
    publicar_token(db, token)
    try:
        registro = TenantAuthRepository.obtener_membresia_vigente(
            db, usuario_id=credenciales.usuario_id, empresa_id=datos.empresa_id,
            contexto=datos.contexto)
    except SQLAlchemyError as exc:
        # Mismo hallazgo que en las dependencias: sin membresía, RLS aborta con
        # 42501 y eso significa "no autorizado", no "servicio no disponible".
        if getattr(getattr(exc, "orig", None), "sqlstate", None) == "42501":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="No autorizado para la empresa solicitada") from exc
        raise
    if registro is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="No autorizado para la empresa solicitada")
    return {"access_token": token, "token_type": "bearer"}


@app.get("/lab/me")
def me(tenant: TenantContext = Depends(get_current_tenant_context)):
    return {"usuario_id": tenant.usuario_id, "empresa_id": tenant.empresa_id,
            "rol": tenant.rol_nombre, "contexto": tenant.contexto,
            "plan": tenant.plan_codigo}


@app.get("/lab/productos")
def productos(tenant: TenantContext = Depends(get_current_tenant_context),
              db: Session = Depends(get_db)):
    return {"total": int(db.scalar(text("SELECT count(*) FROM public.producto")))}


@app.get("/lab/admin/clientes")
def clientes(tenant: TenantContext = Depends(get_current_tenant_admin),
             db: Session = Depends(get_db)):
    return {"total": int(db.scalar(text("SELECT count(*) FROM public.cliente")))}


@app.post("/lab/reservas")
def reservas(carrito_id: int,
             tenant: TenantContext = Depends(get_current_tenant_cliente),
             db: Session = Depends(get_db)):
    reserva_id = db.scalar(
        text("SELECT public.sp_crear_reserva_tenant(:e, :c, :u,"
             " now() + interval '2 days', :o)"),
        {"e": tenant.empresa_id, "c": carrito_id, "u": tenant.usuario_id,
         "o": "lab-http"},
    )
    db.commit()
    return {"reserva_id": int(reserva_id)}
