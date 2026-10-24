# Bloque 3 / Paso 5M — Contrato de identidad y diseño de políticas RLS, **NO EJECUTABLE**

**Estado: SUBFASE 5M VALIDADA solo como diseño y revisión estática del contrato de identidad/RLS; ninguna política ni rol aplicado en PostgreSQL.** Rama `feat/saas-multitenant-parcial2`. Paso 5 de operaciones comerciales sigue EN PROCESO, Bloque 3 4/7.

**Prohibido en 5M:** `CREATE ROLE`, `CREATE POLICY`, `GRANT`, `REVOKE`, `ALTER TABLE`, seed, `CALL` o ejecución de tres borradores `sp_*_tenant.sql.txt`; NO modificar BD restaurada, sandbox, Supabase, `main` ni `SAAS_AUTH_ENABLED=false`.

## 1. Hechos ya probados por 5I–5L

- Sandbox separado `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h`: 46 tablas vacías, 48 FK tenant validadas, sin SP nuevos ni datos históricos; comprobación con `postgres` superusuario.
- 5J: **20 tablas examinadas con RLS habilitado, 0 políticas**; 5L: `bitacora` también tiene RLS habilitado y 0 políticas; la bitácora carece de grants a `PUBLIC` y tiene `empresa_id` y `usuario_id` opcionales con FK independientes.
- 5K: `EXECUTE` a `PUBLIC` en 4 procedimientos de reserva antiguos y 6 funciones de trigger examinadas; 5L: `fn_registrar_bitacora` lee GUC `app.usuario_id`, `fn_completar_ip_bitacora` lee `app.ip`. Son funciones `SECURITY INVOKER` y no autentican estos parámetros.
- Resultado local previo confirmado: **400 passed, 2 warnings in 6.48s**; los tests son mayoritariamente estáticos/SQLite y **no prueban aislamiento RLS en PostgreSQL**.

