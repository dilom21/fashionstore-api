# Bloque 3 / Paso 5K — Cuerpos de triggers y diseño de permisos RLS (SOLO LECTURA)

**Estado: SUBFASE 5K VALIDADA exclusivamente como tests estáticos, auditoría READ ONLY y análisis de cuerpos reales de triggers/permisos.** Paso 5 sigue EN PROCESO (Bloque 3 4/7). Rama exclusiva `feat/saas-multitenant-parcial2`.

**Prohibiciones 5K:** NO crear roles, usuarios, GRANT, REVOKE, CREATE POLICY, ENABLE/DISABLE RLS, migraciones, seed ficticio, SP ni invocaciones CALL. No tocar `main`, Supabase o la base restaurada `fashionstore_restore_20261023_193554_594`; preservar `SAAS_AUTH_ENABLED=false`. Toda comprobación SQL es en `127.0.0.1:5432 / fashionstore_saas_reservas_sandbox_5h`, exclusivamente READ ONLY y ROLLBACK.

## 1. Hallazgo validado en 5J que impide probar SP todavía

En el informe real de 5J, las **20 tablas examinadas tienen `relrowsecurity=t` pero `0 políticas`**; los objetos pertenecen a `postgres` con `rolsuper=t`, `rolbypassrls=t`. No hay roles de ensayo prefijo `fashionstore_5j_%`. En diez tablas consultadas no se encontraron concesiones a `PUBLIC`. Hay 26 tablas de columnas, 136 restricciones y diez triggers activos examinados. Estas cifras no cubren de forma automática las otras tablas ni sustituyen pruebas RLS reales.

**Consecuencia:** RLS deny-by-default bloqueará el acceso a filas para un rol sujeto a RLS sin políticas. Conceder `SELECT/UPDATE` en una tabla NO reemplaza una política; tampoco ejecutar como `postgres` demuestra aislamiento. Los SP nuevos fueron redactados como `SECURITY INVOKER`, por lo que necesitan una estrategia de permisos y RLS efectiva para invocar las tablas, triggers y Kardex.

## 2. Auditoría 5K de SOLO LECTURA preparada

Archivos:
- `database/saas/18_auditar_cuerpos_triggers_y_privilegios_sandbox.sql`: guardas de DB localhost exacta, **46 tablas, 48 FK tenant, cero políticas, cero SP nuevos y sin seed central**. Usa `BEGIN TRANSACTION READ ONLY` y `ROLLBACK`. Extrae a través de `pg_get_functiondef` los cuerpos originales de las funciones de trigger observadas en 5J, sin llamarlas. Revisa `prosecdef`, owner, `proconfig` y firma de cada rutina; `EXECUTE` a PUBLIC, ACL efectivos de roles existentes y secuencias/IDENTITY.
- `database/saas/18_auditar_cuerpos_triggers_y_privilegios_sandbox.ps1`: conecta solo al sandbox; `default_transaction_read_only=on`; exporta a `$env:TEMP\fashionstore_5k_triggers_privilegios_sandbox_LOCAL_REVISAR.txt`, no a GitHub; si el archivo ya existe, no lo sobrescribe.
- `tests_saas/test_trigger_privilegios_preflight_5k.py`: pruebas estáticas nuevas, que **no ejecutan** SQL ni crean roles.

El resultado de `pg_get_functiondef` puede mostrar SQL, literales o configuraciones sensibles: el estudiante debe revisar y, de ser necesario, ocultar secretos al adjuntar el archivo al chat. No publicar el dump ni el informe en el repositorio.

