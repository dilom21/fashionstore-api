# Evidencias FASE 2 — preparación de migración SaaS

Este directorio está deliberadamente separado de `supabase/migrations`. Ningún SQL contenido aquí fue ejecutado.

## Contenido

- `historical_migrations/`: las cinco sentencias originales recuperadas de `supabase_migrations.schema_migrations.statements[1]`.
- `current_catalog/`: snapshot JSON del catálogo `public`, obtenido sólo mediante consultas `SELECT`.
- `current_inventory.json`: conteos y precondiciones relevantes.
- `comparison_report.md`: deriva entre historial, catálogo actual y supuestos SaaS.
- `schema_only_status.md`: bloqueo del `pg_dump` schema-only formal.
- `verification_manifest.json`: tamaños y hashes de integridad.

## Garantías de alcance

- No contiene registros comerciales.
- No contiene contraseñas, tokens ni URLs con credenciales.
- No se aplicaron migraciones.
- No se ejecutó DDL ni DML contra Supabase.
- No se modificó el código de la aplicación.
- Los cuatro archivos SaaS de octubre permanecen sin aplicar.
