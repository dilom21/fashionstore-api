# Evidencia CU16–CU18: JWT, identidades y bloqueo de despliegue (5AO)

**Rama:** `feat/saas-multitenant-parcial2`.
**Estado:** FUNCIONES SQL VALIDADAS EXCLUSIVAMENTE EN LAB, DESPLIEGUE SaaS COMERCIAL NO AUTORIZADO.

## Resultados observados en Windows (24 octubre 2026)

- `5AN` devolvió `CU16 PASS`, `CU17 PASS` y `CU18 PASS`, con roles SQL `fashionstore_5ab_a` y `fashionstore_5ab_b`, membresías ficticias, suscripción activa y políticas RLS **temporales**.
- Después de ejecutar ambos escenarios se verificó `ROLLBACK Y PREFLIGHT OK`: laboratorio PostgreSQL 18 en `127.0.0.1:55432`, 41 policies originales y 0 SP comerciales instalados.
- En una ejecución previa se recibieron `744 passed` para `tests_saas`; posteriormente se agregaron pruebas 5AM/5AN y 5AO, que requieren ejecutar regresión nueva.
- Las cinco empresas de entrega en PostgreSQL 5432 y Supabase nunca participaron en el ensayo 5AN.

## Qué cubren las pruebas HTTP 5AO

`tests_saas/test_cu16_jwt_identidad_http_5ao.py` usa **JWT real firmado** con `emitir_token_empresa`, `leer_token_empresa`, la dependencia real `get_current_tenant_context`, el middleware de ruta y las rutas CU16, CU17 y CU18. Solo reemplaza con dobles de prueba las lecturas de membresía PostgreSQL y la ejecución de CALL. Comprueba:

- Ausencia, alteración, firma incorrecta o expiración de token: 401.
- Membresía revocada o empresa que no corresponde al token: 403.
- `usuario_id`, `empresa_id` y rol no pueden imponerse mediante JSON: 422.
- Dos identidades de la **misma empresa** mantienen actores diferentes; crear o cancelar carritos/reservas ajenos se rechaza en un **repositorio simulado**.
- Personal y clientes ejercen roles distintos en CU16–CU18; los flags no se activan permanentemente.
- No se conecta a la base de datos local ni a servidores externos.

## Hallazgo de seguridad pendiente: actor dentro del tenant

Las rutinas comerciales SQL del borrador `12`, `13` y `14` reciben `p_usuario_id`. Su validación consulta `usuario_empresa`, pero **no existe aún una prueba criptográfica o de principal SQL que ligue el `p_usuario_id` declarado a un usuario final específico**. Los roles SQL A y B representan **empresas**, no individuos. Por tanto, un actor que obtenga acceso SQL directo con el rol A podría pasar el identificador de otro miembro habilitado de la misma empresa; la sola validación de RLS empresa y `usuario_empresa` no puede descartar la suplantación.

La protección HTTP sí obtiene el `usuario_id` de un JWT firmado y revalida la membresía; nunca acepta ese ID por JSON. **Eso no autoriza exponer conexiones SQL ni implica que RLS pruebe por sí solo la identidad individual.** Asimismo, la futura conexión API debe adoptar un principal restringido verificable y ejecutar tests reales contra el SP instalado, además de evitar credenciales SQL públicas.

**Antes de habilitar comercio SaaS**: resolver el vínculo de identidad a nivel de principal/servicio de confianza, validar login/bootstrap restringido, instalar ACL+RLS+SP en ambiente de prueba no productivo, demostrar operaciones JWT→HTTP→PostgreSQL con rol no BYPASSRLS, probar dos usuarios dentro de un tenant y repetir los tests de concurrencia/stock. Hasta entonces mantener `SAAS_AUTH_ENABLED=false` y `SAAS_RESERVAS_ENABLED=false`.

**Regla:** no habilitar flags basándose únicamente en tests mock o resultados SQL aislados. No modificar Supabase ni la base 5432.

## Evidencia posterior 5AP y siguiente control 5AQ (24 octubre 2026)