**Verificaciones específicas sobre triggers antes del seed:**
1. `fn_normalizar_correo_usuario`: revisar dependencias y efecto sobre identidad y unicidad.
2. `fn_registrar_bitacora`: inspeccionar `empresa_id`/actor, tabla `bitacora`, consultas del trigger, privilegios y si el INSERT de catálogo/inventario/usuario podría fallar por auditoría sin contexto.
3. `fn_actualizar_actividad_carrito`: confirmar cómo toca `carrito` al insertar/modificar detalle; impacto en orden de locks CU15/CU16.
4. `fn_validar_sucursal_detalle_carrito`, `fn_validar_detalle_reserva_sucursal`: verificar tenant + sucursal, no solo id.
5. `fn_controlar_actualizacion_inventario`: confirmar preservación de `stock_actual`, cambios permitidos a `stock_reservado` y reglas de Kardex.
6. Comprobar `SECURITY DEFINER`/INVOCER y `search_path` de cada función, concedentes `EXECUTE` sobre rutinas y secuencias para roles de aplicación. Una función SECURITY DEFINER propiedad de `postgres` puede saltarse RLS y requiere auditoría antes de confiar en ella.

## 3. Propuesta de modelo de confianza y permisos (NO SQL ejecutable)

La aplicación valida firma, vigencia y scope del JWT; resuelve `usuario_empresa`, rol, empresa, plan y suscripción con **consultas acotadas por tenant**. `p_empresa_id` y `p_usuario_id` de futuros SP deben provenir solo de ese contexto del servidor, nunca de body/query controlados por usuario.

**Dos modelos por evaluar antes de escribir políticas:**
- **Modelo A (preferible para ensayo de defensa en profundidad):** credencial SQL única, restringida, **sin superusuario, `BYPASSRLS`, propiedad de tablas ni `CREATEROLE/CREATEDB`**, usada solo por API de confianza. La API asigna un contexto de empresa por transacción y valida membresía previamente. RLS puede imponer una segunda comprobación por filas, PERO `current_setting('app.empresa_id')` es configurable por la misma sesión y **no constituye identidad criptográfica por sí solo**. Solo es una barrera adicional frente a errores de filtrado si la aplicación y su SQL son confiables; SQL injection/credenciales comprometidas pueden falsearlo. Antes de adoptar el modelo hay que documentar amenazas y asegurar que clientes NO accedan directamente a SQL o a la credencial.
- **Modelo B (aislamiento SQL más fuerte a estudiar):** identidades PostgreSQL restringidas distintas por tenant o contexto de conexión con autenticación independiente, y políticas basadas en `current_user` y una asignación protegida. Implica complejidad de login/rotación, roles globales y pool de conexiones. No es una migración preparada todavía.

**No dar por segura una política que solo compara dos parámetros enviados por el cliente**, ni funciones SECURITY DEFINER propiedad de superusuario sin revisar todo su cuerpo.

### Matriz mínima propuesta de privilegios y políticas para futuro laboratorio

| Recurso | SELECT (sujeto a políticas) | INSERT/UPDATE | DELETE | Alcance a verificar |
|---|---|---|---|---|
| `empresa`, `plan_saas`, `suscripcion` | Solo empresa y plan autorizado | No a rol cliente/operador de ensayo | No | Suscripción vigente; evitar fuga de otros tenants |
| `usuario`, `usuario_empresa`, `rol`, `rol_funcion` | Solo identidad/rol asociado o consulta de autorización segura | No salvo flujo administrador separado | No | Evitar recursividad al verificar membresía |
| `cliente`, `empleado`, `sucursal` | Cliente propio o empleado asignado | Ninguna mutación directa para rol de reserva | No | `empresa_id`, `usuario_id`, `sucursal_id` coherentes |
| `carrito`, `detalle_carrito` | Solo carrito propio del tenant | CU15 únicamente bajo reglas del backend | Soft delete carrito, no DELETE físico directo | `USING`, `WITH CHECK`, locks y filas del cliente |
| `inventario` | Stock de sucursal empresarial | Reservar/liberar solamente en transacción autorizada | No | `stock_reservado <= stock_actual`, Kardex, no cruces |
| `reserva`, `detalle_reserva`, `movimiento_inventario` | Solo empresa, propietario o personal autorizado | CU16/CU17/CU18 transaccionales, sin escrituras directas por HTTP | No | No repetir liberación, Kardex empresarial |

