# FASE 2 — validación de restauración PostgreSQL 17

Fecha: 2026-11-06  
Respaldo: `C:\Users\Harold\Documents\FashionStore_Backups\20261106_135445\fashionstore_public_full.custom`  
Destino aislado: `fashionstore_restore_validation_20261106_135445` en `127.0.0.1:5432`, PostgreSQL 17.9.

## Dictamen

**RESPALDO RECUPERABLE PARA EL ESQUEMA `public`, CON LIMITACIONES DE ACL SUPABASE.**

La restauración terminó con código 0, dentro de una única transacción, sobre una base nueva cuya ausencia y vacuidad fueron verificadas antes de comenzar. No se sobrescribió ni eliminó ninguna base existente. La base restaurada se conserva para inspección posterior.

## Integridad de los archivos

| Archivo | Bytes | SHA-256 verificado |
|---|---:|---|
| `fashionstore_public_schema_only.sql` | 171887 | `dd3ab5fb3bd79cf4b16e2b0942610e51d9479a71f4c2e1c948be5ca061bf705e` |
| `fashionstore_public_full.custom` | 260823 | `ca0ab6c2201b3cf77f0c29af171f0df7136afbfb61194950241cd6057ec74dfe` |
| `fashionstore_public_full_inventory.txt` | 37715 | `8cb0ef0ecf3a8c1c92eab5b3b5732b63c6fa5e87c31f9b618e0ffd5575eb06b5` |

Los tamaños y hashes coinciden con `verification.json`. `pg_restore --list` leyó 568 entradas y el archivo ya había superado la lectura completa hacia `NUL`.

## Procedimiento empleado

1. Confirmación de que el nombre de destino no existía.
2. Creación de una base PostgreSQL 17 nueva desde `template0`.
3. Verificación de nombre, versión y cero relaciones de usuario.
4. Retirada del esquema `public` vacío de la base nueva para permitir que el archivo recreara su definición original.
5. Restauración mediante `pg_restore --no-owner --no-privileges --exit-on-error --single-transaction`.
6. Consultas de catálogo y conteos sobre la base restaurada.

Se usaron `--no-owner` y `--no-privileges` porque el archivo referencia los roles administrados de Supabase `anon`, `authenticated` y `service_role`, que no existen en el clúster local. No se crearon roles globales artificiales.

## Inventario restaurado

| Objeto | Esperado | Restaurado |
|---|---:|---:|
| Tablas | 41 | 41 |
| Vistas | 1 | 1 |
| Secuencias | 37 | 37 |
| Funciones | 15 | 15 |
| Procedimientos | 9 | 9 |
| Triggers | 27 | 27 activos |
| Claves foráneas | 60 | 60 |
| Índices | 134 | 134 |
| Tablas con RLS | 38 | 38 |
| FORCE RLS | 0 | 0 |
| Políticas | 0 | 0 |

Todas las constraints están validadas (`unvalidated_constraints=0`) y todos los índices son válidos (`invalid_indexes=0`). La vista `vw_inventario_stock` conserva `security_invoker=true` y devuelve 265 filas.

## Conteos históricos

| Tabla | Original | Restaurado |
|---|---:|---:|
| `usuario` | 9 | 9 |
| `sucursal` | 4 | 4 |
| `cliente` | 5 | 5 |
| `empleado` | 4 | 4 |
| `producto` | 14 | 14 |
| `variante_producto` | 120 | 120 |
| `inventario` | 265 | 265 |
| `reserva` | 11 | 11 |
| `venta` | 40 | 40 |
| `pago` | 30 | 30 |
| `bitacora` | 806 | 806 |

Datos AR restaurados adicionalmente: 8 configuraciones, 6 sesiones y 6 pruebas.

## Dependencias

- El archivo custom no contiene entradas `EXTENSION`.
- El destino dispone únicamente de `plpgsql`, suficiente para las rutinas restauradas.
- No existen FK hacia objetos fuera de `public`.
- No se encontraron referencias de rutinas a `auth`, `storage` ni `extensions`.
- Todos los objetos restaurados quedaron bajo el owner local `postgres` por el uso deliberado de `--no-owner`.
- Las ACL de Supabase no fueron reproducidas por el uso deliberado de `--no-privileges`.

## Alcance y limitaciones

- El resultado demuestra recuperabilidad de estructura y datos de `public` en PostgreSQL 17.
- No constituye un respaldo completo del proyecto Supabase: no incluye Auth, Storage, roles administrados, configuración del proyecto, Edge Functions ni otros esquemas.
- La restauración local no valida la fidelidad de ownership y grants Supabase.
- No se ejecutaron procedimientos de negocio ni pruebas que modificaran los datos restaurados.
- No se aplicó ninguna migración SaaS.
- Supabase remoto no fue modificado.
