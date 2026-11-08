# Arquitectura recomendada para cerrar `050`

## Elección

Usar tres logins PostgreSQL de servicio, restringidos y con pools separados:

- `bootstrap`: sólo ejecuta una función que devuelve `usuario_id/password_hash` por correo.
- `api`: operaciones autenticadas, con el JWT original colocado mediante `SET LOCAL app.actor_token`.
- `platform_admin`: exclusivamente rutas `/saas/superadmin`; acceso global intencional pero sin BYPASSRLS, DDL ni tablas comerciales fuera del onboarding.

El GUC no es identidad. Cada helper RLS verifica nuevamente HS256, `exp`, `iat`, `jti`, scope, contexto, membresía, empresa y suscripción. El secreto se consulta desde Supabase Vault por un owner `NOLOGIN`; nunca aparece en SQL. Un GUC falsificado sin JWT válido falla cerrado.

Separar owners `context`, `bootstrap`, `SP` y `audit` limita el daño de cada función `SECURITY DEFINER`. Ninguno tiene LOGIN, BYPASSRLS, CREATE, CREATEROLE o CREATEDB.

## Por qué no login por usuario

Es seguro y coincide con el verificador experimental actual, pero obliga a crear/rotar credenciales por usuario y fragmenta el pool. No es imprescindible si PostgreSQL puede verificar una credencial firmada que el login compartido no puede fabricar. El JWT existente ya es esa credencial.

## Alternativas descartadas

- `app.empresa_id/app.usuario_id` sin firma: el mismo login puede modificarlos.
- Login por empresa: evita cruces entre empresas, no la suplantación dentro de una empresa.
- Confiar sólo en FastAPI: una consulta omitida o inyección evita filtros de aplicación.
- Migrar ahora a Supabase Auth/Data API: ofrece `auth.uid()` y pooling administrado, pero cambia IDs/auth/repositorios y supera el cambio mínimo requerido.
- SECURITY DEFINER amplio o `service_role`: elude RLS y amplía el impacto de una vulnerabilidad.

## Cambios mínimos de FastAPI (no implementados)

1. Conservar el JWT bearer crudo después de validarlo y ponerlo en `Session.info['actor_token']`.
2. En `after_begin`, ejecutar `set_config('app.actor_token', token, true)` antes de cualquier consulta autenticada. No usar los GUC de IDs como autorización.
3. Establecer el token inmediatamente después de decodificarlo, antes de consultar membresía; idealmente sustituir la consulta directa por un helper de contexto verificado.
4. Crear un engine/pool bootstrap separado con credencial dedicada y llamar `fashionstore_private.bootstrap_credencial`, sin SELECT directo sobre `usuario`.
5. Crear otro engine/pool para superadmin y cambiar esas rutas a `get_platform_db`; actualmente reutilizan `get_db`.
6. Usar logins FastAPI dedicados, nunca `postgres`, `service_role` ni BYPASSRLS. El pool tenant puede seguir con tamaño 5/overflow 5.
7. Sustituir la comprobación `session_user por usuario` de `tenant_reserva_transaccional_5al.py` por `actor_empresa_id/actor_usuario_id` verificados; conservar controles de owner, ACL y RLS.
8. Insertar en cada SP tenant una guardia que compare parámetros con el actor firmado. No confiar en los parámetros del CALL.
9. Eliminar el INSERT directo de `tenant_write_repository` a bitácora o reemplazarlo por una función de auditoría estrecha; el trigger queda bajo owner NOLOGIN.
10. Mantener flags SaaS apagados hasta aprobar pruebas de pool, RLS y SP.

## Pruebas obligatorias

- Dos empresas; dos usuarios en A y uno en B; cliente, admin y personal de sucursal.
- SELECT/INSERT/UPDATE/DELETE positivos y negativos para cada policy.
- A1 no puede leer ni modificar recursos de A2 que sean personales.
- A/B no ven filas cruzadas aunque conozcan los IDs.
- Manipular `app.empresa_id`/`app.usuario_id` no cambia identidad.
- Token alterado, expirado, sin `jti`, con `alg=none` o empresa sin membresía falla 42501.
- Cambio de membresía/suscripción invalida acceso en la siguiente sentencia, aunque el JWT no expire.
- CALL con actor o empresa distintos falla; concurrencia de última unidad mantiene stock.
- Conexión física reutilizada secuencial y concurrentemente A1→B1→A2 no conserva token tras commit/rollback.
- Login bootstrap no puede consultar tablas, Vault ni funciones autenticadas.
- Login platform_admin no puede consultar tablas comerciales no requeridas ni asumir roles tenant.
- Login API no puede leer Vault, crear objetos, cambiar roles, ejecutar legacy ni escribir bitácora directamente.
- Ejecutar advisors de seguridad/rendimiento después del ensayo Supabase aislado.

## Estado

Los archivos 050a/050b/050c son propuestas bloqueadas, no migraciones aprobadas. Requieren prueba en una base desechable restaurada y revisión de las políticas contra cada endpoint antes de retirar sus guardas.