**Nota:** la tabla anterior expresa política deseada, no permisos que ya existan. Un SP `SECURITY INVOKER` exige permisos del invocador para sus `SELECT/INSERT/UPDATE`; es necesario decidir si se otorgarán a un rol de servicio protegido o si se diseñará un camino DB de privilegio controlado. **No otorgar `INSERT` directo a clientes finales** solo para hacer funcionar el SP.

## 4. Matriz de pruebas RLS y transacciones FUTURAS, no implementadas hoy

Casos que deberán ejecutarse tras autorizar rol/políticas/seed ficticio exclusivamente en sandbox:
- Rol SQL de ensayo sin `rolsuper` ni `rolbypassrls`: `SELECT current_user`, `has_table_privilege`, `row_security_active` y pruebas de acceso real.
- Dos empresas inventadas A/B, usuario A, usuario B, empleado A, cajero A, miembro desactivado, suscripción vencida y un carrito reservado por empresa.
- SELECT propio permitido/ajeno oculto; `INSERT ... empresa_id=B` desde A rechazado; UPDATE/DELETE ajenos rechazados o sin filas; `WITH CHECK` en cambios de tenant.
- Carrito propio ACTIVE/EXPIRED, stock suficiente/insuficiente, reserva duplicada, cancelación repetida, finalización sin compra solo CONFIRMADA, venta vinculada bloqueada, seguimiento Kardex.
- Dos carritos simultáneos para mismo inventario, fallo en segundo ítem y rollback atómico, sin negativo ni aumento de `stock_actual`.
- Trigger `fn_actualizar_actividad_carrito` concurrente y locks carrito-inventario; garantía de orden y sin deadlocks en tests PG.

**No hay credenciales ni seed** definidos para los ensayos en esta subfase. El rol PostgreSQL sería objeto GLOBAL del clúster y, si llegara a crearse con autorización expresa, requeriría `NOSUPERUSER`, `NOBYPASSRLS`, `NOCREATEDB` y `NOCREATEROLE` entre otros límites. Ninguna creación de rol se aprobará implícitamente por superar pytest.

## 5. Comandos SOLO de lectura para el estudiante

Desde PowerShell, `C:\SI2_Parcial1\fashionstore-api`, con Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Resultado observado en Windows: `384 passed, 2 warnings in 6.05s`, cero fallos** = 370 anteriores + 14 verificaciones estáticas de 5K.

Solo si pytest pasa:

```powershell
.\database\saas\18_auditar_cuerpos_triggers_y_privilegios_sandbox.ps1
```

Debe reportar `5K PREFLIGHT OK`, terminar en `ROLLBACK` y guardar el informe en TEMP local. Enviar salida de PowerShell y el archivo `fashionstore_5k_triggers_privilegios_sandbox_LOCAL_REVISAR.txt` revisado por secretos al chat, NUNCA a GitHub.

No ejecutar `CREATE ROLE`, `GRANT`, `CREATE POLICY`, `INSERT`, `CALL`, ni `sp_*_tenant.sql.txt`. No es necesario activar ningún feature flag.

## 6. Criterio de cierre de 5K

**5K VALIDADA dentro del alcance estático/read-only y con informe SQL analizado**: recibidos `384 passed`, auditoría 5K READ ONLY completada y cuerpos originales examinados. El Paso 5 sigue EN PROCESO, Bloque 3 4/7. No ejecutar cambios en el sandbox hasta autorización explícita en una subfase posterior; esperar `siguiente`.

## 7. RESULTADO REAL 5K: análisis de la auditoría íntegra (sin escrituras)

