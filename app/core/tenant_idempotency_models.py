"""Persistent idempotency records for authenticated customer mutations."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.tenant_models import EmpresaScopedMixin


class OperacionClienteIdempotente(EmpresaScopedMixin, Base):
    __tablename__ = "operacion_cliente_idempotente"
    __table_args__ = (
        UniqueConstraint(
            "empresa_id", "usuario_id", "tipo", "operacion_id",
            name="uq_operacion_cliente_idempotente",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tipo: Mapped[str] = mapped_column(String(40), nullable=False)
    operacion_id: Mapped[str] = mapped_column(String(36), nullable=False)
    huella: Mapped[str] = mapped_column(String(64), nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False)
    recurso_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    respuesta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