**5AP validado por el estudiante:** `7 passed` para `tests_saas/test_http_jwt_postgresql_reversible_5ap.py` y, en el laboratorio 55432, `5AP ACTORES PASS`, `5AP CU16 PASS`, `5AP CU17 PASS`, `5AP CU18 PASS`, `5AP JWT+HTTP+SQL PASS`, `5AP ROLLBACK VERIFICADO`. Fueron tres CALL SQL reales con JWT y membresías PostgreSQL; los COMMIT de negocio fueron interceptados por el adaptador de ensayo y todo terminó en ROLLBACK. Las cinco empresas originales no se tocaron.

**5AQ preparado, aún SIN ejecutar:** `database/saas/52_auditar_suplantacion_usuario_sql_5aq.py`. Reutiliza el preflight del laboratorio, crea temporalmente un segundo cliente con carrito propio e intenta dos CALL bajo el MISMO `session_user=fashionstore_5ab_a`: un intento declarado como usuario A1 hacia el carrito de A2 (debe fallar), y un intento declarando A2 como usuario (se prevé que se acepte). El objetivo es documentar con evidencia el límite de un principal SQL compartido dentro de un tenant, no afirmar que el JWT de FastAPI es falsificable. La prueba es deliberadamente negativa; el éxito de la demostración no satisface RNF02.

**Remediación a diseñar, NO desplegada:** el SQL no debe aceptar `p_usuario_id` de un cliente directo que usa un principal empresarial compartido. Opciones: principal PostgreSQL individual restringido y mapeo DBA-protegido `session_user ↔ empresa_id/usuario_id` validado por los tres SP, **o** credencial firmada para cada invocación cuya firma, audiencia, caducidad y actor sean verificadas por la base mediante un verificador privilegiado cuidadosamente auditado. Un `current_setting('app.usuario_id')` editable por el propio login empresarial NO demuestra identidad individual y no resuelve el hallazgo. El login SQL de reserva NO debe estar expuesto a clientes; revocar EXECUTE de los principals compartidos antes de transición. No usar `SET ROLE` como prueba de identidad individual, pues un login con capacidad de cambiar a otros roles también podría suplantarlos.

Mantener `SAAS_AUTH_ENABLED=false`, `SAAS_RESERVAS_ENABLED=false`, sin migraciones al servidor 5432 ni Supabase, hasta cerrar esta brecha y repetir los ensayos SQL/HTTP con principal del actor real.

## Resultado 5AQ confirmado y ensayo 5AR preparado

**5AQ confirmado por el estudiante**: `5 passed` para las pruebas estructurales y en PostgreSQL del laboratorio se obtuvo `5AQ CONTROL PASS`, luego `5AQ BRECHA DEMOSTRADA: el MISMO session_user A admitió p_usuario_id=A2 en CALL directo`, después `5AQ ROLLBACK VERIFICADO`. Esto es evidencia de **suplantación SQL intraempresa** si alguien accede al rol compartido, no evidencia de falsificación de JWT.

**5AR preparado, NO ejecutado ni instalado**: `database/saas/53_rol_sql_por_usuario_guardia_reversible_5ar.py`. En una única transacción revertida crea dos roles individuales ficticios A1/A2, `PASSWORD NULL`, sin `BYPASSRLS` y sin privilegios sobre el mapa de identidad; crea un verificador `SECURITY DEFINER` de alcance mínimo y `search_path` fijo; revoca el acceso `PUBLIC` y solo permite consultas booleanas. Reemplaza *temporalmente* la definición de CU16 para exigir que `session_user` coincida con el usuario de `p_usuario_id` a través del mapa inaccesible a los usuarios de aplicación. Demuestra que A1 no se puede presentar como A2 aunque altere `app.usuario_id` y que un CALL directo desde el principal SQL compartido A falla con `42501`. Termina con `ROLLBACK` y preflight del laboratorio intacto.

**No equivale a instalar roles de cada usuario en el backend real**. El ensayo usa `SET LOCAL SESSION AUTHORIZATION` desde `postgres` **únicamente como simulación controlada de laboratorio**; no debe incorporarse como mecanismo de selección de identidad a un pool SQL superusuario del backend. Para un SaaS real debe existir autenticación PostgreSQL no superusuario por actor o una alternativa de aserción criptográfica verificada por un componente SQL de confianza. Requiere además adaptar RLS y permisos para principales individuales, y replicar guardia al CU17/CU18 antes de conceder ejecución.