Según la documentación oficial PostgreSQL 18, con RLS activo y ninguna política aplicable se aplica **denegación por defecto** a roles sujetos a RLS, mientras `BYPASSRLS`, superusuario y normalmente propietario se exceptúan. `CREATE ROLE` afecta a TODO el clúster. `SECURITY INVOKER` usa los privilegios del llamador. Referencias: [PostgreSQL — RLS](https://www.postgresql.org/docs/18/ddl-rowsecurity.html), [Roles](https://www.postgresql.org/docs/18/database-roles.html), [Funciones](https://www.postgresql.org/docs/18/sql-createfunction.html).

## 2. Hallazgos concretos del código existente que BLOQUEAN las políticas ejecutables

1. **Bootstrap antes del tenant:** `app/modules/autenticacion_seguridad/api/tenant_router.py` llama primero a `AuthService.autenticar_credenciales`, luego a `TenantAuthRepository.obtener_membresia_vigente`. El repositorio lee `usuario`, `usuario_empresa`, `empresa`, `rol`, `suscripcion`, `plan_saas` y `cliente`/`empleado`/`sucursal`. Estas consultas ocurren **ANTES** de que `get_current_tenant_context` establezca `app.empresa_id`. Políticas `empresa_id = current_setting(...)` estrictas sobre todas estas tablas impedirían el login. No permitir `USING(TRUE)` público como solución.
2. **Contexto de actor incompleto:** `app/core/tenant_dependencies.py` establece `db.info["empresa_id"]` y `set_config('app.empresa_id', ..., true)` tras validar JWT+membresía; `app/core/database.py` reaplica por transacción `app.empresa_id` e IP. **No reaplica centralmente `app.usuario_id` del tenant**: algunos servicios lo establecen individualmente. Auditoría no puede asumir actor fiable o presente.
3. **Argumentos de SP no enlazados al llamador SQL:** los tres borradores 5G reciben `p_empresa_id` y `p_usuario_id`, verifican membresía/roles por esos valores, pero **no demuestran que quien invoca sea el usuario indicado**. Si una futura credencial SQL tiene acceso y puede llamar al SP con IDs ajenos válidos, la mera comprobación de membresía podría autorizar el actor suplantado. Los parámetros GUC también son modificables por el usuario SQL: compararlos con un GUC **NO repara por sí solo** la suplantación.
4. **Auditoría, locks y RLS indirectos:** `fn_registrar_bitacora` necesita insertar en `bitacora`; `fn_actualizar_actividad_carrito` actualiza `carrito` desde el trigger de detalle; `fn_validar_sucursal_detalle_carrito` y `fn_validar_detalle_reserva_sucursal` requieren SELECT sobre referencias; FK tenant no garantiza SELECT. Sin grants/policies correctos, los SP `SECURITY INVOKER` pueden fallar en pasos intermedios y necesitan prueba de rollback.
5. **Ejecución legacy expuesta:** cuatro SP antiguos conservan `EXECUTE PUBLIC`. El rol API nuevo no debería poder llamar rutas legacy; esto exige un plan de privilegios y de compatibilidad con el primer parcial antes de revocar EXECUTE en el sandbox.

## 3. Modelo de confianza propuesto para un laboratorio futuro (NO aprobado aún)

**Etapa A: autenticación controlada.** El cliente únicamente habla HTTP con FastAPI. El servidor verifica las credenciales o JWT firmado y su vigencia; posteriormente consulta, de manera controlada, la membresía en tenant, estado del usuario, plan, rol, suscripción y contexto `cliente`/`personal`. Debe haber **un camino de bootstrap de identidad** que pueda leer lo imprescindible ANTES de conocer `empresa_id`. Candidatos para comparar en laboratorio:
- Función de autenticación de superficie mínima, revisada, en esquema protegido, con `SECURITY DEFINER` propiedad de una cuenta dedicada **NO superusuario** y `search_path` fijo; no retornar hash de contraseña ni membresías de otros tenants; revisar RLS y riesgo de recursion dentro de la función y evitar ejecución arbitraria por clientes SQL.
- Conexión de autenticación separada y restringida a sentencias/campos mínimos, sin acceso comercial, usada solamente por backend; no permitir que el rol que atiende acciones comerciales use automáticamente esos privilegios.

**Etapa B: operaciones empresariales confiables.** La aplicación usa un rol SQL de servicio `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`, no propietario de tablas, sin pertenencia heredada a roles potentes. El servidor aplica a cada transacción un tenant y actor **derivados de JWT+membresía**, no del cuerpo HTTP; fija `app.empresa_id`, `app.usuario_id` y, si se necesita, `app.ip` con `set_config(..., true)`; utiliza parámetros SQL reales y filtra por empresa y propiedad en **cada consulta**.

**LÍMITE crítico:** esos valores GUC son parámetros de sesión/transaction mutables por SQL arbitrario. El diseño anterior usa RLS como **defensa en profundidad contra errores accidentales de filtrado de un backend confiable**, no como barrera criptográfica contra SQL injection ni contra un usuario con credencial SQL robada. Para resistir SQL arbitrario habrá que usar un principal SQL independiente por tenant/autenticación o una comprobación SQL autenticada cuya prueba no sea falsificable por ese mismo principal. **La decisión de confianza debe documentarse ANTES de crear políticas reales.**

**Etapa C: permisos:** por defecto denegar todo. Solo después de definir principal/contrato se otorgará `CONNECT` a la base de ensayo, `USAGE` al esquema y los privilegios estrictamente necesarios sobre las tablas/rutinas. `CREATE ROLE` es global al clúster local; no crearlo sin autorización expresa. **No otorgar privilegios en la fuente restaurada**.

## 4. Matriz de políticas por tabla — DISEÑO, sin CREATE POLICY

| Tablas / dominio | Lectura pretendida | Escrituras pretendidas | Depende de |
|---|---|---|---|
| `usuario`, `usuario_empresa`, `rol`, `rol_funcion`, `funcion`, `accion` | Bootstrap limitado o identidad propia validada | Ninguna mediante reserva | Resolver autenticación sin recursión de policies sobre `usuario_empresa` |
| `empresa`, `suscripcion`, `plan_saas` | Empresa activa y plan vinculados al contexto autenticado | No mediante CU16–CU18 | Plan vigente y capacidades por tenant |
| `cliente`, `empleado`, `sucursal` | Cliente propio, personal/empresa/sucursal asignados | No mediante reserva | FK tenant, `usuario_id` autenticado |
| `categoria`, `producto`, `variante_producto`, `temporada`, `talla`, `color` | Catálogo del tenant y referencias globales permitidas | Ninguna en CU16 | Evitar mostrar productos B a A |
| `carrito`, `detalle_carrito` | Carrito propio y detalles del mismo tenant | Conversión de carrito y CU15 solo según servicio | `USING` en UPDATE y `WITH CHECK` en INSERT/UPDATE; actividad trigger |
| `inventario`, `movimiento_inventario` | Inventario autorizado de sucursal y Kardex asociado | `UPDATE stock_reservado` e `INSERT movimiento` solo en transacción autorizada | `CHECK` stock, FK empresa/linea, triggers, audit |
| `reserva`, `detalle_reserva`, `venta` | Reservas permitidas, venta asociada consultable solo en tenant | `INSERT reserva/detalle`, `UPDATE reserva`; ninguna escritura de venta en CU16 | Transacción, no venta vinculada, restricciones de estado |
| `bitacora` | Sin SELECT para rol operacional, solo auditor autorizado | `INSERT` de evento desde operación autenticada | Política `WITH CHECK`, evento global `empresa_id NULL`, cadena de actor/IP |

`USING` controla filas visibles/actualizables y `WITH CHECK` nuevas filas o cambios. `WITH CHECK` no sustituye permisos SQL; `SELECT` puede requerirse adicionalmente al actualizar o mediante triggers. **No redactar una policy ejecutable basada únicamente en `current_setting('app.empresa_id')` sin resolver la identidad.**

## 5. Bloqueos obligatorios ANTES de instalar SQL en sandbox

1. Elegir explícitamente modelo de confianza (backend HTTP de confianza contra errores, o resistencia a SQL arbitrario) y un mecanismo de bootstrap de login no recursivo.
2. Vincular identidad del invocador con `p_usuario_id`/`p_empresa_id` de los 3 SP; prohibir `CALL` directo por cliente final y `EXECUTE PUBLIC`. Revisar la interacción `SECURITY INVOKER` con `bitacora` y Kardex.
3. Diseñar policies `USING/WITH CHECK` por tabla, roles, comando y contexto; evitar combinaciones `PERMISSIVE OR` que amplíen acceso. Aclarar tratamiento de `usuario` y `bitacora` globales.
4. Preparar un ensayo con cuenta NO `BYPASSRLS` no propietaria de tablas y 2 tenants totalmente ficticios en sandbox, comprobando lecturas correctas, cruces A/B, permisos, triggers, IP/actor y rollback de stock.
5. Verificar roles existentes en cluster y aprobaciones para DDL: roles tienen alcance global. Proteger las cuatro rutinas legacy accesibles a PUBLIC sin afectar `main` ni la BD histórica.

## 6. Revisor estático preparado para 5M

`database/saas/20_revision_estatica_contrato_rls_5m.py` analiza **solo archivos fuente**: flag inactivo, dependencia de JWT+membresía, GUC empresa, listener de transacciones, bloqueo de rutas y borradores `SECURITY INVOKER`; muestra los contratos de seguridad aún pendientes. **No importa FastAPI ni SQLAlchemy, no conecta a BD, no escribe archivos ni lee `.env`.** Una salida con `GUARDA OK` prueba consistencia estática únicamente; **NO certifica RLS**. Los riesgos señalados requieren diseño y pruebas PostgreSQL futuras.

Pruebas adicionales: `tests_saas/test_contrato_confianza_rls_5m.py`, **12 nuevos casos estáticos**; suite completa confirmada en Windows: **`412 passed, 2 warnings in 8.81s`** (400 anteriores + 12 nuevos), cero fallos.

Comandos seguros para el estudiante desde `C:\SI2_Parcial1\fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
python .\database\saas\20_revision_estatica_contrato_rls_5m.py
```

**Si pytest falla, detenerse** y compartir error; no ejecutar pasos posteriores. El revisor solo imprime hallazgos, no usa clave PostgreSQL y no requiere instalar nada.

## 7. Estado de cierre

**5M CERRADA en su alcance documental y estático** tras `412 passed, 2 warnings in 8.81s` y ejecución satisfactoria del revisor (5 guardas OK; 5 bloqueantes de diseño detectados intencionalmente). No se han implementado ni probado policies, GRANT, revocaciones, SP ni cuentas SQL restringidas. No modificar `main`, Supabase ni la base restaurada; sandbox sigue sin seed ni SP tenant; `SAAS_AUTH_ENABLED=false`. Paso 5 en proceso (Bloque 3 4/7). Detenerse hasta recibir el resultado y después esperar `siguiente`.


## 8. Evidencia real de cierre 5M recibida en PowerShell

El estudiante sincronizó `feat/saas-multitenant-parcial2` al commit `68a0621` y ejecutó `python -m pytest -q tests_saas`: **`412 passed, 2 warnings in 8.81s`**, cero fallos. Las dos advertencias son las ya conocidas de deprecaciones Starlette/httpx y `HTTP_422_UNPROCESSABLE_ENTITY`.

Ejecutó `python .\\database\\saas\\20_revision_estatica_contrato_rls_5m.py`. La herramienta informó **cinco `GUARDA OK`**: (1) flag SaaS inactivo por defecto, (2) login JWT y comprobación de membresía, (3) contexto empresa establecido solo después de membresía, (4) rutas transaccionales de reserva bloqueadas y (5) tres procedimientos nuevos aún como borradores `SECURITY INVOKER`.

También informó **cinco `BLOQUEANTE DE DISENO`** (diagnósticos esperados, NO fallos de pytest ni excepciones):

1. `bootstrap_login_sin_politicas_definidas`: autorización inicial debe acceder a identidad/membresía antes del tenant.
2. `actor_auditoria_no_automatizado_en_dependencia_saas`: dependencia de tenant todavía no automatiza `app.usuario_id`.
3. `actor_no_reaplicado_automaticamente_tras_nueva_transaccion`: listener de SQLAlchemy no repone actor al abrir otra transacción.
4. `args_sp_no_vinculados_al_principal_sql_autenticado`: argumentos `p_usuario_id`/`p_empresa_id` de SP no demuestran el principal autenticado por sí mismos.
5. `guc_empresa_no_es_identidad_independiente`: `app.empresa_id` no puede tratarse como autenticación contra SQL arbitrario.

La salida terminó exactamente con `5M ANALISIS DE CODIGO COMPLETADO; RLS REAL TODAVIA NO VALIDADO`. **El revisor no resuelve los cinco bloqueantes**, solo los deja documentados y verifica los cierres vigentes. **No se ejecutó SQL ni se creó/modificó ningún rol, GRANT, policy, seed o SP en el sandbox**; `SAAS_AUTH_ENABLED=false` y endpoints de reservas siguen bloqueados. Los pasos futuros deben resolver el modelo de confianza y ensayar posteriormente RLS con una identidad SQL no privilegiada y datos ficticios, solo tras nueva autorización.

**Decisión:** cerrar 5M como revisión estática y diseño; **Paso 5 comercial EN PROCESO, Bloque 3 en 4/7 pasos**. Mantener `main`, Supabase y base restaurada sin cambios. Esperar que el estudiante diga `siguiente` antes de avanzar a la próxima subfase.
