# Bloque 3 / Paso 5R — Bootstrap mínimo de credenciales en el login SaaS

**Estado: 5R VALIDADA Y CERRADA únicamente como refactor de login SaaS y pruebas unitarias locales. PostgreSQL RLS real NO validado.** Paso 5 comercial EN PROCESO; Bloque 3 4/7. Solo `feat/saas-multitenant-parcial2`.

## Antecedentes verificados

5P: `454 passed, 2 warnings in 7.06s` y auditoría READ ONLY PostgreSQL con 46 tablas vacías, 48 FK tenant y 0 policies en las 16 tablas examinadas. Solo se observó `postgres` (superusuario con `BYPASSRLS`) entre los roles filtrados y cuatro SP heredados con `EXECUTE PUBLIC`. No se ejecutó SQL con usuario restringido.

5Q: `470 passed, 2 warnings in 7.55s` y validador offline satisfactorio: `8 recursos, 7 bloqueantes sin resolver`, matriz `BORRADOR_NO_APLICABLE`/`desplegable=false`. **Eso no equivale a RLS aplicado**.

El login SaaS heredaba `AuthService.autenticar_credenciales` y `AuthRepository.obtener_usuario_activo_por_correo`, que usan `select(Usuario)` + `joinedload(Usuario.rol)` antes de validar tenant. Esa consulta carga el ORM completo, aunque la verificación de contraseña solamente necesita `usuario.id` y `usuario.password_hash`.

## Cambios 5R en el código SaaS

1. `app/modules/autenticacion_seguridad/repositories/tenant_repository.py`: define `CredencialesBootstrapTenant` como dataclass inmutable, `repr=False` para el campo `password_hash`; la consulta SQLAlchemy **parametrizada** `_SQL_CREDENCIALES_BOOTSTRAP_TENANT` lee **solo** `u.id AS usuario_id, u.password_hash`, filtra por correo normalizado y `u.estado IS TRUE`, `LIMIT 2`. No hace `JOIN`, no selecciona `Rol` ni columnas adicionales, no retorna hash al endpoint y falla cerrado si hay cero o múltiples resultados. **Sí devuelve internamente el hash a Python** para verificación Argon2: esto NO elimina el privilegio `SELECT(password_hash)` que necesitaría un futuro principal bootstrap.
2. `app/modules/autenticacion_seguridad/services/service.py`: incorpora `AuthService.autenticar_credenciales_tenant`. Llama únicamente al repositorio mínimo, comprueba contraseña usando `verify_password` existente y entrega **solo ID** o `None`. Mantiene sin cambios `AuthService.autenticar_credenciales` y las rutas legacy `/auth/*`.
3. `app/modules/autenticacion_seguridad/api/tenant_router.py`: el endpoint `POST /auth/tenant/login` usa la nueva vía mínima, conserva los mismos campos de `TenantLoginResponse`, la verificación de membresía `TenantAuthRepository.obtener_membresia_vigente`, el JWT firmado y errores genéricos **401** para credenciales inválidas o empresa no autorizada. Un `SQLAlchemyError` durante bootstrap o consulta empresarial se traduce a **503** sin devolver el SQL ni detalles del motor. La bandera `SAAS_AUTH_ENABLED` se comprueba ANTES del acceso a credenciales.
4. `tests_saas/test_bootstrap_minimo_tenant_5r.py`: **16 pruebas** usando sesiones falsas y datos académicos ficticios, sin conexión PostgreSQL. Comprueban SQL mínimo/proyección/parametrización, normalización, usuario ausente o ambiguo, hash oculto de `repr`, verificación de contraseña, retorno solo ID, respuesta HTTP sin hash, claims JWT, membresía revocada con error genérico, errores SQL controlados, flag inactivo, rechazo de IDs/rol enviados por HTTP y compatibilidad legacy.

**Total confirmado en Windows:** **`486 passed, 2 warnings in 6.96s`** (470 anteriores + 16 nuevas); cero pruebas fallidas. Las advertencias son las dos deprecaciones ya conocidas. No se ha conectado ni cambiado ninguna base.

## Riesgos que se reducen, riesgos que siguen abiertos

