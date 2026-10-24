# Bloque 3 — Paso 5P: preflight de autenticación/RLS y permisos por columna

**Estado: 5P VALIDADA solo en pruebas estáticas + auditoría READ ONLY + dictamen de ACL reales.** Paso 5 comercial EN PROCESO; Bloque 3 4/7. Trabajar exclusivamente en `feat/saas-multitenant-parcial2`.

**PROHIBIDO en 5P:** modificar `main`, proyecto Supabase, base restaurada `fashionstore_restore_20261023_193554_594` o incluso escribir en sandbox `fashionstore_saas_reservas_sandbox_5h`. No crear roles globales, políticas, GRANT, REVOKE, seed, procedimientos ni ejecutar `CALL`. Conservar `SAAS_AUTH_ENABLED=false` y rutas transaccionales bloqueadas.

## 1. Qué validó 5O

`440 passed, 2 warnings in 7.17s` confirmó los constructores Python **inertes** de tres `CALL` tenant. No se llamó a SQL ni se instaló ninguno de los borradores `sp_*_tenant.sql.txt`. **No están resueltos** los tres bloqueos: bootstrap de login sin políticas, identidad del invocador en los SP y GUC `app.empresa_id/app.usuario_id` mutable ante SQL arbitrario. RLS no se ha ensayado con una cuenta SQL limitada.

## 2. Ruta real de login: inventario de permisos que falta

La API de login SaaS sigue el orden:
1. `AuthService.autenticar_credenciales` obtiene `Usuario` por `correo` normalizado y estado (mediante `AuthRepository.obtener_usuario_activo_por_correo`); carga `Rol` relacionado y **verifica el hash Argon2 en Python**, `verify_password(password, usuario.password_hash)`. Por eso el backend actual necesita acceder a `usuario.password_hash` **antes** de saber qué tenant es válido.
2. `TenantAuthRepository.obtener_membresia_vigente` utiliza `usuario_empresa`, `usuario`, `empresa`, `rol`, `suscripcion`, `plan_saas` y exige perfil de `cliente` o `empleado+sucursal` correspondiente. Devuelve rol empresarial, plan y contexto.
3. Solo después de verificar token o credenciales y comprobar membresía se establecen `app.empresa_id` y `app.usuario_id` transaccionales. Las políticas que exijan esos GUC para todas las tablas de identidad impedirían el paso 1/2: **circularidad de bootstrap**.

**Regla negativa crítica:** jamás solucionar esa circularidad mediante `GRANT SELECT ON usuario TO PUBLIC`, `USING (true)` global, exposición directa de `password_hash`, acceso PostgreSQL para clientes finales, ni login de la API con `postgres`/`BYPASSRLS`.

## 3. Modelos de autenticación considerados (solo diseño)

| Modelo | Consecuencia | Decisión actual |
|---|---|---|
| Una credencial SQL de superusuario `postgres` para toda la API | Elude RLS; filtrado depende solo de SQL Python | **RECHAZADO** |
| Una cuenta comercial `NOSUPERUSER NOBYPASSRLS` y permitir `SELECT` a toda la tabla `usuario` para login | Posible lectura masiva de hashes/membresías si esa credencial se compromete | **RECHAZADO** como solución rápida |
| Dos cuentas SQL backend, bootstrap de credenciales y comercial, ambas con permisos mínimos y pools aislados | Evita dar `password_hash` al rol comercial; la credencial bootstrap aún necesita restricciones fuertes y puede enumerar usuarios si obtiene `SELECT` general | **CANDIDATO**, diseño/no implementado |
| Autenticación fuera de las tablas de negocio con proveedor de identidad o interfaz de verificación cuidadosamente revisada | Puede evitar exponer `password_hash` al principal comercial; requiere rediseño, identidad verificable y migración | **ALTERNATIVA A EVALUAR**, no implementada |
| Cuenta SQL separada por empresa o identidad validada DB-side contra suplantación | Permite una barrera independiente de GUC editable por SQL de la sesión; roles son GLOBALes y aumenta complejidad/pooling | **ALTERNATIVA DE AISLAMIENTO FUERTE**, no implementada |

