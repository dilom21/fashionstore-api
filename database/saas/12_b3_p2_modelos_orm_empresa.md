# Bloque 3 — Paso 2 de 7: modelos ORM empresariales

**Estado:** cambios y pruebas preparados en la rama `feat/saas-multitenant-parcial2`, **pendientes de ejecución local de pytest**. No se modificaron datos en Supabase, PostgreSQL local ni `main`.

## Objetivo
Adaptar los modelos de SQLAlchemy a las **31 tablas empresariales** que ya tienen `empresa_id` en la copia de PostgreSQL multitenant del Bloque 2. Una empresa tiene varias sucursales; las tablas globales conservan sus claves y uso compartido.

## Implementación
1. `app/core/tenant_models.py`: mixin `EmpresaScopedMixin` con `empresa_id BIGINT NOT NULL`, sin default de Vanter Men. La pertenencia debe llegar desde una identidad de tenant validada. `EmpresaAuditoriaMixin` para `bitacora.empresa_id BIGINT NULL`, permitiendo eventos de plataforma.
2. En los 14 módulos con modelos ORM empresariales se agrega el mixin a cada clase. Las 30 tablas comerciales requerirán `empresa_id`; `bitacora` es la única excepción.
3. Se preservan los modelos globales `usuario`, `rol`, `ciudad`, `talla`, `color`, `accion`, `funcion`, `modulo`, `rol_funcion`. El catálogo global `token_recuperacion` no tenía modelo comercial en estos módulos y no se modifica.
4. Las restricciones `UNIQUE` de `categoria(nombre)`, `temporada(nombre)`, `variante_producto(sku)`, `sucursal(ciudad,nombre)`, `proveedor(nit)`, `cliente(ci,usuario_id)` y `empleado(ci,usuario_id)` se reflejan como **unicidad por empresa** (nombres de restricciones coherentes con migración SQL del Paso 5 del Bloque 2).
5. La tabla `orden_compra` aparece en dos clases existentes (vista mínima de proveedores CU11, modelo completo en compras CU12). Se agregó `empresa_id` a ambas sin crear una segunda tabla.
6. No se duplicaron aún las 48 `FOREIGN KEY(empresa_id,...)` en los modelos: dichas restricciones **ya existen y fueron probadas en la base PostgreSQL local**. Añadir rutas alternativas de FK al metadata de SQLAlchemy antes de fijar `foreign_keys` y `primaryjoin` podría causar ambigüedad en relaciones legacy. Se conserva la relación ORM simple mientras se migra el acceso por empresa.
7. No se realizó `Base.metadata.create_all()`, ni DDL ni SQL remoto.

## Pruebas nuevas
Archivo `tests_saas/test_tenant_orm_mapping.py` (solo Python/metadata, sin conectarse a Postgres o Supabase):
- Inventario de 31 tablas empresariales, 10 tablas globales.
- `empresa_id` BigInteger, NOT NULL en 30 tablas, NULL solo en bitácora, sin default ni PK.
- Unicidad compuesta por `empresa_id` en los siete modelos que se ajustaron.
- OrdenCompra mínima y completa comparten la misma `Table`.
- Herencia del mixin y prohibición de duplicar FKs compuestas prematuramente.

## Comandos para ejecutar en Windows (conda fashionstore-api)
```powershell
cd C:\SI2_Parcial1\fashionstore-api
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

Se esperan **79 pruebas unitarias (36 previas + 43 nuevas)** si todos los cambios pasan. No declarar aprobado hasta leer la salida de PowerShell del usuario.

## Advertencias importantes
- **NO ejecutar el FastAPI de esta rama contra Supabase original:** el esquema remoto del primer parcial sigue sin columna `empresa_id`, y SQLAlchemy la incluiría en SELECT; las consultas fallarían. Esta rama solo está lista para continuar pruebas aisladas y sobre la copia PostgreSQL local que ya migramos.
- Con `SAAS_AUTH_ENABLED=false` el middleware preventivo no bloquea rutas heredadas; ese modo **no puede usarse para exponer las cinco empresas de la copia local** hasta adaptar los repositorios.
- No habilitar `SAAS_AUTH_ENABLED`, no desplegar, no ejecutar `supabase db push`.
- Los servicios y repositorios heredados todavía no proporcionan `empresa_id` en sus INSERT ni filtran SELECT/UPDATE/DELETE, por lo que **RNF02 sigue incompleto**.
- El paso 3 del Bloque 3 abordará lectura del catálogo con contexto obligatorio de tenant y pruebas 403/404; no iniciarlo hasta la confirmación del usuario (`siguiente`).

## Estado de avance
Bloque 3: **Paso 2/7 en preparación**, pendiente de verificación local; Paso 1/7 completo con 36 tests exitosos.
