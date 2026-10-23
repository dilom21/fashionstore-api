# Bloque 2 — Paso 2/7: migración SQL base SaaS versionada

**Estado:** preparada y revisada estáticamente. **NO EJECUTADA** en Supabase ni en PostgreSQL local.  
**Documento rector:** `Examen Parcial-2 - Sistemas 2 - S2-2026.pdf`, Desafío VI, apartados **9.1–9.6**, y **RNF02**.  
**Repositorio y rama:** `dilom21/fashionstore-api`, `feat/saas-multitenant-parcial2`.  
**Archivo canónico:** `supabase/migrations/20261023000100_saas_base_tables.sql`.

## Objetivo
Crear únicamente las cinco tablas estructurales `empresa`, `plan_saas`, `suscripcion`, `usuario_empresa` y `empresa_configuracion`, para preparar el backend SaaS sin tocar las 41 tablas y los registros del primer parcial.

### Modelo
- `empresa`: un tenant independiente; no equivale a una sucursal.
- `plan_saas`: parámetros editables para capacidades (productos, ventas, reservas, offline, reportes e IA generativa) y máximo de sucursales activas.
- `suscripcion`: empresa, plan, fechas, estado, índice parcial que impide dos suscripciones `ACTIVA` simultáneas.
- `usuario_empresa`: identidad global `usuario.id` + rol efectivo `rol.id` específico de `empresa.id`.
- `empresa_configuracion`: datos de presentación y ajustes por empresa.

**Requisito del PDF, apartado 9.4:** Básico permite hasta 2 sucursales activas sin IA generativa; Profesional hasta 10 y con IA generativa. Los dos registros concretos de plan y las cinco empresas demo **NO** se insertan en este paso, solo se deja preparada su estructura. La regla definitiva se debe verificar en el backend.

## Correcciones respecto al borrador preliminar
1. Se usa formato de migración Supabase CLI `supabase/migrations/<YYYYMMDDHHMMSS>_nombre.sql`, en este caso `20261023000100_saas_base_tables.sql`.
2. Se agrega preflight `to_regclass`: exige `usuario` y `rol` e impide aplicar por error sobre un esquema que ya contiene cualquiera de las cinco tablas.
3. Se conserva `BIGINT` en las FK a `usuario.id` y `rol.id`, según la comprobación de Supabase real.
4. Se activa RLS en las cinco tablas, sin políticas de acceso iniciales; se revoca acceso público a tablas y secuencias.
5. La sentencia `REVOKE` para los roles `anon` y `authenticated` se ejecuta condicionalmente **solo si existen**. Así el mismo archivo puede probarse en PostgreSQL local restaurado sin roles de Supabase.
6. Se hace en una transacción con `BEGIN/COMMIT`; una falla de una sentencia revertirá los cambios de esa ejecución. `lock_timeout` de 5 segundos limita la espera por bloqueos.

## Dependencias confirmadas
- Supabase actual: PostgreSQL 17; 41 tablas existentes, cero tablas SaaS antes de este paso, cinco migraciones históricas en el servidor.
- `public.usuario.id`, `public.rol.id` y `public.usuario.rol_id` son `BIGINT`.
- PostgreSQL 18 local en Windows: backup `public` restaurado satisfactoriamente por el estudiante en `127.0.0.1:5432`, base **`fashionstore_restore_20261023_193554_594`**.
- La restauración reportó 41 tablas, una vista, 9 usuarios, 5 clientes, 4 sucursales, 14 productos, 120 variantes, 265 inventarios, 11 reservas, 40 ventas y 30 pagos.

## Importante: limitaciones y reglas de seguridad
- **Esto no implementa todavía el aislamiento de ventas/productos/clientes:** las 31 tablas empresariales originales aún carecen de `empresa_id`. El requisito 9.6 no se considera cumplido en este momento.
- Las cinco tablas nuevas inician vacías. `usuario_empresa` no concederá acceso de forma automática a ninguno de los usuarios previos.
- RLS no protege frente a roles propietarios privilegiados o `BYPASSRLS` como el rol administrativo `postgres` de Supabase; todavía se debe auditar el usuario de BD de FastAPI.
- Los campos `fecha_actualizacion` solo tienen valor por defecto en creación; mantenerlos al modificar registros requerirá reglas de aplicación o trigger futuro.
- **Mantener `SAAS_AUTH_ENABLED=false`.**
- No ejecutar el borrador anterior `database/saas/01_esquema_base_borrador.sql`; queda solo como referencia histórica. El archivo nuevo en `supabase/migrations/` es el canónico.

## ATENCIÓN: Supabase CLI y migraciones previas
El proyecto remoto conserva cinco migraciones del primer parcial en su historial, pero estos cinco archivos NO forman parte aún de `supabase/migrations/` del repositorio nuevo. Antes de usar el flujo normal de Supabase CLI sobre el servidor, debemos conciliar/respaldar ese historial en el repositorio. **No ejecutar `supabase db reset`, `supabase db push` ni `supabase migration repair` como improvisación**. No hacen falta para el Paso 3.

## Paso 3 futuro (no ejecutarlo aún)
Se probará el archivo canónico SOLO sobre la base local restaurada. Antes de ejecutar, comprobar dos veces el nombre:
`fashionstore_restore_20261023_193554_594`, host `127.0.0.1`, puerto `5432`. Se usará `psql` y se verificarán:
- 46 tablas `public` (41 antiguas + 5 nuevas).
- Todas las nuevas con RLS activo, 0 políticas por ahora.
- 9 usuarios, 4 sucursales, 40 ventas y demás conteos históricos intactos.
- Cero registros de empresa, suscripción, membresía y plan inmediatamente después de crear solo la estructura.
- Restricciones FK, unicidad, fechas y permisos funcionando.
- Si una comprobación falla, detenerse y revisar sin tocar Supabase.

**Fin del Paso 2:** SQL versionado creado en GitHub; ninguna base de datos alterada.