**Límite explícito:** una conexión comercial compartida cuya identidad solo sea `current_setting('app.empresa_id')` es defensa en profundidad ante errores accidentales en backend confiable. **NO** resiste SQL injection/arbitrariedad ejecutada con la misma credencial, que puede cambiar ese valor. Tampoco lo soluciona comparar `p_usuario_id` con `app.usuario_id`. Los constructores de 5O solo impiden que la API tome ID de parámetros HTTP, **no autentican el principal de PostgreSQL**.

**Condición para avanzar a SQL ejecutable:** seleccionar un modelo de amenaza que cubra llamadas SQL directas o declare explícitamente esa amenaza fuera de alcance; decidir el mecanismo DB-side, la auditoría de identidad y el alcance del rol por tenant/servicio sin depender de `postgres`.

## 4. Permisos mínimos que se deben estudiar antes de crear roles

| Objeto / operación | Bootstrap inicial | Comercial de reservas | Auditoría |
|---|---|---|---|
| `usuario` (`id,correo,estado,password_hash`) | Lectura acotada para verificar credenciales, **nunca acceso público** | No lectura de `password_hash` | Solo ID del actor |
| `rol`, `usuario_empresa`, `empresa`, `suscripcion`, `plan_saas` | Resolver UNA membresía y plan en login | Revalidación tenant y elegibilidad al ejecutar SP | No mutar |
| `cliente`/`empleado`/`sucursal` | Confirmar perfil y sucursal | Solo principal de sesión y empresa autorizada | No mutar |
| `carrito`/`detalle_carrito` | Sin privilegios directos | SELECT/UPDATE solo si propio y en operación validada | Trigger de actividad modifica `carrito` |
| `reserva`/`detalle_reserva`/`inventario`/`movimiento_inventario` | Sin privilegios directos | Permisos exactos que requiera `SECURITY INVOKER`; `USING`/`WITH CHECK` diseñados y testeados | Trigger de inventario genera bitácora |
| `bitacora` | Sin acceso general | `INSERT` controlado por trigger, nunca `SELECT` general | `SELECT` solo de auditores autorizados |
| Rutinas `sp_*reserva*` heredadas | Sin `EXECUTE` | **No invocar legacy**; revisar `EXECUTE PUBLIC` en sandbox | Sin `EXECUTE` |

Una `GRANT` no sustituye RLS; `WITH CHECK` verifica nuevas filas pero no autentica la sesión. La API debe seguir parametrizando consultas y comprobando membresía/propiedad con filtros SQL explícitos.

## 5. Auditoría 5P preparada — PostgreSQL SOLO LECTURA

- `database/saas/21_auditar_bootstrap_rls_permisos_sandbox.sql`: `BEGIN TRANSACTION READ ONLY` y `ROLLBACK`, preflight de host/BD exactos, **46 tablas `public` vacías**, **48 FK compuestas tenant validadas**, **0 policies en 16 tablas críticas**, **0 procedimientos tenant**. Examina únicamente METADATOS, nunca valores de columnas.
- Sección `1_SQL_ROLE_FLAGS`: banderas `LOGIN/SUPERUSER/BYPASSRLS/CREATEROLE/CREATEDB` de roles existentes acotados, sin contraseñas.
- Sección `2_BOOTSTRAP_RLS`: tablas necesarias para login y reserva, estado RLS, propietario y número de policies.
- Sección `3_AUTH_TABLE_GRANTS`: concesiones de tabla declaradas para principal propietario y `PUBLIC`, y roles SaaS disponibles.
- Secciones `4_BOOTSTRAP_COLUMN_RIGHTS` y `5_EXPLICIT_COLUMN_ACL`: **solo permisos booleanos y metadatos ACL** de `usuario.password_hash`, `usuario.correo`, membresía y rol. **NO muestra contenidos, nombres personales, correos ni hashes**.
- Sección `6_ROLE_CONNECT_SCHEMA`: CONNECT/USAGE/SELECT a nivel de tabla, INSERT bitácora.
- Sección `7_LEGACY_RESERVA_EXECUTE`: cuatro SP heredados, permiso `EXECUTE PUBLIC` y SECURITY DEFINER; no obtiene cuerpos ni los invoca.
- Sección `8_PG_ROLE_MEMBERSHIP`: pertenencia de eventuales roles `fashionstore_5p_%` sin crear ninguno.