Se recibió salida de Windows después de fast-forward de `8844e47` a `89dfea6` y `python -m pytest -q tests_saas`: **`384 passed, 2 warnings in 6.05s`**, cero fallos. Las dos advertencias conocidas son Starlette/httpx y el nombre de `HTTP_422_UNPROCESSABLE_ENTITY`.

El estudiante ejecutó `18_auditar_cuerpos_triggers_y_privilegios_sandbox.ps1` EXCLUSIVAMENTE en `127.0.0.1:5432 / fashionstore_saas_reservas_sandbox_5h`. El SQL informó `5K PREFLIGHT OK: sandbox, 46 tablas, 48 FK, 0 policies, 0 SP tenant, sin seed principal`; la auditoría terminó con `ROLLBACK`, `AUDITORIA 5K SANDBOX READ ONLY COMPLETADA` y un reporte TEMP revisado y adjuntado por el estudiante. No se ejecutó ninguna función de trigger ni se instaló SP.

### 7.1 Resultado de las seis funciones reales referenciadas por diez triggers

- `fn_registrar_bitacora()` (5 triggers sobre `cliente`, `inventario`, `reserva`, `sucursal`, `usuario`): **SECURITY INVOKER**, `search_path=public, pg_temp`, propietario `postgres`. Recoge `app.usuario_id` desde `current_setting(...,TRUE)` y toma `empresa_id` del JSON de `NEW/OLD`; escribe `public.bitacora` en el mismo flujo. **Riesgo pendiente:** el parámetro `app.usuario_id` es modificable por sesiones SQL; su valor no prueba identidad ni pertenencia a empresa. El rol real necesitará permisos/política compatibles para escribir `bitacora`; inspeccionar su RLS y grants específicos. En tablas de identidad global `usuario`, `empresa_id` puede quedar NULL, situación esperada por el cuerpo.
- `fn_actualizar_actividad_carrito()`: **SECURITY INVOKER**, **sin `SET search_path` propio**, propietario `postgres`. Tras INSERT/UPDATE/DELETE en `detalle_carrito` ejecuta `UPDATE public.carrito SET fecha_actualizacion=NOW() WHERE id=v_carrito_id`. La modificación lateral exige UPDATE permitido a `carrito` según rol/policy y altera el **orden efectivo de bloqueos**; ensayar RLS y deadlocks con CU15 y CU16 antes de operar.
- `fn_validar_sucursal_detalle_carrito()`: **SECURITY INVOKER**, **sin `SET search_path` propio** (cuerpo referencia `public.carrito` y `public.inventario`). Verifica que exista carrito e inventario y que `sucursal_id` coincida, pero NO valida por sí sola `empresa_id`. Las FK compuestas tenant presentes complementan esta protección; no asumir que el trigger por sí solo garantiza aislamiento.
- `fn_validar_detalle_reserva_sucursal()`: **SECURITY INVOKER**, `search_path=public, pg_temp`. Compara `sucursal_id` de reserva/inventario sin revisar expresamente tenant. Las FK compuestas `(empresa_id, id)` cubren integridad de referencias, no SELECT permitido al rol.
- `fn_controlar_actualizacion_inventario()`: **SECURITY INVOKER**, `search_path=public, pg_temp`. Prohíbe alterar `sucursal_id`, `variante_producto_id` y `temporada_id`; actualiza timestamp si cambia `stock_actual` o `stock_reservado`. **No prohíbe explícitamente cambiar `empresa_id`**, ni valida por sí sola autorización o incremento/reserva legítimos; CHECK y RLS/grants/negocio son controles independientes.
- `fn_normalizar_correo_usuario()`: **SECURITY INVOKER**, `search_path=public, pg_temp`; obliga correo no vacío y normaliza usando `LOWER(BTRIM(...))`. Con `usuario.correo` UNIQUE, los correos ficticios deben ser únicos incluso tras normalización.

