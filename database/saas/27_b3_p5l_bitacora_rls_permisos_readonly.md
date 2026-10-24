# Bloque 3 / Paso 5L — Auditoría de bitácora y permisos RLS (SOLO LECTURA)

**Estado: PREPARADO en la rama `feat/saas-multitenant-parcial2`; PENDIENTE de pruebas y del informe de Windows.** Paso 5 comercial EN PROCESO; Bloque 3 4/7 pasos completados.

**Restricciones:** NO crear roles, usuarios, políticas, grants/revokes, seeds, migraciones, triggers o procedimientos; NO ejecutar `CALL` ni activar SaaS. Preservar `SAAS_AUTH_ENABLED=false`; `main`, Supabase y la base restaurada `fashionstore_restore_20261023_193554_594` no se modifican. Base única de auditoría: `127.0.0.1:5432 / fashionstore_saas_reservas_sandbox_5h`, transacción `READ ONLY` con `ROLLBACK`.

## 1. Evidencia previa de 5J / 5K

5K confirmó por PowerShell **`384 passed, 2 warnings in 6.05s`** y `5K PREFLIGHT OK` sin escrituras. De 20 tablas con RLS habilitado en 5J, **0 políticas RLS**. Seis funciones distintas son llamadas por diez triggers y todas son `SECURITY INVOKER`. Dos carecen de `SET search_path` propio.

La función `fn_registrar_bitacora()` obtiene `app.usuario_id` de `current_setting(...,TRUE)`, determina `empresa_id` desde la fila y ejecuta `INSERT INTO public.bitacora`; el valor GUC `app.usuario_id` **no constituye prueba de identidad**. En 5K también apareció `fn_completar_ip_bitacora()`, pero solo como metadata, sin su cuerpo. **Esto impide todavía dar por seguros los registros de auditoría, los permisos de escritura del futuro SP `SECURITY INVOKER` y la integridad de IP/actor.**

5K halló `EXECUTE` a `PUBLIC` en diez rutinas (6 funciones trigger y 4 SP heredados). El permiso `EXECUTE` no basta para leer tablas sujetas a RLS, pero es una superficie a revisar antes de habilitar una cuenta de aplicación no superusuario. No se revoca nada en 5L.

## 2. Qué inspeccionamos sin escribir

Archivos nuevos:
- `database/saas/19_auditar_bitacora_y_rls_sandbox.sql`: `BEGIN TRANSACTION READ ONLY` + `ROLLBACK`. Preflight que exige nombre de BD, host local, **46 tablas públicas vacías**, 48 FK tenant, 0 policies en el conjunto comercial previo, 0 `sp_*_tenant` instalados. Revisa estructura exacta de `bitacora` (columnas/NULL/DEFAULT/IDENTITY), constraints, índices, políticas específicas, RLS `relrowsecurity`/ `relforcerowsecurity`, ACL, triggers, cuerpos `pg_get_functiondef` de `fn_registrar_bitacora` y `fn_completar_ip_bitacora`, `EXECUTE` a `PUBLIC`, permisos de roles existentes y secuencias vinculadas.
- `database/saas/19_auditar_bitacora_y_rls_sandbox.ps1`: conecta solo a `127.0.0.1` y la base sandbox, `default_transaction_read_only=on`, solicita clave **local** de forma segura, no muestra ni almacena la contraseña, y guarda el informe en `$env:TEMP\fashionstore_5l_bitacora_rls_sandbox_LOCAL_REVISAR.txt` fuera del repositorio. Se niega a sobrescribir el archivo si existe.
- `tests_saas/test_bitacora_rls_preflight_5l.py`: **16 pruebas ESTÁTICAS**; no conectan a PostgreSQL ni ejecutan funciones.

**Resultado esperado de la suite completa: `400 passed`**, calculado como 384 casos validados de 5K + 16 nuevos de 5L. **Todavía NO confirmado en Windows.**

## 3. Propuesta de diseño RLS sin DDL ni credenciales

