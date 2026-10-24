# Bloque 3 / Paso 5J — Auditoría RLS y preparación de roles/datos ficticios en sandbox

**Estado: SUBFASE 5J VALIDADA EN ALCANCE READ ONLY Y DICTAMEN DEL INFORME COMPLETO.** Paso 5 comercial global EN PROCESO; Bloque 3 sigue 4/7. **NO SEED, NO DDL, NO LOGIN, NO creación de roles o políticas, NO CALL de procedimientos en 5J**.

## 1. Punto de partida confirmado

El estudiante ejecutó **357 passed, 2 warnings in 6.37s** y recuperó de forma controlada el sandbox exclusivo `127.0.0.1:5432 / fashionstore_saas_reservas_sandbox_5h`. La inspección posterior mostró **46 tablas públicas vacías**, 48 FK tenant validadas, flags RLS activos en seis tablas comerciales y **0 procedimientos `sp_*_tenant` instalados**. Se comprobó todo con el superusuario `postgres` (`rolsuper=t`, `rolbypassrls=t`), de modo que **no acredita el aislamiento de filas con un rol de aplicación**. La BD restaurada `fashionstore_restore_20261023_193554_594` sigue siendo SOLO LECTURA y no debe usarse para nuevas pruebas de escritura.

## 2. Hallazgo arquitectónico: ROLE es un objeto del clúster, no de una sola base

En PostgreSQL un `CREATE ROLE` crea identidad **para todo el clúster**, aunque sus privilegios `CONNECT/USAGE/SELECT/INSERT/UPDATE` se restrinjan a una base. Por lo tanto, **no crear todavía roles globales a ciegas**. Antes de agregar uno nuevo, verificar si existe una identidad de ensayo apropiada y que pueda acotarse; si no existe, preparar un rol de prueba exclusivamente para el laboratorio, por ejemplo `fashionstore_5j_rls_probe`, con **NOLOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION, NOBYPASSRLS**, sin membresía heredada. Es un **nombre propuesto**, NO un rol creado.

Para validar RLS de forma más realista posteriormente se puede usar una cuenta `LOGIN` restringida con contraseña temporal gestionada fuera del repositorio, y probar `current_user`, privilegios y políticas bajo una conexión real, además de auditoría controlada de `SET ROLE`. Una simulación `SET ROLE` lanzada por `postgres` no sustituye la verificación con autenticación del rol real, y ningún argumento empresarial de la API ni una variable `app.empresa_id` modificable prueba por sí solo la identidad.

Las políticas deben revisar acceso autorizado al tenant (p. ej. contexto empresarial y membresía validados), `WITH CHECK` en escrituras, `USING` en lectura y `UPDATE/DELETE`; evitar recursion de políticas al consultar `usuario_empresa`. Si no hay ninguna política aplicable, el modo RLS habilitado opera como **denegación por defecto para roles sujetos a RLS**; no introducir grants abiertos como solución rápida. No conceder `BYPASSRLS` ni `pg_read_all_data`; no usar `SECURITY DEFINER` sin auditoría del propietario y privilegios.

## 3. Auditoría NO destructiva preparada para 5J

Archivos nuevos:
- `database/saas/17_auditar_roles_rls_y_seed_sandbox_local.sql`: `BEGIN TRANSACTION READ ONLY` + `ROLLBACK`. Guardas estrictas de nombre de sandbox y `127.0.0.1`, 46 tablas, 48 FK, ausencia de `sp_*_tenant` y tablas centrales todavía vacías. Consulta flags RLS, `pg_policies`, roles de ensayo preexistentes, privilegios PUBLIC, restricciones, columnas obligatorias/default/IDENTITY y triggers del seed.
- `database/saas/17_auditar_roles_rls_y_seed_sandbox_local.ps1`: conecta solamente al sandbox, fuerza `default_transaction_read_only=on`, pide contraseña de PostgreSQL local y exporta informe textual a `$env:TEMP\fashionstore_5j_rls_roles_columnas_sandbox_LOCAL.txt`, fuera de GitHub. **No modifica la base** y no imprime contraseñas. Si el informe ya existe, aborta para evitar sobrescribirlo.
- `tests_saas/test_rls_roles_seed_preflight_5j.py`: **13 pruebas nuevas solo estáticas**, sin conexión a PostgreSQL.

