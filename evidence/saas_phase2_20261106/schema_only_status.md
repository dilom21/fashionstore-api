# Estado del respaldo schema-only

No se generó un archivo `pg_dump` schema-only en esta fase.

## Herramientas comprobadas

- `pg_dump` PostgreSQL 17.9: disponible.
- `psql` PostgreSQL 17.9: disponible.
- Supabase CLI: no instalado.
- Docker: no instalado.

## Intentos de conexión

1. La conexión directa mediante `pg_dump` 17 alcanzó el servidor PostgreSQL, pero no había una contraseña autorizada coincidente para ese host.
2. Existe un `pgpass.conf` local, pero no contiene una entrada asociada al project ref `zyitwlxfkphthlvlmuej`.
3. No se leyó ni mostró ninguna contraseña y no se creó una URL de conexión.

Por tanto, no se improvisó una reconstrucción llamándola `pg_dump`. Como evidencia sustitutiva, el directorio `current_catalog/` contiene un snapshot de catálogo obtenido exclusivamente con consultas `SELECT`: relaciones y columnas, constraints, índices, rutinas completas, triggers, vista, secuencias, ACL, RLS y políticas.

Para completar el respaldo formal será necesaria una conexión PostgreSQL de solo lectura utilizable por `pg_dump` 17, o una instalación autenticada de Supabase CLI v3 usando explícitamente `db dump --remote`.
