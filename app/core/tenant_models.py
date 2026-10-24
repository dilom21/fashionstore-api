"""Campos compartidos para el mapeo ORM de recursos empresariales.

Cada recurso comercial pertenece a una empresa (tenant). Estos mixins solo
describen la columna existente en PostgreSQL; NO crean tablas ni migraciones.

La FK empresarial `empresa_id -> empresa(id)`, las 48 FK compuestas y las
28 claves UNIQUE de apoyo fueron creadas y verificadas en los SQL versionados
del Bloque 2. En esta fase NO duplicamos las 48 FK como `ForeignKeyConstraint`
en el mapper: las relaciones legacy tienen FK simples y agregar ambas rutas
en SQLAlchemy puede introducir `AmbiguousForeignKeysError` hasta que se
especifique `foreign_keys` / `primaryjoin` en cada relación. Esa adaptación
se realizará al migrar los repositorios por empresa.

No se asigna default de Vanter Men. Todo INSERT en recurso empresarial debe
recibir un empresa_id explícito desde un contexto validado; los servicios
legacy permanecen bloqueados en modo SaaS hasta su migración.
"""

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column


class EmpresaScopedMixin:
    """Campo obligatorio en tablas empresariales, salvo bitacora."""

    empresa_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class EmpresaAuditoriaMixin:
    """Permite NULL exclusivamente en eventos de plataforma sin tenant."""

    empresa_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
