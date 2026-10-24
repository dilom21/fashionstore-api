# Bloque 3 / Paso 5S — RLS efectivo del principal actual y plan de ensayo limitado

**Estado: 5S PREPARADA, pendiente de la suite local y del informe PostgreSQL READ ONLY.** Paso 5 comercial EN PROCESO, **Bloque 3 4/7**. Rama exclusiva `feat/saas-multitenant-parcial2`.

## 1. Qué recibimos de 5R

El estudiante confirmó `486 passed, 2 warnings in 6.96s`; el validador 5Q siguió indicando **8 recursos y 7 bloqueantes sin resolver** con contrato no desplegable. 5R redujo la consulta de credenciales a `usuario_id` y `password_hash`, sin conceder privilegios ni alterar el login heredado. El principal SQL usado para auditorías previas fue `postgres` con `rolsuper=t`, `rolbypassrls=t`: **ninguna de esas consultas anteriores demuestra aislamiento RLS para el backend**.

Documentación oficial PostgreSQL 18:

- [System Information Functions — `row_security_active(table)`](https://www.postgresql.org/docs/18/functions-info.html): señala si RLS **se aplica en el contexto de usuario y entorno actual**, a diferencia del mero flag `relrowsecurity`.
- [Row Security Policies](https://www.postgresql.org/docs/18/ddl-rowsecurity.html): sin políticas para una tabla protegida, a un principal sujeto a RLS se le aplica denegación por defecto. Un owner sin FORCE y un superusuario normalmente eluden el filtrado.
- [Client Connection Defaults, `row_security`](https://www.postgresql.org/docs/18/runtime-config-client.html): configurar `row_security=on` **no obliga a un superusuario ni a un rol con BYPASSRLS a obedecer políticas**.

## 2. Herramientas nuevas 5S: sin datos, roles ni cambios SQL

Se añadieron estos tres archivos en la rama feature:

1. `database/saas/24_auditar_rls_efectivo_sandbox_5s.sql`: guardas exactas para `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h`, usuario `postgres`, **46 tablas públicas vacías**, **48 FK tenant validadas**, **0 políticas en las 16 tablas críticas examinadas** y **0 SP tenant nuevos**. Se ejecuta `BEGIN TRANSACTION READ ONLY` con `SET LOCAL row_security=on`, sin `SET ROLE`, DDL, DML ni `CALL`, y termina `ROLLBACK`. Consulta `current_user`/`session_user`, `rolsuper`, `rolbypassrls`, flags `relrowsecurity/relforcerowsecurity`, y el indicador nativo **`row_security_active(c.oid)`** para 16 tablas. Imprime un dictamen **`NO_CERTIFICADO`** cuando el usuario actual es superusuario o elude RLS.
2. `database/saas/24_auditar_rls_efectivo_sandbox_5s.ps1`: uso obligatorio de PostgreSQL LOCAL, `default_transaction_read_only=on`, contraseña LOCAL introducida sin eco (nunca la de Supabase), informe temporal en `$env:TEMP\fashionstore_5s_rls_principal_actual_sandbox_LOCAL_REVISAR.txt`, sin sobrescribir. Rechaza el resultado incompleto o sin `ROLLBACK`.
3. `tests_saas/test_rls_principal_actual_5s.py`: **12 pruebas estáticas** de las restricciones, guardas, comandos SQL no mutadores, detección de principal y documentos. No conectan a PostgreSQL. Suite esperada **`498 passed`** = 486 previos + 12; pendiente de ejecución real.

**Límite de certificación: el diagnóstico no acredita aislamiento entre empresas. NO crear roles** durante 5S ni ejecutar ensayos de escritura hasta obtener autorización expresa.

**La herramienta captura el problema, no lo soluciona:** PostgreSQL señalará que para `postgres` RLS no se aplica efectivamente. Tampoco hay tenants ficticios sembrados para probar `SELECT` permitido A/denegado B.

## 3. Protocolo FUTURO de aislamiento A/B (NO ejecutar en 5S)

Antes de solicitar autorización para crear roles del clúster, diseñar una prueba real que satisfaga TODO lo siguiente:

| Condición | Comprobación futura obligatoria |
|---|---|
| Principal SQL | No superusuario, `NOBYPASSRLS`, no propietario de tablas ni heredero de privilegios elevados |
| Dos empresas ficticias | A y B con clientes/productos/inventarios separados; **nunca copiar Vanter Men** |
| `SELECT` bajo RLS | Tabla empresarial A visible solo para A, B oculta para A y viceversa |
| `WITH CHECK` | Bloquear `INSERT/UPDATE` con `empresa_id` de otra empresa, además de FK compuestas |
| Bootstrap login | Validar credenciales y una sola membresía sin abrir `password_hash` al rol comercial |
| Procedures y triggers | Ensayar `SECURITY INVOKER`, permisos transitivos `INSERT bitacora` y `UPDATE carrito`, stock y Kardex |
| Auditar rol SQL real | Mostrar `row_security_active()` como true en tablas sujetas a RLS y probar denegación con permisos efectivos |
| Atomicidad/concurrencia | `ROLLBACK` íntegro ante falla; sin reservas dobles ni inconsistencias en stock reservado |

**Antes de ejecutar cualquier ensayo:** solicitar autorización explícita para crear roles de PostgreSQL, porque `CREATE ROLE` tiene efecto en el **CLÚSTER**, incluso al conectarse a una base de prueba. Preparar plan de eliminación de roles y objetos de ensayo, verificar dependencias, no alterar otras bases del equipo. Las policies y grants deberán limitarse a la base **sandbox**. No se han escrito ni autorizado instrucciones ejecutables DDL para esos ensayos.

## 4. Límites persistentes de identidad

Los siete controles del manifiesto 5Q continúan **sin resolver**: bootstrap RLS, exposición de hashes bajo credencial bootstrap, principal autenticado en `CALL`, GUC modificables, policies/grants, procedimientos legacy `EXECUTE PUBLIC` e integración transaccional PostgreSQL real. `current_setting('app.usuario_id')` o `current_setting('app.empresa_id')` no autentican frente a SQL arbitrario bajo la misma credencial de servicio. Los constructores HTTP de 5O son una capa de backend confiable, no control criptográfico de identidad SQL.

**No activar SaaS**: mantener exactamente `SAAS_AUTH_ENABLED=false` y no abrir las rutas de reserva, pago o venta SaaS. No crear roles, policies, grants, seed ni SP. Mantener `main`, Supabase y `fashionstore_restore_20261023_193554_594` fuera de cualquier modificación; el sandbox tampoco recibe escrituras en 5S.

## 5. Comandos para el estudiante

Desde `C:\SI2_Parcial1\fashionstore-api`, Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Esperado: `498 passed`** (no confirmado aún en Windows). Si hay cualquier fallo, detenerse y compartir la salida.

Solo después de aprobar la suite:

```powershell
.\database\saas\24_auditar_rls_efectivo_sandbox_5s.ps1
```

Se pedirá contraseña del PostgreSQL LOCAL. Debe mostrar `5S PREFLIGHT OK` en la consola, y el informe debe contener `1_SQL_PRINCIPAL_ACTUAL`, `2_RLS_EFECTIVO_PRINCIPAL_ACTUAL`, `3_DICTAMEN_ALCANCE` y terminar en `ROLLBACK`. En caso de éxito el script imprimirá `AUDITORIA 5S SANDBOX READ ONLY COMPLETADA`.

Abrir el informe (revisar antes de enviarlo, no subirlo a GitHub):

```powershell
notepad "$env:TEMP\fashionstore_5s_rls_principal_actual_sandbox_LOCAL_REVISAR.txt"
```

**No se afirmará que RLS protege empresas** hasta haber demostrado la operación con principal SQL restringido y datos ficticios. Los 12 tests estáticos de 5S no sustituyen esa prueba.

## 6. Estado pendiente

**5S preparada pero aún sin validación local**. Después de recibir suite y reporte, cerrar solo el diagnóstico `row_security_active()` del principal privilegiado actual; **Paso 5 seguirá EN PROCESO y Bloque 3 4/7**. No iniciar 5T ni Paso 6 antes de `siguiente`.