**Resultado esperado, aún NO ejecutado por el estudiante: `370 passed`** = 357 anteriores + 13 nuevos. Las pruebas estáticas **no certifican políticas ni RLS efectivo**, solo restricciones de la auditoría.

La salida del SQL incluye posibles definiciones de políticas y triggers que pueden contener literales de configuración. Revisar el texto generado por posibles secretos antes de adjuntarlo al chat; **nunca subirlo al repositorio ni a Supabase**.

## 4. Plan de seed académicamente ficticio: diseño, NO sentencias DML aún

No se han supuesto IDs de empresa ni credenciales. El seed definitivo dependerá del reporte de `information_schema.columns`, `pg_constraint` y triggers, para evitar saltarse campos NOT NULL y romper FK. Propuesta **solo de datos inventados**, con dos empresas deliberadamente distinguibles por ejemplo `lab-tenant-a-5j` y `lab-tenant-b-5j`, cada una con un cliente de ensayo y su carrito/inventario ficticio. Para probar roles CU16/CU17/CU18 podrían requerirse un empleado encargado y un cajero de prueba, definidos según la matriz real `rol/rol_funcion`.

Orden tentativo de dependencias para diseñar después el seed en una transacción, usando `RETURNING id` en lugar de IDs globales fijos:

`rol` / `modulo` / `funcion` / `accion` y `rol_funcion` cuando corresponda; `ciudad`, `talla`, `color`; `plan_saas`; `empresa`; `suscripcion`; `usuario`; `usuario_empresa`; `cliente` / `sucursal` / `empleado`; `categoria`; `producto`; `temporada`; `variante_producto`; `inventario`; `carrito`; `detalle_carrito`.

Es **un orden preliminar**, a validar contra el catálogo de FK antes de codificar. Los `usuario.password_hash` serán hashes sintéticos **no reutilizados** y no se publicarán contraseñas; los nombres/correos/domicilios serán inventados (dominio reservado de ejemplo), evitando copiar datos de Vanter Men y las otras cuatro empresas. El seed posterior se permitirá **solo en el sandbox** y deberá ser idempotente o rechazar si detecta un estado preexistente; no habilita `SAAS_AUTH_ENABLED`.

## 5. Comandos del estudiante en PowerShell

Directorio `C:\SI2_Parcial1\fashionstore-api`, entorno Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Confirmado en Windows: `370 passed, 2 warnings in 5.91s`, cero fallos.** El informe del script se generó correctamente, pero su contenido detallado aún no se ha proporcionado al chat; sin él no concluir sobre políticas, grants ni columnas.

Si pytest pasa:

```powershell
.\database\saas\17_auditar_roles_rls_y_seed_sandbox_local.ps1
```

El script escribe el informe en `$env:TEMP`, **no modifica nada**. Compartir salida PowerShell y, tras revisarlo por si contiene información sensible, adjuntar el archivo `fashionstore_5j_rls_roles_columnas_sandbox_LOCAL.txt` al chat. Si el informe ya existe, reportar ese error sin borrar ni sobrescribir todavía.

**NO ejecutar** ningún `CREATE ROLE`, `CREATE POLICY`, `GRANT`, `INSERT`, ni los borradores `sp_*_tenant.sql.txt`. Mantener explícitamente `SAAS_AUTH_ENABLED=false` durante toda la subfase 5J; no habilitar rutas comerciales SaaS. No modificar `main`, la fuente restaurada ni Supabase.

## 6. Criterio de validación y próxima subfase

Al recibir los resultados, documentar qué políticas existen realmente, qué roles/privilegios son apropiados y qué campos exige el seed, sin inventarlos. **5J solo se cerrará como auditoría READ ONLY y diseño**. La creación de roles restringidos y el seed transaccional ficticio requerirán una subfase siguiente, con autorización explícita para escribir SOLO en el sandbox y comprobación de aislamiento con rol sin `BYPASSRLS`. Paso 5 y Bloque 3 seguirán abiertos; detenerse hasta `siguiente`.


## 7. Ejecución previa: auditoría READ ONLY y pruebas superadas

El estudiante ejecutó `git pull --ff-only origin feat/saas-multitenant-parcial2` y sincronizó el commit `8844e47`. Ejecutó `python -m pytest -q tests_saas` y obtuvo **`370 passed, 2 warnings in 5.91s`**, 0 fallos; las advertencias son las deprecaciones conocidas de Starlette/httpx y `HTTP_422_UNPROCESSABLE_ENTITY`.