**Las seis funciones tienen `prosecdef=f` y owner `postgres`; ninguna de estas seis es `SECURITY DEFINER`.** Los dos triggers sin `search_path` fijado son los de actividad del carrito y validación de sucursal de detalle de carrito. No ejecutar ni modificar cuerpos aún.

### 7.2 Privilegios efectivos y riesgo de rutinas heredadas

- Sección `3_ROUTINE_EXECUTE_PUBLIC`: **10/10 rutinas examinadas con `execute_public=t`**, de las cuales son las 6 funciones de trigger y **4 procedimientos antiguos**: `sp_cancelar_reserva`, `sp_crear_reserva`, `sp_crear_reserva_desde_carrito` y `sp_finalizar_reserva_sin_compra`. Todos figuran con `prosecdef=f` y propietario `postgres`. **Esto habilita el privilegio EXECUTE a PUBLIC, pero NO acredita acceso a tablas/RLS ni ejecución exitosa desde roles limitados**. Antes de permitir conexiones SQL del backend se requerirá revisar y restringir la invocación de rutinas heredadas en el sandbox, con una estrategia validada y sin tocar otras bases.
- Sección `4_ROLES_WITH_ACCESS`: solo `postgres` coincidió con roles solicitados y tiene `rolcanlogin=t`, `rolsuper=t`, `rolbypassrls=t` y todos los permisos consultados. No hay una sesión de aplicación sin `BYPASSRLS` probada.
- Sección `5_ID_SEQUENCES`: **14 secuencias** de identidad catalogadas, todas propiedad de `postgres`, ACL mostrada `<DEFAULT>`; habrá que verificar uso de IDENTITY y grants mínimos en futuras pruebas. No interpretar automáticamente `<DEFAULT>` como permisos amplios.
- Sección `6_SECURITY_FUNCTIONS`: `fn_completar_ip_bitacora()`, `fn_controlar_actualizacion_inventario()`, `fn_registrar_bitacora()` y `sp_ajustar_inventario(...)` tienen `prosecdef=f` y `search_path=public, pg_temp`; el informe muestra sus metadatos, **no cuerpo de `sp_ajustar_inventario` ni de `fn_completar_ip_bitacora`**.

### 7.3 Bloqueos de seguridad y acciones de una subfase POSTERIOR

1. Auditar `public.bitacora`: política RLS, grants INSERT/SELECT, trigger auxiliar `fn_completar_ip_bitacora`, campos obligatorios y si un rol de reserva puede mantener auditoría sin permisos amplios de lectura.
2. Diseñar aislamiento tenant con contexto confiable desde la API y políticas `USING`/`WITH CHECK` sobre las tablas necesarias. `app.usuario_id`/`app.empresa_id` definidos por la conexión son datos no autenticados de por sí; evitar depender únicamente de GUC configurables por usuario.
3. Diseñar revocación controlada de `EXECUTE` sobre cuatro SP heredados a `PUBLIC` sin afectar la aplicación legacy; controlar concesiones a rutinas de trigger, RLS y secuencias por rol SQL limitado.
4. Planificar los permisos transaccionales de `SECURITY INVOKER` teniendo en cuenta el `UPDATE carrito` desde trigger y `INSERT bitacora`; revisar riesgos de deadlock con triggers completos antes de probar stock real.
5. No crear `CREATE ROLE` ni `CREATE POLICY` ni seeds hasta autorización expresa. `CREATE ROLE` es global al clúster y **no** se puede deshacer con una simple transacción de sandbox. Preservar la base restaurada, `main` y Supabase.

**CIERRE 5K: VALIDADA** solo en su alcance estático, lectura y dictamen del SQL existente. **No están validados los permisos efectivos de rol restringido, RLS multitenant aplicado o seguridad de CALL/stock real**. `SAAS_AUTH_ENABLED=false` y endpoints transaccionales bloqueados. **Paso 5 EN PROCESO; Bloque 3 sigue 4/7. Esperar `siguiente`.**