La base `5432` de cinco empresas y Supabase permanecen fuera de todo el ensayo. Los flags `SAAS_AUTH_ENABLED` y `SAAS_RESERVAS_ENABLED` siguen `false`.

## 5AR verificado y 5AS preparado para los tres SP

**5AR confirmado en PostgreSQL local por el estudiante:** `6 passed`; `5AR ACTOR PASS` para `fashionstore_5ar_a1` y `fashionstore_5ar_a2`; `5AR CU16 BLOQUEADO PASS` con SQLSTATE `42501` para un principal compartido, y `5AR ROLLBACK VERIFICADO`. Esto demuestra el control de identidad para el SQL de CU16 en ensayo temporal; no autoriza acceso de producción.

**5AS creado, pendiente de ejecutar:** `database/saas/54_tres_sp_guardia_actor_sql_5as.py` y sus pruebas `tests_saas/test_guardias_tres_sp_actor_sql_reversible_5as.py`. Reutiliza el mapa de identidades de 5AR, agrega un rol individual sin contraseña B1 y protege CU17 y CU18 además de CU16 con la misma guarda al inicio de cada SP, únicamente en `BEGIN ... ROLLBACK` del laboratorio 55432. Exige que todos los intentos indebidos de actores A1/A2/B1 o logins compartidos A/B produzcan `42501`, verificando antes que tengan `EXECUTE` para no confundir rechazo de actor con falta de ACL. Verifica también la manipulación de `app.usuario_id`, la eliminación de los roles y el mapa, y el preflight posterior.

**Límite explícito:** las pruebas positivas de operaciones DML como roles SQL individuales **aún no existen**. Es necesario diseñar y ensayar RLS SELECT/INSERT/UPDATE, secuencias, triggers, permisos por actor y conexiones PostgreSQL no superusuario, y repetir los flujos HTTP antes de habilitar el SaaS. El verificador `SECURITY DEFINER` prototipo fue creado por `postgres` y devuelve solo un booleano, pero su titular y superficie de ejecución necesitarán una auditoría adicional. Los flags SaaS siguen desactivados.

## 5AS aprobado y paso 5AT listo para ensayo

**Evidencia observada de 5AS:** el estudiante ejecutó `7 passed`; en el laboratorio aparecieron `5AS CU16 DENEGADO PASS`, `5AS CU17 DENEGADO PASS` y `5AS CU18 DENEGADO PASS` bajo principales compartidos A/B e individuales ajenos, todos con el código `42501` causado por la guardia de identidad. Los tres roles individuales fueron reconocidos para sus propios usuarios y la GUC editable no permitió cambiar la identidad. `5AS ROLLBACK OK` confirmó que no persistieron roles, mapa ni SP. Los SP de producción siguen sin instalar.

**5AT preparado; todavía no ejecutado:** `database/saas/55_sp_actor_individual_dml_rollback_5at.py` agrega temporalmente permisos de columnas y secuencias y políticas RLS orientadas al principal SQL individual, basadas en una función de lectura mínima del mapa privado. Con la guardia instalada en los tres SP, A1 y A2 deben poder completar cada uno `CU16→CU17` con su propio carrito, reteniendo y devolviendo exactamente una unidad; B1 debe poder completar `CU18` sin compra. Se verifica el aislamiento A/B, que RLS esté activo y que los roles individuales carezcan de permisos sobre el mapa. Todo se revierte en `ROLLBACK` y se comprueba el baseline original.

**Límites de 5AT:** las sesiones individuales aún se simulan por el superusuario `postgres` usando `SET LOCAL SESSION AUTHORIZATION` en el laboratorio. Este método es únicamente un arnés de prueba: NO se puede llevar al backend como selector de identidad del JWT. El futuro diseño debe resolver conexiones/roles sin privilegios de superusuario, administración segura de credenciales, mínimo privilegio, seguridad de la función `SECURITY DEFINER`, concurrencia y pruebas HTTP reales con transacciones independientes antes de activar flags.