Ejecutó `17_auditar_roles_rls_y_seed_sandbox_local.ps1` contra `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h`. El SQL informó **`5J PREFLIGHT OK: 46 tablas, 48 FK tenant, cero SP SaaS, sin datos centrales`**; el PowerShell informó **`AUDITORIA 5J SANDBOX READ ONLY COMPLETADA`** y guardó el informe en `$env:TEMP\\fashionstore_5j_rls_roles_columnas_sandbox_LOCAL.txt`, fuera del repositorio.

**Nota histórica:** en esta etapa de ejecución todavía faltaba leer las políticas, permisos y columnas. Ese análisis se completó posteriormente con el archivo compartido, según la sección 8. Aun con el archivo, RLS efectivo bajo rol restringido sigue SIN validar.

La auditoría READ ONLY y la suite estaban superadas; el dictamen del contenido del archivo se registra en la sección 8. No crear roles, insertar semillas, instalar `sp_*_tenant` ni ejecutar reservas dentro de 5J; conservar `SAAS_AUTH_ENABLED=false`.

## 8. DICTAMEN FINAL sobre el informe 5J recibido

**Evidencia:** informe local completo `fashionstore_5j_rls_roles_columnas_sandbox_LOCAL.txt` adjuntado por el estudiante (239 líneas de texto). El preflight se ejecutó con `BEGIN`, `SET`, `DO` y terminó en `ROLLBACK`, sin DDL/DML. La suite previa obtuvo **370 passed, 2 warnings in 5.91s**, 0 fallos. Esta sección documenta solo información efectivamente registrada en el archivo, sin ejecutar SQL adicional.

### 8.1 Roles y aislamiento (hallazgo BLOQUEANTE)

- Sección `1_ROLE_CURRENT`: base `fashionstore_saas_reservas_sandbox_5h`, conexión y rol actual `postgres`; `rolsuper=t`, `rolbypassrls=t`, `rolcreaterole=t`, `rolcreatedb=t`. No sirve para certificar RLS ni el comportamiento de una futura sesión de aplicación restringida.
- Sección `2_ROLE_NAMES_RESERVED`: **cero roles** con nombre `fashionstore_5j_%`. No se ha creado ningún rol de ensayo.
- Sección `3_RLS_TABLES`: **20/20 tablas examinadas** presentan `rls_enabled=t`, `force_rls=f`, propietario `postgres` y, fundamentalmente, **`numero_politicas=0` EN TODAS**. Se incluyen `empresa`, `plan_saas`, `suscripcion`, `usuario_empresa`, `usuario`, `rol`, `cliente`, `empleado`, `carrito`, `detalle_carrito`, `reserva`, `detalle_reserva`, `inventario`, `movimiento_inventario`, `venta`, `pago`, `categoria`, `producto`, `variante_producto` y `sucursal`.
- Sección `4_POLICIES_DETAIL`: **0 filas**. En la práctica hay **denegación por defecto para los roles sujetos a RLS** sobre estas tablas, pero `postgres` superusuario lo omite. No equivale a políticas multitenant funcionales. No conceder SELECT/INSERT/UPDATE indiscriminados ni usar el superusuario de la API para sortearlo.
- Sección `5_PUBLIC_GRANTS`: **0 privilegios para PUBLIC en las 10 tablas examinadas** (`carrito`, `cliente`, `detalle_carrito`, `detalle_reserva`, `empresa`, `inventario`, `movimiento_inventario`, `reserva`, `usuario`, `usuario_empresa`). **No se auditaron aquí todas las 46 tablas ni los privilegios explícitos de otros roles**, por lo que no extender la afirmación al resto.

### 8.2 Dependencias del seed ficticio

