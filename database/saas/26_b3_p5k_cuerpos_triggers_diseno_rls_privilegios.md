# Bloque 3 / Paso 5K — Cuerpos de triggers y diseño de permisos RLS (SOLO LECTURA)

**Estado: PREPARADO, pendiente de pruebas Windows y del informe SQL completo.** Paso 5 sigue EN PROCESO (Bloque 3 4/7). Rama exclusiva `feat/saas-multitenant-parcial2`.

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

**Resultado esperado y NO confirmado: `384 passed`** = 370 anteriores + 14 verificaciones estáticas de 5K.

Solo si pytest pasa:

```powershell
.\database\saas\18_auditar_cuerpos_triggers_y_privilegios_sandbox.ps1
```

Debe reportar `5K PREFLIGHT OK`, terminar en `ROLLBACK` y guardar el informe en TEMP local. Enviar salida de PowerShell y el archivo `fashionstore_5k_triggers_privilegios_sandbox_LOCAL_REVISAR.txt` revisado por secretos al chat, NUNCA a GitHub.

No ejecutar `CREATE ROLE`, `GRANT`, `CREATE POLICY`, `INSERT`, `CALL`, ni `sp_*_tenant.sql.txt`. No es necesario activar ningún feature flag.

## 6. Criterio de cierre de 5K

Se marcará como validada **solo** la preparación de modelo de seguridad + extracción READ ONLY de código de triggers cuando se reciban las pruebas `384 passed` y el contenido del informe sea analizado. Hasta entonces 5K EN PROCESO, Paso 5 EN PROCESO, Bloque 3 4/7. No ejecutar cambios en el sandbox hasta que el siguiente paso sea expresamente autorizado; esperar `siguiente`.
