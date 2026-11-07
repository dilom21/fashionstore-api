# Evidencia del laboratorio 5CB

- Fecha: 2026-11-06T20:42:10+00:00
- PostgreSQL: 17.9
- Base: fashionstore_lab_5cb (clúster desechable en %TEMP%)
- 060_lab_correcciones.sql: **NO aplicado** (050a-c se ensayan tal cual)
- Copias 050a-c con la guarda retirada: 050a_lab.sql, 050b_lab.sql, 050c_lab.sql
- trg_bitacora_categoria: antes=0 despues=1 idempotente=True

## Resumen estructural

| Métrica | Valor |
|---|---|
| tablas | 46 |
| vistas | 1 |
| fk | 145 |
| fk_no_validadas | 0 |
| indices | 251 |
| triggers | 59 |
| tablas_rls | 46 |
| tablas_force_rls | 46 |
| politicas | 111 |
| funciones_privadas | 15 |
| empresas | 2 |
| planes | 2 |
| membresias | 5 |

## Pruebas

| Código | Resultado | Prueba | Detalle |
|---|---|---|---|
| EST-01 | PASS | 46 tablas y 145 FK validadas, igual que la etapa 2 | 46 tablas, 145 FK válidas, 0 sin validar |
| EST-02 | PASS | FORCE RLS en las 46 tablas y políticas instaladas | force=46 políticas=111 |
| EST-03 | PASS | categoria NO tenía trigger de auditoría antes de 040 y tiene 1 después | antes=0 después=1 |
| EST-04 | PASS | Re-ejecutar el bloque DO de 040 no duplica el trigger | 2 ejecuciones del bloque literal -> 1 trigger |
| EST-05 | PASS | El trigger es AFTER INSERT/UPDATE/DELETE con fn_registrar_bitacora() | CREATE TRIGGER trg_bitacora_categoria AFTER INSERT OR DELETE OR UPDATE ON public.categoria FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora() |
| EST-06 | PASS | La auditoría la escribe un owner NOLOGIN con search_path fijo | definer=True owner=fashionstore_audit_owner login=False search_path=public, pg_temp |
| EST-07 | PASS | Ningún rol fashionstore es SUPERUSER ni tiene BYPASSRLS | 10 roles fashionstore sin SUPERUSER ni BYPASSRLS |
| EST-08 | PASS | Conexión API autentica con login propio y sin BYPASSRLS | session_user=fashionstore_5cb_api current_user=fashionstore_5cb_api bypassrls=False |
| EST-09 | PASS | La API ve exactamente una empresa (la de su token) | 1 empresa visible para A1 |
| ID-01 | PASS | Sin token, toda consulta empresarial falla cerrada | rechazado con sqlstate=42501 |
| ID-02 | PASS | Token válido A1 deriva empresa y usuario del JWT firmado | actor=(empresa 1, usuario 101) |
| ID-03 | PASS | Manipular app.empresa_id/app.usuario_id NO cambia la identidad | actor sigue (1,101) y 1 producto visible |
| ID-04 | PASS | Token con firma alterada | rechazado con sqlstate=42501 |
| ID-05 | PASS | Token expirado | rechazado con sqlstate=42501 |
| ID-06 | PASS | Token con cabecera alg=none | rechazado con sqlstate=42501 |
| ID-07 | PASS | Token sin jti | rechazado con sqlstate=42501 |
| ID-08 | PASS | Token con scope distinto de tenant | rechazado con sqlstate=42501 |
| ID-09 | PASS | Token de A1 declarando una empresa donde no es miembro | rechazado con sqlstate=42501 |
| ID-10 | PASS | Token de usuario inexistente | rechazado con sqlstate=42501 |
| ID-11 | PASS | Token con iat en el futuro | rechazado con sqlstate=42501 |
| ID-12 | PASS | Token desmesurado (>8192 caracteres) | rechazado con sqlstate=42501 |
| ID-13 | PASS | Contexto 'personal' con un usuario que sólo es cliente | rechazado con sqlstate=42501 |
| ID-14 | PASS | Revocar la membresía invalida el token vigente en la siguiente sentencia | 42501 en la sentencia siguiente a la revocación |
| ID-15 | PASS | Suspender la suscripción invalida el token vigente | 42501 con la suscripción de la empresa B suspendida |
| ID-16 | PASS | Rotación de clave: el token previo sigue válido con 'previous' | clave previa aceptada mientras existe y revocada al eliminarla |
| ACL-01 | PASS | El GRANT SELECT sobre bitácora no basta: sin token falla cerrada | rechazado con sqlstate=42501 |
| ACL-02 | PASS | El login API no puede INSERTAR en bitácora (por eso audita el trigger) | rechazado con sqlstate=42501 |
| ACL-03 | PASS | El login API no puede BORRAR eventos de bitácora | rechazado con sqlstate=42501 |
| ACL-04 | PASS | El administrador del tenant sí puede crear y actualizar categorías | 1 INSERT y 1 UPDATE aplicados |
| ACL-05 | PASS | Un cliente no puede crear categorías | rechazado con sqlstate=42501 |
| ACL-06 | PASS | Nadie puede borrar categorías (falta DELETE) | rechazado con sqlstate=42501 |
| ACL-07 | PASS | El login de bootstrap no lee tablas de negocio directamente | rechazado con sqlstate=42501 |
| ACL-08 | PASS | El login de bootstrap no ve credenciales completas de usuario | rechazado con sqlstate=42501 |
| ACL-09 | PASS | El login API no puede ejecutar bootstrap_empresa_id() | rechazado con sqlstate=42501 |
| ACL-10 | PASS | El login API no tiene CREATE en el esquema public | CREATE TABLE rechazado con 42501 |
| BST-01 | PASS | bootstrap_empresa_id('vanter-men') resuelve la empresa 1 | devolvió la empresa 1 |
| BST-02 | PASS | Un slug inexistente devuelve 0 filas, no error | 0 filas |
| BST-03 | PASS | El slug se normaliza (mayúsculas y espacios) | normalización lower(btrim()) aplicada |
| BST-04 | PASS | Inyección SQL en el slug no devuelve ninguna empresa | 0 filas con x' OR true-- |
| BST-05 | PASS | Un slug válido no devuelve más de una empresa | como máximo una fila |
| BST-06 | PASS | Una empresa inactiva no se resuelve por slug | empresa inactiva -> 0 filas |
| BST-07 | PASS | bootstrap_credencial devuelve sólo id y hash del correo exacto | correo normalizado, sólo id+hash, 0 filas si no existe |
| BST-08 | PASS | Corrección propuesta para 050a: SELECT(id, slug, estado) en empresa | GRANT SELECT(id, slug, estado) repara bootstrap_empresa_id() |
| AIS-01 | PASS | A1 sólo ve el catálogo de su empresa | producto=1 cliente=2 categoria=1 |
| AIS-02 | PASS | B1 sólo ve el catálogo de su empresa | producto=1 cliente=1 categoria=1 |
| AIS-03 | PASS | A1 no puede leer por id una fila de B | producto 2 de B invisible |
| AIS-04 | PASS | A1 no puede ACTUALIZAR la fila de otra empresa | 0 filas afectadas sobre producto 2 |
| AIS-05 | PASS | A1 sólo ve su propio inventario | 2 inventarios en A, ninguno de B |
| AIS-06 | PASS | A1 no puede INSERTAR filas con empresa_id ajeno | rechazado con sqlstate=42501 |
| AIS-07 | PASS | B2 tampoco puede INSERTAR en la empresa 1 | rechazado con sqlstate=42501 |
| AIS-08 | PASS | Cambiar empresa_id de una fila se bloquea (inmutable) | rechazado con sqlstate=42501 |
| AIS-09 | PASS | B2 sólo ve las ventas de su empresa | 1 venta en B |
| AIS-10 | PASS | La bitácora ve sólo la empresa del token y el cliente no ve nada | A1: 16 propios y 0 ajenos; cliente A2: 0; B2: 13 propios y 0 ajenos |
| BIT-01 | PASS | Las fixtures dejan 2 eventos de categoría, uno por empresa y sin usuario | una fila por empresa sembrada en las fixtures |
| BIT-02 | PASS | El administrador crea una categoría y confirma la transacción | categoría 7 creada y confirmada |
| BIT-03 | PASS | El INSERT de categoría deja EXACTAMENTE un evento CREAR | eventos=[(1, 101, 'CREAR')] |
| BIT-04 | PASS | El UPDATE de categoría añade UN evento MODIFICAR (no duplica el CREAR) | CREAR + MODIFICAR: un evento por escritura, dos en total |
| BIT-05 | PASS | El evento se atribuye a la empresa de la FILA, no a la sesión | el evento de B2 lleva empresa 2 y usuario 202 |
| BIT-06 | PASS | Una escritura bloqueada por RLS no genera evento | 0 filas afectadas y ningún evento nuevo |
| BIT-07 | PASS | Manipular app.empresa_id no falsifica la empresa del evento | app.empresa_id=2 no altera el empresa_id=1 del evento |
| BIT-08 | PASS | El DELETE también se audita y conserva la empresa de la fila | ELIMINAR auditado con empresa 1 y usuario NULL (sin sesión de API) |
| BIT-09 | PASS | Las pruebas no dejan residuo en la bitácora | quedan 2 eventos de categoría |
| SP-01 | PASS | El SP crea la reserva y descuenta disponibilidad de forma atómica | reserva 3: stock reservado, carrito convertido, 1 detalle |
| SP-02 | PASS | El SP exige una sola reserva por carrito (SP-02 usado) | segundo intento rechazado con 23514 |
| SP-03 | PASS | El SP exige que la empresa coincida con el token | rechazado con sqlstate=42501 |
| SP-04 | PASS | El SP exige que el usuario coincida con el token | rechazado con sqlstate=42501 |
| SP-05 | PASS | El SP rechaza una fecha de atención pasada | rechazado con sqlstate=22007 |
| POOL-01 | PASS | El token no sobrevive al fin de la transacción | la conexión física queda sin actor tras el rollback |
| POOL-02 | PASS | El pool de SQLAlchemy reutiliza la conexión sin fugar el actor | la conexión devuelta al pool no hereda el actor anterior |
| SONDA-01 | PASS | app/core/bitacora_trigger.py ve trg_bitacora_categoria | pg_trigger detectado en la primera consulta y memorizado |
| SONDA-02 | PASS | El repositorio real delega la auditoría al trigger y no la duplica | categoría 10 con un único evento CREAR (sin duplicado del repo) |
| SONDA-03 | PASS | El repositorio real tampoco duplica el evento del UPDATE | CREAR + MODIFICAR desde el repositorio, un evento cada uno |
| SONDA-04 | PASS | La sonda deja la base como la encontró | quedan 2 eventos |
| FAPI-01 | PASS | Login real (bootstrap + Argon2) y /lab/me con JWT | login bootstrap+Argon2 OK, /lab/me={'usuario_id': 101, 'empresa_id': 1, 'rol': 'ADMINISTRADOR', 'contexto': 'personal', 'plan': 'PROFESIONAL'} |
| FAPI-02 | PASS | Login con contraseña incorrecta devuelve 401 | 401 con contraseña incorrecta |
| FAPI-03 | PASS | El bootstrap no filtra existencia de correos ni empresas | empresa ajena 403 y correo inexistente 401 |
| FAPI-04 | PASS | La API HTTP respeta RLS sobre el pool compartido | el cliente A2 ve 1 producto (RLS por empresa sobre el pool api) |
| FAPI-05 | PASS | Sin token, el endpoint empresarial no responde | 401 sin cabecera Authorization |
| FAPI-06 | PASS | Token con firma de otra clave devuelve 401 | 401 con token firmado por otra clave |
| FAPI-07 | PASS | Autorización por rol de empresa en HTTP | el administrador ve 2 clientes; el cliente recibe 403 |
| FAPI-08 | PASS | Crear por HTTP deja EXACTAMENTE un evento (el trigger, no el repositorio) | categoría 11 creada por HTTP con un único evento CREAR |
| FAPI-09 | PASS | Actualizar por HTTP añade un MODIFICAR sin duplicar | CREAR + MODIFICAR por HTTP: un evento por escritura |
| FAPI-10 | PASS | La bitácora es sólo para el administrador del tenant | admin ve 18 eventos; el cliente recibe 403 |
| FAPI-11 | PASS | Aislamiento entre empresas sobre HTTP | B1 ve su catálogo (1 categoría) y nunca el de la empresa A |
| FAPI-12 | PASS | El pool HTTP no filtra la identidad entre peticiones | A2 y B1 no comparten identidad ni filas entre peticiones |
| FAPI-13 | PASS | Las pruebas HTTP no dejan residuo en la base | bitácora restaurada a 2 eventos de categoría |

**Total: 86 pruebas, 86 PASS, 0 FAIL.**

## Hallazgos y correcciones propuestas

- Propuesta: 050a (bloqueante para el login por slug): `GRANT SELECT(id) ON public.empresa` no habilita las columnas que usa el predicado de bootstrap_empresa_id() (slug, estado) y la función SECURITY DEFINER aborta con 42501. Corrección mínima probada en BST-08: `GRANT SELECT(id, slug, estado) ON public.empresa TO fashionstore_bootstrap_owner`.
- Propuesta: Bitácora: 050c concede SELECT a fashionstore_api a propósito; la lectura queda limitada por las policies de 050b a personal del propio tenant (verificado en AIS-10). No es una fuga, pero conviene documentarlo en el contrato del endpoint.
