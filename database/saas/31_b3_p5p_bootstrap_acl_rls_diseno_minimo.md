# Bloque 3 — Paso 5P: preflight de autenticación/RLS y permisos por columna

**Estado: 5P PREPARADA, PENDIENTE de pruebas locales y del informe PostgreSQL READ ONLY.** Paso 5 comercial EN PROCESO; Bloque 3 4/7. Trabajar exclusivamente en `feat/saas-multitenant-parcial2`.

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

**Pruebas estáticas nuevas:** `tests_saas/test_bootstrap_acl_rls_preflight_5p.py` incluye **14 casos**, sin BD. Total esperado **`454 passed`** (440 anteriores +14), **todavía no validado localmente**.

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

**5P PENDIENTE** hasta verificar pytest y recibir informe completo de ACL/permisos. Estos artefactos no hacen seguro el bootstrap por sí solos: **no se implementó aún una ruta de autenticación con credencial limitada ni un aislamiento frente a SQL arbitrario**. Solo después de conocer las concesiones reales podremos decidir una ruta segura y solicitar autorización específica para un futuro ensayo de roles/DDL limitado al clúster local, recordando que `CREATE ROLE` tiene efecto global.

**Paso 5 EN PROCESO; Bloque 3 sigue 4/7.** Mantener `SAAS_AUTH_ENABLED=false`, `main`, Supabase, BD restaurada y sandbox sin mutaciones. Esperar `siguiente` antes de abrir la subfase 5Q.