## 5AT validado y control preventivo 5AU

**5AT validado por el estudiante:** `7 passed` y en PostgreSQL 18 del laboratorio se observaron `5AT ACL/RLS OK`, `5AT CU16 PASS` y `5AT CU17 PASS` para los roles individuales ficticios A1 y A2, `5AT CU18 PASS` para B1 y `5AT ROLLBACK VERIFICADO`. Los cinco CALL positivos demostraron stock correcto y permisos DML; no persistieron roles, SP, mapa ni políticas temporales. Las cinco empresas originales no fueron afectadas.

**5AU (cambio deliberadamente fail-closed del adaptador de la aplicación):** `app/modules/reservas/services/tenant_reserva_transaccional_5al.py` ahora requiere, **además** de los checks existentes de RLS, plan vigente y EXECUTE, una función SQL `public.fs_verificar_actor_reserva(bigint,bigint)` que compruebe que el `session_user` real corresponde al `empresa_id` y `usuario_id` del JWT/membresía. Su dueño exacto se deberá configurar desde `SAAS_ACTOR_SQL_OWNER`; por defecto es `None` y todas las escrituras comerciales fallan con 503 antes de CALL. No se usa `app.usuario_id` ni `app.empresa_id` como aserción de identidad.

Antes de invocar la función el adaptador inspecciona metadatos PostgreSQL: `SECURITY DEFINER`, `search_path=pg_catalog, pg_temp`, propietario NOLOGIN/no superusuario/sin BYPASSRLS, dueño coincidente con configuración, sin ACL EXECUTE a PUBLIC, caller con EXECUTE pero sin CREATE sobre base/public, ni membresía en el rol dueño. Si cualquier comprobación falla, se deniega. **No se ha instalado esa función ni configurado su dueño**: 5AU es un bloqueo preventivo, no una solución desplegada.

**Compatibilidad deliberadamente suspendida del viejo ensayo 5AP:** al ejecutar los antiguos SP sin el verificador de 5AU, la ruta HTTP de escritura deberá responder 503, incluso sobre el laboratorio. La pasada de 5AP anterior sigue siendo evidencia histórica, pero no debe usarse como verificación de esta nueva versión de seguridad. La siguiente integración deberá provisionar el verificador aprobado con dueño sin LOGIN, conectar a PostgreSQL sin superusuario como el actor correcto, y adaptar las pruebas end-to-end a ese entorno de ensayo; este paso puede requerir una base de laboratorio nueva o provisioning transaccional aislado con un procedimiento específico de limpieza. Nunca hacerlo en 5432 ni Supabase.

**Flags:** `SAAS_AUTH_ENABLED=false` y `SAAS_RESERVAS_ENABLED=false`. No configurar `SAAS_ACTOR_SQL_OWNER` hasta que exista y se audite la función y todo su mapa de identidad.

## 5AU confirmado y 5AV integración preparada (aún SIN ejecutar)

**5AU aprobado por el estudiante:** `18 passed in 0.66s` para `tests_saas/test_guardia_identidad_sql_servicio_5au.py`, sin conexión al laboratorio. El servicio real devuelve un bloqueo antes de `CALL` si no existe verificador, el dueño no es de confianza, hay `PUBLIC EXECUTE`, privilegios CREATE indebidos, membresía con el dueño o si el actor SQL no coincide con JWT. Las banderas SaaS no se activaron.

**5AV disponible para ensayo:** `database/saas/56_servicio_actor_guard_real_rollback_5av.py` y `tests_saas/test_servicio_actor_guard_real_reversible_5av.py`. Reutilizan los fixtures y 104 policies temporales de 5AT; añaden una función `public.fs_verificar_actor_reserva(bigint,bigint)` de titular **NOLOGIN separado**, con permiso limitado de lectura del mapa, RLS select solo para ese titular, sin ejecución pública y sin CREATE del dueño tras su traspaso. Los tres SP del ensayo usan la **misma** función que valida el servicio 5AU. Se prueba con PostgreSQL verdadero: A1 no puede acreditarse como A2 aunque edite `app.usuario_id`, A1/A2 ejecutan `CU16→CU17`, B1 ejecuta `CU18`, y el principal compartido falla cerrado. NO utiliza HTTP ni credenciales PostgreSQL reales de usuarios finales. Los cinco COMMIT del servicio se interceptan y el único `BEGIN` exterior termina en `ROLLBACK` con preflight.