| Aspecto | Cambio 5R | Límite |
|---|---|---|
| Proyección SQL bootstrap | Ya no selecciona el ORM `Usuario` completo ni precarga `Rol` en el login SaaS | Sí necesita leer hash Argon2; no se definió cuenta SQL con permisos por columna |
| Datos HTTP/JWT | Solo usa `usuario_id` tras verificar contraseña, luego membresía tenant | El hash sigue temporalmente en memoria del backend; no hay certificación con datos reales |
| Errores del login | 401 genérico para credenciales o membresía; 503 para error SQL | Rate limiting, igualdad de tiempos de respuesta y resistencia a enumeración siguen pendientes |
| Backend heredado | `AuthService.autenticar_credenciales` y ORM legacy no modificados | El primer parcial no pasa automáticamente a RLS; `SAAS_AUTH_ENABLED=false` continúa |
| RLS en PostgreSQL | Ninguna policy/GRANT instalada | El bloqueo del bootstrap **NO se considera resuelto** para una conexión SQL no privilegiada |
| Identidad SQL | Se autentica en servicio Python y continúa validándose membresía por empresa | `app.empresa_id`/`app.usuario_id` son GUC modificables por SQL arbitrario; CALL directo con identidad ajena sigue pendiente |
| Procedimientos heredados | No se cambian | Los 4 legacy con EXECUTE PUBLIC siguen requiriendo revisión y autorización |

**Punto crítico:** el nuevo `SELECT` mínimo es solo una **reducción de proyección**, no una interfaz de autorización de la base. Si un atacante posee la credencial SQL bootstrap y esa credencial tiene `SELECT` general sobre `usuario.password_hash`, aún podría enumerar hashes. PostgreSQL debe imponer privilegios/restricciones propios; no considerar solucionada la exposición por este cambio Python.

## Pruebas y criterio de cierre

Desde `C:\SI2_Parcial1\fashionstore-api`, Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
python .\database\saas\23_validar_diseno_acceso_5q.py
```

**Si pytest falla, no continuar** y enviar salida completa. El validador 5Q debe mantener `8 recursos, 7 bloqueantes sin resolver` y `BORRADOR NO DESPLEGABLE`; ninguna mejora local del login autoriza cerrar esos 7 controles de aislamiento.

No hay scripts SQL/psql nuevos, ni contraseña PostgreSQL necesaria en 5R. No ejecutar `CALL`, migraciones, seed, `CREATE ROLE`, `CREATE POLICY`, `GRANT` o `REVOKE`.

**5R VALIDADA Y CERRADA solo como código de autenticación SaaS y pruebas unitarias**, tras recibir suite completa y validador; sin concluir que RLS esté implementado. El **Paso 5 sigue EN PROCESO (Bloque 3 4/7)**. No tocar `main`, Supabase, `fashionstore_saas_reservas_sandbox_5h` o base restaurada `fashionstore_restore_20261023_193554_594`. Mantener `SAAS_AUTH_ENABLED=false` y gate de rutas comerciales. Esperar `siguiente` para abrir 5S.


## Evidencia de cierre recibida — 24 de octubre de 2026

En PowerShell con Conda `fashionstore-api`, el estudiante realizó `git pull --ff-only origin feat/saas-multitenant-parcial2`: fast-forward **`a2fc698..fe19d4d`**, con los cambios de login, repositorio, servicio, documento y 16 pruebas nuevas. Ejecutó `python -m pytest -q tests_saas` y obtuvo **`486 passed, 2 warnings in 6.96s`**; **cero fallos**. Advertencias: `StarletteDeprecationWarning` por uso de httpx en testclient y por `HTTP_422_UNPROCESSABLE_ENTITY` deprecado.

Luego ejecutó `python .\\database\\saas\\23_validar_diseno_acceso_5q.py` con resultado literal:

```text
5Q VALIDACION ESTATICA OFFLINE — NO APLICA SQL NI ROLES
5Q MATRIZ CONSISTENTE: 8 recursos, 7 bloqueantes sin resolver
5Q BORRADOR NO DESPLEGABLE; RLS POSTGRESQL REAL NO VALIDADO
```

**Dictamen:** Se confirma la reducción de proyección del login SaaS, la verificación Argon2 en el backend y 16 pruebas unitarias adicionales, sin modificar el login legacy. **Los siete controles bloqueantes siguen ABIERTOS.** No se ha probado acceso SQL con un principal `NOSUPERUSER NOBYPASSRLS`, no se impusieron ACL por columna, policies RLS ni cierre de `EXECUTE PUBLIC`; un `SELECT` mínimo en Python no impide enumeración de hashes si la credencial SQL tiene privilegios amplios. El validador 5Q pasa precisamente porque el diseño sigue **no desplegable**, no porque la arquitectura esté autorizada para producción.

**5R CERRADA exclusivamente en alcance de código/pruebas locales; Paso 5 EN PROCESO, Bloque 3 4/7.** `SAAS_AUTH_ENABLED=false` y reservas, pagos y ventas SaaS aún bloqueados. En esta subfase no se ejecutaron mutaciones PostgreSQL ni Supabase. **Esperar instrucción `siguiente` para preparar 5S**, sin crear roles del clúster ni alterar la base histórica por defecto.