`database/saas/21_auditar_bootstrap_rls_permisos_sandbox.ps1` obliga conexión a `127.0.0.1:5432 / fashionstore_saas_reservas_sandbox_5h` con `default_transaction_read_only=on`, contraseña PostgreSQL **LOCAL**, archivo en `$env:TEMP\fashionstore_5p_bootstrap_rls_permisos_sandbox_LOCAL_REVISAR.txt` y sin sobrescritura. Restaurará variables de entorno; **no subir el informe a GitHub**.

**Pruebas estáticas nuevas:** `tests_saas/test_bootstrap_acl_rls_preflight_5p.py` incluye **14 casos**, sin BD. Total confirmado en Windows: **`454 passed, 2 warnings in 7.06s`** (440 anteriores +14), cero fallos.

## 6. Comandos para el estudiante

Desde `C:\SI2_Parcial1\fashionstore-api` en el entorno Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Únicamente si las 454 pruebas se aprueban:**

```powershell
.\database\saas\21_auditar_bootstrap_rls_permisos_sandbox.ps1
```

Debe indicar `5P PREFLIGHT OK`, terminar con `ROLLBACK`, y `AUDITORIA 5P SANDBOX READ ONLY COMPLETADA`. Revisar antes de compartir:

```powershell
notepad "$env:TEMP\fashionstore_5p_bootstrap_rls_permisos_sandbox_LOCAL_REVISAR.txt"
```

Compartir salida pytest + PowerShell y archivo TXT revisado por configuraciones sensibles. No ejecutar comandos DDL, SQL de borradores o scripts de seed. Si hay un fallo, detenerse y compartirlo.

## 7. Cierre / decisión pendiente

**5P VALIDADA exclusivamente en alcance de revisión estática, auditoría READ ONLY y dictamen del informe íntegro recibido.** Estos artefactos no hacen seguro el bootstrap por sí solos: **no se implementó aún una ruta de autenticación con credencial limitada ni un aislamiento frente a SQL arbitrario**. Solo después de conocer las concesiones reales podremos decidir una ruta segura y solicitar autorización específica para un futuro ensayo de roles/DDL limitado al clúster local, recordando que `CREATE ROLE` tiene efecto global.

**Paso 5 EN PROCESO; Bloque 3 sigue 4/7.** Mantener `SAAS_AUTH_ENABLED=false`, `main`, Supabase, BD restaurada y sandbox sin mutaciones. Esperar `siguiente` antes de abrir la subfase 5Q.


## 8. Dictamen final 5P — evidencia PostgreSQL recibida (2026-10-24)

**Prueba local confirmada:** en Windows, tras actualizar la rama de `73e4de8` a `0bbed28`, `python -m pytest -q tests_saas` produjo **`454 passed, 2 warnings in 7.06s`**, cero fallos. Las dos warnings conocidas son Starlette/httpx y `HTTP_422_UNPROCESSABLE_ENTITY` deprecado; no certifican ni invalidan RLS.

**Preflight ejecutado:** `21_auditar_bootstrap_rls_permisos_sandbox.ps1` conectó únicamente a `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h`. PostgreSQL informó literalmente `5P PREFLIGHT OK: sandbox, 46 tablas vacias, 48 FK tenant, 0 policies criticas, 0 SP tenant`. La salida inicia `BEGIN`, `SET`, `SET`, `DO` y termina en **`ROLLBACK`**. Se recibió informe local TEMP íntegro, sin llevarlo a GitHub. En particular, este preflight comprueba **ausencia de policies solo para las 16 tablas enumeradas**, no necesariamente para todas las 46.

### 8.1 Resultados verificados del catálogo PostgreSQL