**Auditoría negativa complementaria 5AV:** tras reservar/cancelar como A2 se intenta desde A1 un `UPDATE public.reserva` directo sobre una reserva propiedad de A2. Ese UPDATE se revierte con `SAVEPOINT` tanto si se acepta como si se deniega. Un resultado `5AV ALERTA DML` demostraría que los permisos DML de 5AT son demasiado amplios incluso bajo roles SQL individuales; NO autoriza producción. Se deberá limitar el DML directo (p. ej. vía procedimientos protegidos cuya identidad y RLS estén auditados, y sin otorgar UPDATE abierto sobre todo el tenant).

**5AV no resuelve** la autenticación de login PostgreSQL no superusuario, la gestión de conexiones por actor y de secretos, ni el aislamiento si se expone acceso SQL directo. Nunca se incorpora `SET SESSION AUTHORIZATION` a la aplicación. Las cinco empresas de 5432 y Supabase no participan. Los flags siguen desactivados y el dueño real `SAAS_ACTOR_SQL_OWNER` sigue sin configurar.

## 5AV confirmado: vulnerabilidad UPDATE directa intraempresa y remediación 5AW

**5AV confirmado por el estudiante:** `8 passed` en `test_servicio_actor_guard_real_reversible_5av.py`. En el laboratorio, `5AV CU16 PASS` y `5AV CU17 PASS` para A1 y A2, `5AV CU18 PASS` para B1, `5AV PRINCIPAL COMPARTIDO PASS`. Pero también se obtuvo exactamente `5AV ALERTA DML: A1 pudo ejecutar UPDATE directo sobre reserva de A2`. `5AV ROLLBACK OK` restituyó todos los roles, tablas auxiliares, procedimientos, stock y 41 políticas. El alcance de identidad SQL individual **NO** convierte automáticamente las policies de empresa en aislamiento de cada cliente.

**5AW preparado, sin ejecución aún:** `database/saas/57_restringir_reserva_rls_actor_rollback_5aw.py` y `tests_saas/test_restringir_reserva_actor_rls_reversible_5aw.py`. Agrega exclusivamente **dos políticas AS RESTRICTIVE** (`FOR SELECT`, `FOR UPDATE`) en `public.reserva` para los roles A1/A2/B1, como intersección con las políticas permisivas existentes. Cada fila debe pertenecer al cliente cuyo `usuario_id` coincide con el `session_user` verificado por `fs_verificar_actor_reserva`, o a un ADMINISTRADOR activo y autenticado de esa misma empresa. Repite cinco operaciones comerciales por los tres SP y comprueba que A1 NO pueda leer ni UPDATE una reserva de A2 **aun cuando sí posee GRANT UPDATE**. Devuelve todo a baseline con `ROLLBACK` y preflight.

**Limitación esencial:** incluso si 5AW bloquea A1→A2, la actualización SQL directa de una reserva *propia* y del inventario de su empresa puede seguir permitida. Esto evita garantías de transición de estado y consistencia de stock que solo los SP implementan. Se auditan ambas operaciones con savepoints reversibles. Para cerrar de verdad el perímetro de escritura comercial habrá que retirar GRANT DML directo a actores y rediseñar procedimientos de privilegio mínimo con propietario NOLOGIN, `SECURITY DEFINER` auditado y comprobaciones de actor, permisos, search_path y RLS; no simplemente añadir otra policy. Ningún resultado del laboratorio autoriza activar los flags ni acceso real desde HTTP.

## 5AW verificado y protección de escritura 5AX preparada