| Componente | Regla deseada para estudiar | Bloqueador a verificar |
|---|---|---|
| `bitacora` | Escritura de evento solo dentro de operación tenant autorizada; lectura solo de auditoría privilegiada de esa empresa | RLS/owner/ACL reales, campos `empresa_id` y FK, IP y actor |
| `fn_registrar_bitacora()` | Actor derivado del contexto validado en backend y no de body HTTP | `app.usuario_id` es GUC de sesión modificable; no prueba identidad ante SQL malicioso |
| `fn_completar_ip_bitacora()` | No confiar en cabeceras IP sin validación del proxy, ni permitir valores arbitrarios en auditoría | Falta revisar código real y secuencia de triggers |
| `carrito` y `detalle_carrito` | `UPDATE` de actividad permitido solo para cliente/empresa de la transacción | Trigger de detalle ejecuta `UPDATE carrito`, que requiere permisos/RLS/locks |
| `reserva` e `inventario` | Cambios únicamente a través del servicio de reserva con tenant confiable | Procedimientos `SECURITY INVOKER` necesitan SELECT/UPDATE/INSERT de varias tablas, RLS `USING` y `WITH CHECK` |
| Rol SQL de ensayo | `NOSUPERUSER`, `NOBYPASSRLS`, `NOCREATEDB`, `NOCREATEROLE`, acceso mínimo a una base | `CREATE ROLE` es global al clúster; aún no autorizado |

**Modelo de confianza a revisar:** el JWT firmado identifica usuario/tenant, pero la autorización se vuelve a contrastar en PostgreSQL (`usuario_empresa`/plan/suscripción). Solo el backend de confianza puede seleccionar el tenant por transacción; `current_setting('app.empresa_id')` o `app.usuario_id` por sí solos **no autentican al usuario**, pues una sesión SQL con acceso arbitrario puede configurarlos. Evitar conceder a clientes finales un acceso PostgreSQL directo con credenciales compartidas de backend.

Aún no definimos `CREATE POLICY`: deberán contemplarse tablas globales vs. empresariales, `USING`, `WITH CHECK`, SELECT requeridos por SP, triggers de bitácora sin recursión, grants mínimos y un rol de servicio que no sea propietario de tablas. Probar autorización de empresa A/B con datos académicos totalmente ficticios y cuenta real no superusuario será tarea FUTURA, no 5L.

## 4. Comandos Windows que debe ejecutar el estudiante

Desde `C:\SI2_Parcial1\fashionstore-api` con Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Esperado `400 passed` (no validado aún).** Si hay un solo fallo, detenerse sin ejecutar la auditoría.

Solo con la suite aprobada:

```powershell
.\database\saas\19_auditar_bitacora_y_rls_sandbox.ps1
```

El script anuncia `5L PREFLIGHT OK`, luego `ROLLBACK` y `AUDITORIA 5L SANDBOX READ ONLY COMPLETADA`. No añade, borra ni altera ningún objeto o fila.

Después, revisar el archivo generado antes de enviarlo al chat:

```powershell
notepad "$env:TEMP\fashionstore_5l_bitacora_rls_sandbox_LOCAL_REVISAR.txt"
```

Las definiciones pueden contener literales sensibles; **no subir este informe a GitHub, no pegar contraseñas**, no eliminar el archivo anterior para reintentar si ya existe: reportar el error para diagnóstico.

## 5. Criterios de cierre y continuación

Cerrar 5L **solo como auditoría estática/READ ONLY + dictamen del contenido** después de recibir las pruebas y el reporte íntegro, comparando `bitacora`, `fn_completar_ip_bitacora`, políticas y privilegios con las hipótesis de 5K. No interpretar pytest ni un `ROLLBACK` como certificación de RLS efectivo.

**Aún no crear roles o políticas ni ejecutar seed**; la creación futura de roles afecta al CLÚSTER y necesita autorización expresa. Mantener `SAAS_AUTH_ENABLED=false`, `main`, Supabase y fuente restaurada sin cambios. Paso 5 comercial EN PROCESO, Bloque 3 4/7, y esperar `siguiente` para avanzar a otra subfase.