| Sección | Evidencia observada | Alcance y límite |
|---|---|---|
| `1_SQL_ROLE_FLAGS` | Solo aparece `postgres` entre los roles solicitados, con `rolcanlogin=t`, `rolsuper=t`, `rolbypassrls=t`, `rolcreaterole=t`, `rolcreatedb=t`, `rolinherit=t` | Es una **consulta filtrada de roles**, no prueba que PostgreSQL tenga un único rol total. `postgres` no sirve para certificar RLS efectivo. |
| `2_BOOTSTRAP_RLS` | **16/16** tablas inspeccionadas tienen `rls_enabled=t`, `force_rls=f`, owner `postgres`, **0 policies** | Una cuenta restringida sujeta a RLS quedaría bajo denegación por defecto sin policies. No se han probado conexiones de esa clase. |
| `3_AUTH_TABLE_GRANTS` | **104 filas** de ACL/default para **13 tablas**, todas a `postgres`; **no aparecen grants a `PUBLIC`** | Solo las 13 tablas elegidas y los beneficiarios incluidos en filtro. No concluir sobre todas las tablas, futuros roles ni privilegios indirectos. |
| `4_BOOTSTRAP_COLUMN_RIGHTS` | **13 combinaciones columna/rol**; solo `postgres`, `puede_leer_columna=t` en todas; incluye `usuario.password_hash` | No se leyeron hashes ni datos de usuarios. La prueba de acceso efectiva se hizo únicamente para superusuario. |
| `5_EXPLICIT_COLUMN_ACL` | **9 columnas examinadas** muestran `acl_explicita` NULL, incluyendo `usuario.password_hash`, `correo` y claves de membresía | Significa **sin ACL específico por columna** en esas 9 columnas; NO significa que no existan permisos por tabla. |
| `6_ROLE_CONNECT_SCHEMA` | Solo `postgres` aparece, con CONNECT/USAGE y SELECT de `usuario`/`usuario_empresa`, INSERT `bitacora` permitidos | No existe una cuenta restringida de prueba comprobada. |
| `7_LEGACY_RESERVA_EXECUTE` | **4/4 SP antiguos tienen `execute_public=t`, `security_definer=f`, owner `postgres`** | Son `sp_cancelar_reserva`, `sp_crear_reserva`, `sp_crear_reserva_desde_carrito`, `sp_finalizar_reserva_sin_compra`. EXECUTE a PUBLIC **no equivale a acceso a tablas** ni a ejecución exitosa bajo RLS. |
| `8_PG_ROLE_MEMBERSHIP` | **0 filas** para membresías relacionadas con `fashionstore_5p_%` | No certifica ausencia de toda membresía de roles en el clúster. |

### 8.2 Conclusiones de seguridad y decisión

1. **No se puede demostrar un login SaaS sin bypass RLS todavía:** el login actual necesita `usuario.password_hash` para la verificación Argon2 en Python y luego leer membresía y suscripción antes de disponer de tenant validado; los permisos observados corresponden a `postgres` superusuario. Diseñar un camino restringido para bootstrap es obligatorio antes de activar `SAAS_AUTH_ENABLED`.
2. **No otorgar SELECT general a la credencial comercial** sobre `usuario` o `password_hash` como atajo. Separar privilegios de autenticación y comerciales sigue como **propuesta**, no como implementación aprobada. El paso exacto requiere definir modelo de principal y reglas de datos mínimos; evitar `SECURITY DEFINER` propiedad de superusuario sin auditoría.
3. **Cerrar la superficie `EXECUTE PUBLIC` legacy** en futura etapa controlada y autorizada **solo en sandbox** sin romper funcionalidad legacy. No `REVOKE` en BD restaurada ni Supabase. No generar nuevas rutinas con exposición pública por defecto.
4. **RLS no autentica GUC:** `app.empresa_id`, `app.usuario_id` y los argumentos de `CALL` son modificables bajo SQL arbitrario. Las mejoras 5N/5O fortalecen el backend HTTP confiable, pero no equivalen a autenticar al invocador PostgreSQL.
5. **La próxima subfase no debe crear roles automáticamente.** `CREATE ROLE` es global al clúster: requiere autorización explícita y consideración del resto de bases del equipo. Una futura prueba A/B con roles `NOSUPERUSER` y `NOBYPASSRLS`, policies `USING/WITH CHECK`, datos ficticios y comprobaciones reales de `row_security_active()` seguirá pendiente.

**CIERRE:** 5P validada en su alcance de metadatos, diseño y pruebas estáticas, **no** en autenticación SQL real, política RLS aplicada, seguridad de `CALL`, ni transacciones de reserva. **Paso 5 sigue EN PROCESO; Bloque 3 4/7.** `SAAS_AUTH_ENABLED=false`, la base histórica y Supabase no intervenidas, y sandbox sin escrituras según ejecución de solo lectura. Esperar a que el estudiante diga **`siguiente`** antes de abrir 5Q. **No pedir nuevos comandos ahora.**