**5AW confirmado por el estudiante:** `8 passed`, los cinco flujos reales CU16/CU17 para A1/A2 y CU18 para B1 volvieron a funcionar, y `5AW AJENO BLOQUEADO PASS` impidió que A1 leyera o actualizara la reserva de A2 pese a contar con `GRANT UPDATE`. Pero también fueron observadas `5AW ALERTA DML PROPIO` (A2 actualiza directamente su reserva) y `5AW ALERTA INVENTARIO` (A1 actualiza directamente inventario). La transacción terminó con `5AW ROLLBACK VERIFICADO`; base LAB intacta.

**5AX preparado, aún NO ejecutado:** `database/saas/58_sp_owner_nologin_sin_dml_directo_rollback_5ax.py` y `tests_saas/test_writer_nologin_sin_dml_directo_5ax.py`. Solo en `127.0.0.1:55432`, preflight estricto y `BEGIN...ROLLBACK`. Crea temporalmente un propietario independiente `fashionstore_5ax_sp_writer`, `NOLOGIN`, sin `BYPASSRLS` ni `CREATE` al finalizar el aprovisionamiento. Replica temporalmente las 54 policies SELECT/INSERT/UPDATE, incluyendo las dos restrictivas de 5AW, para ese propietario; concede solo columnas/secuelas necesarias y EXE-CUTE del verificador de actor. Cambia **solo en el ensayo** los tres SP a `SECURITY DEFINER` con dueño `NOLOGIN`, `search_path=pg_catalog,pg_temp` y conservando la guardia que comprueba `session_user` versus `usuario_id`. Revoca tanto permisos DML de tabla como los permisos DML por columna para los tres actores, confirma su ausencia mediante `has_column_privilege`, y exige que CU16→CU17 de A1/A2 y CU18 de B1 sigan funcionando exclusivamente mediante CALL.

**Resultado requerido de seguridad:** un `UPDATE` directo de la reserva propia, de otra persona, del carrito y del inventario debe fallar con `42501`, mientras los procedimientos protegidos continúan funcionando y A1 no puede leer la reserva A2. Si algo falla, el ensayo se considera incompleto. La base vuelve a 41 policies originales, 3 reservas, 4 carritos y 0 roles/procedimientos comerciales con `ROLLBACK`.

**Advertencia arquitectónica:** esto es un prototipo de separación de privilegios **solo laboratorio**, no la instalación definitiva. `SECURITY DEFINER` incrementa la superficie sensible, requiere auditar código/funciones llamadas y titularidad de cada procedimiento, asegurar permisos de esquemas y funciones y evitar dinámicas SQL inseguras. Tampoco prueba login PostgreSQL no-superusuario por actor, pool de FastAPI/JWT, conexión independiente ni pruebas de concurrencia. El arnés utiliza `SET LOCAL SESSION AUTHORIZATION` bajo el administrador sólo en PG18 aislado, jamás en código de aplicación. No ejecutar migraciones, instalar los SP comerciales ni activar flags SaaS sin superar las validaciones posteriores.

### 5AX — precisión adicional para principales históricos y pruebas negativas

La protección transitoria 5AX **también revoca INSERT/UPDATE/DELETE a los logins compartidos `fashionstore_5ab_a` y `fashionstore_5ab_b`** que conservan permisos de la instalación LAB 5AJ/5AN. En total revisa permisos por columna y tabla para cinco principales (A1/A2/B1/A/B) en ocho tablas comerciales. El ensayo intenta igualmente `UPDATE` directo de inventario desde el login compartido A. El `ROLLBACK` preserva exactamente las ACL originales del laboratorio.

También ejecuta dos `CALL` negativos contra la nueva modalidad temporal `SECURITY DEFINER`: A1 declarando a A2, y el antiguo principal A declarando a A1. Exige que el SP dé `42501` con el mensaje **exacto de la guardia de identidad**, para descartar falsos positivos por falta de permisos.

**Sin ejecutar todavía:** no existen resultados positivos de 5AX hasta que el estudiante ejecute el script completo. El diseño final de deployment debe considerar que funciones/procedimientos `SECURITY DEFINER` cambian `current_user` al dueño, por lo que todas las comprobaciones de identidad se basan explícitamente en `session_user` y requieren auditoría de los tres cuerpos de SP antes de su promoción.