- Sección `6_SEED_COLUMNS`: **26 tablas** con catálogo de tipos, nulabilidad, valores por defecto e identidad. IDs de varias tablas son `GENERATED BY DEFAULT AS IDENTITY`. En tablas empresariales como `cliente`, `carrito`, `detalle_carrito`, `reserva`, `detalle_reserva`, `inventario`, `sucursal`, `producto`, `temporada` y `variante_producto`, `empresa_id` es **NOT NULL sin DEFAULT**: el seed deberá establecerlo expresamente en cada INSERT.
- `usuario` exige `rol_id`, `correo` único y `password_hash` NOT NULL. `usuario_empresa` exige `usuario_id`, `empresa_id` y `rol_id` válidos y únicos por par. Para ensayar los SP, `cliente.usuario_id` debe enlazar con usuario y membresía existentes aunque la columna sea nullable. `empleado` requiere usuario, sucursal, nombres/apellidos, CI, fecha de contratación y empresa.
- `plan_saas` exige `codigo`, `nombre` y `max_sucursales_activas`; `suscripcion` exige empresa, plan, rango de fechas y estado vigente. Para un SP que verifica reservas se necesita un plan que permita reservas y suscripción ACTIVA, sin usar IDs globales supuestos.
- Las dependencias comerciales incluyen `ciudad -> sucursal`, `categoria -> producto -> variante_producto`, `talla/color -> variante_producto`, `temporada + sucursal + variante -> inventario`, `usuario_empresa -> cliente/empleado`, `cliente + sucursal -> carrito`, y `carrito + inventario -> detalle_carrito`. El orden exacto se validará dinámicamente antes de cualquier seed.
- Sección `7_SEED_CONSTRAINTS`: **136 restricciones** (PK, FK, UNIQUE, CHECK) sobre tablas seleccionadas. Destacan las FK tenant compuestas `(empresa_id, ...)`, `uq_reserva_carrito`, los CHECK positivos de cantidades y la invariancia `0 <= stock_reservado <= stock_actual`. Cada inserción deberá ser atómica y respetar estas condiciones.
- Sección `8_TRIGGERS`: **10 triggers habilitados en las tablas inspeccionadas**; incluyen `fn_registrar_bitacora` (cliente, inventario, reserva, sucursal, usuario), `fn_normalizar_correo_usuario`, `fn_actualizar_actividad_carrito`, `fn_validar_sucursal_detalle_carrito`, `fn_validar_detalle_reserva_sucursal` y `fn_controlar_actualizacion_inventario`. El informe muestra la definición de `CREATE TRIGGER`, **no el cuerpo de las funciones**; deben inspeccionarse antes de probar mutaciones, porque podrían requerir campos de auditoría/contexto.

### 8.3 Decisiones de seguridad para la PRÓXIMA subfase, no cambios actuales

1. Definir contrato de identidad tenant confiable y política mínima para tablas de referencia, membresías y comerciales, con `USING` y `WITH CHECK`; revisar recursión de políticas al consultar `usuario_empresa`. El uso de `current_setting('app.empresa_id')` **por sí solo no acredita identidad** si un llamador puede modificar ese parámetro.
2. Diseñar role/grants de privilegio mínimo para laboratorio exclusivamente. `CREATE ROLE` afecta todo el clúster, por lo que **no crearlo sin consentimiento expreso ni antes de revisar el alcance y los permisos**, con `NOSUPERUSER`, `NOBYPASSRLS`, `NOCREATEDB`, `NOCREATEROLE`, sin membresías elevadas; no asumir que NOLOGIN basta para demostrar autenticación real.
3. Auditar en read-only las definiciones **completas** de las funciones de trigger de negocio/auditoría y los privilegios efectivos antes de ejecutar el futuro seed. Mantener el futuro seed de dos empresas, clientes y empleados TOTALMENTE ficticios, sin reutilizar contraseñas, datos personales, NIT o correos reales.
4. Probar SELECT/INSERT/UPDATE/DELETE permitido y denegado, cruces empresa A/B, errores de FK, y rollback del stock/Kardex bajo usuario SQL restringido en un **PostgreSQL de ensayo separado**, antes de instalar/invocar SP `SECURITY INVOKER` o habilitar rutas.

**CIERRE 5J: VALIDADA como auditoría read-only y dictamen de diseño.** El sandbox sigue sin seed, sin roles de ensayo, sin SP tenant instalados. **No está validada la seguridad RLS de aplicación** ni las transacciones de reservas. Paso 5 global EN PROCESO, Bloque 3 4/7. `SAAS_AUTH_ENABLED=false`, `main`, Supabase y la base restaurada sin cambios. **No solicitar más comandos ahora; esperar la palabra `siguiente`**.
