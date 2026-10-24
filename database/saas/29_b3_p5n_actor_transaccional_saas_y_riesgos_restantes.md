# Bloque 3 / Paso 5N — Actor de auditoría SaaS entre transacciones (cambio acotado)

**Estado: IMPLEMENTADO EN CÓDIGO DE RAMA FEATURE, PENDIENTE DE PRUEBAS EN WINDOWS.** Paso comercial 5/7 sigue EN PROCESO; Bloque 3 sigue **4/7**. No habilitar rutas de reserva/pago/venta ni ejecutar procedimientos SQL tenant.

## 1. Qué se verificó hasta 5M

5L confirmó **400 passed** y que `fn_registrar_bitacora()` usa `current_setting('app.usuario_id',true)`; `fn_completar_ip_bitacora()` lee `current_setting('app.ip',true)`. Ambas funciones son `SECURITY INVOKER`. La bitácora está sujeta a RLS sin políticas. 5M confirmó **`412 passed, 2 warnings in 8.81s`** y cinco guardas estáticas `OK`, pero detectó cinco obstáculos de diseño, sin validar RLS con usuario restringido.

**Limitación esencial:** un GUC `app.usuario_id` o `app.empresa_id` puede cambiarse mediante SQL. Su presencia en el trigger ayuda al registro de auditoría de una API **de confianza**, pero no es prueba autónoma de la identidad, no resiste por sí sola una credencial SQL comprometida y no autoriza `CALL` con IDs arbitrarios.

## 2. Cambios implementados (solo Python de la rama SaaS)

1. `app/core/tenant_dependencies.py`: cuando `get_current_tenant_context` ha validado JWT **y membresía vigente**, establece para la transacción **primero** `set_config('app.usuario_id', usuario_id, true)` y luego `set_config('app.empresa_id', empresa_id, true)`, con parámetros enlazados SQLAlchemy. Al completarse ambas operaciones, copia **`usuario_id` y `empresa_id`** a `Session.info` para futuras transacciones de esa sesión. Si alguna operación falla, no persiste un estado parcial en `Session.info`. Nunca toma los IDs directamente del body del request. El uso de `true` hace al GUC **local a la transacción**; no sobrevive a COMMIT/ROLLBACK de esa conexión de pool.
2. `app/core/database.py`: el listener SQLAlchemy `after_begin` sigue aplicando `app.ip` y `app.empresa_id`, y ahora **también reaplica `app.usuario_id` desde `Session.info`** si esa clave está presente. Solo la ruta SaaS validada la introduce en `Session.info`; servicios legacy sin esa clave conservan su comportamiento, incluyendo las rutinas históricas que establecen el actor por cuenta propia.
3. `tests_saas/test_contexto_actor_tenant_5n.py`: **12 nuevas pruebas unitarias** usando sesiones/conexiones falsas, sin conectar a Postgres/Supabase. Cubren orden de validación, token ausente/inválido, membresía revocada, flag SaaS inactivo, fallo del primer o segundo `set_config`, reentrada de `after_begin`, ausencia de actor sin identidad y separación de sesiones.

**No se modificaron** `app/core/tenant_context.py`, API de login, contratos `sp_*_tenant.sql.txt`, rutas de autorización, archivos de base de datos, feature flag o políticas RLS. Tampoco hay credenciales nuevas.

## 3. Resultado esperado y límites de certificación

Suite anterior confirmada: **412 passed** (5M). Casos nuevos: **12**. Total **esperado**: **`424 passed`**, todavía pendiente del estudiante en Windows.

Las pruebas 5N ejercitan lógica de Python con sesiones falsas. No validan que `bitacora` pueda insertar con un principal RLS real, ni comprueban reuso del pool PostgreSQL, políticas, concurrencia o el aislamiento multitenant. Sigue siendo imprescindible realizar ensayos con roles no privilegiados y dos empresas **completamente ficticias** antes de considerar rutas transaccionales.

## 4. Estado de los cinco problemas identificados en 5M

| Diagnóstico original 5M | Resultado en 5N | Sigue pendiente |
|---|---|---|
| `bootstrap_login_sin_politicas_definidas` | Sin cambio | Sí; diseño de acceso inicial sin abrir todas las filas |
| `actor_auditoria_no_automatizado_en_dependencia_saas` | **Cambio en código** para aplicar actor después de validar | Confirmación mediante pytest y posteriormente PostgreSQL |
| `actor_no_reaplicado_automaticamente_tras_nueva_transaccion` | **Cambio en código** al listener `after_begin` | Confirmación mediante pytest y posteriormente PostgreSQL |
| `args_sp_no_vinculados_al_principal_sql_autenticado` | Sin cambio; SP son borradores | Sí; posible suplantación con SQL arbitrario |
| `guc_empresa_no_es_identidad_independiente` | Sigue siendo cierto | Sí; elegir explícitamente modelo de amenaza y principal DB |

**No convertir el hecho de fijar GUCs en autorización SQL**. Una API con credencial técnica compartida debe validar el token y membresía en el servidor y filtrar todas sus consultas; las policies basadas en GUC se consideran solo defensa adicional frente a errores de filtro, no aislamiento frente a SQL arbitrario.

## 5. Pruebas propuestas al estudiante

En PowerShell, desde `C:\SI2_Parcial1\fashionstore-api`, con Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
python .\database\saas\20_revision_estatica_contrato_rls_5m.py
```

El último comando **no requiere base de datos ni contraseña**: al detectar los cambios 5N, debería mostrar los dos diagnósticos de actor como `REVISAR CAMBIO`, en vez de `BLOQUEANTE DE DISENO`. Los otros tres bloqueos seguirán presentes. La salida aún debe terminar `5M ANALISIS DE CODIGO COMPLETADO; RLS REAL TODAVIA NO VALIDADO`. Este revisor histórico solo informa sobre el código, **NO** es validador de RLS.

Si pytest falla, no ejecutar más comandos; compartir salida completa. No ejecutar `psql` ni scripts de seed o privilegios en 5N.

## 6. Límites operativos y cierre

- Rama permitida: `feat/saas-multitenant-parcial2`, exclusivamente; **no tocar `main`**.
- BD local restaurada `fashionstore_restore_20261023_193554_594`: lectura únicamente; BD sandbox `fashionstore_saas_reservas_sandbox_5h`: **sin modificaciones en 5N**.
- **No modificar Supabase**, no habilitar `SAAS_AUTH_ENABLED=false`, no instalar `sp_*_tenant`, crear roles globales, policies, grants, seed ni ejecutar operaciones comerciales reales.
- El trabajo 5N se podrá marcar como validado **solo en su alcance de código/unit tests** tras `424 passed` local y revisión de salida estática. Paso 5 comercial sigue EN PROCESO y Bloque 3 4/7. Esperar `siguiente` antes de iniciar otra subfase.
