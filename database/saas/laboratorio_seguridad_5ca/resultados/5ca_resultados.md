# Evidencia del laboratorio 5CA

- Fecha: 2026-11-06T19:31:39+00:00
- PostgreSQL: 17.9
- Clúster desechable en: C:\Users\Harold\AppData\Local\Temp\fashionstore-5ca-lab-jxtqj1pu
- Base: fashionstore_lab_5ca
- Copias 050a-c con la guarda retirada: 050a_lab.sql, 050b_lab.sql, 050c_lab.sql

## Resumen estructural

| Métrica | Valor |
|---|---|
| tablas | 46 |
| vistas | 1 |
| fk | 145 |
| fk_no_validadas | 0 |
| indices | 251 |
| triggers | 58 |
| tablas_rls | 46 |
| tablas_force_rls | 46 |
| politicas | 110 |
| funciones_privadas | 14 |
| empresas | 2 |
| planes | 2 |
| membresias | 5 |

## Pruebas

| Código | Resultado | Prueba | Detalle |
|---|---|---|---|
| EST-01 | PASS | 46 tablas y 145 FK validadas, igual que la etapa 2 | 46 tablas, 145 FK válidas, 0 sin validar |
| EST-02 | PASS | FORCE RLS en las 46 tablas y políticas instaladas | force=46 políticas=110 |
| EST-03 | PASS | Ningún rol fashionstore es SUPERUSER ni tiene BYPASSRLS | 10 roles examinados, 0 con bypass ni superusuario |
| EST-04 | PASS | Conexión API autentica con login propio y sin BYPASSRLS | session_user=fashionstore_5ca_api current_user=fashionstore_5ca_api bypassrls=False |
| EST-05 | PASS | La API ve exactamente una empresa (la de su token) | 1 empresa visible para A1 |
| ACL-01 | PASS | API no puede leer el Vault local | rechazado con sqlstate=42501 |
| ACL-02 | PASS | API no puede leer vault.secrets | rechazado con sqlstate=42501 |
| ACL-03 | PASS | API no puede insertar bitácora directamente | rechazado con sqlstate=42501 |
| ACL-04 | PASS | API no puede crear objetos en public | rechazado con sqlstate=42501 |
| ACL-05 | PASS | API no puede ejecutar los procedimientos legacy | rechazado con sqlstate=42501 |
| ACL-06 | PASS | API no puede asumir el rol platform_admin | rechazado con sqlstate=42501 |
| ACL-07 | PASS | Bootstrap no puede leer la tabla usuario | rechazado con sqlstate=42501 |
| ACL-08 | PASS | Bootstrap no puede ejecutar actor_claims() | rechazado con sqlstate=42501 |
| ACL-09 | PASS | Platform_admin no puede leer tablas comerciales | rechazado con sqlstate=42501 |
| ACL-10 | PASS | API no ve usuarios de otra empresa por id directo | 0 filas del usuario 201 de la empresa B |
| ACL-11 | PASS | Bootstrap obtiene id+hash sólo por su función estrecha | 1 credencial devuelta |
| ACL-12 | PASS | Bootstrap no puede abrir la vista jwt_keys | rechazado con sqlstate=42501 |
| ID-01 | PASS | Sin token, toda consulta empresarial falla cerrada | rechazado con sqlstate=42501 |
| ID-02 | PASS | Token válido A1 deriva empresa/usuario del JWT firmado | actor=(1,101) |
| ID-03 | PASS | Manipular app.empresa_id/app.usuario_id NO cambia la identidad | actor sigue (1,101) y 1 producto visible |
| ID-04 | PASS | Token con firma alterada | rechazado con sqlstate=42501 |
| ID-05 | PASS | Token expirado | rechazado con sqlstate=42501 |
| ID-06 | PASS | Token con alg=none | rechazado con sqlstate=42501 |
| ID-07 | PASS | Token sin jti | rechazado con sqlstate=42501 |
| ID-08 | PASS | Token con scope distinto de tenant | rechazado con sqlstate=42501 |
| ID-09 | PASS | Token de A1 apuntando a empresa sin membresía | rechazado con sqlstate=42501 |
| ID-10 | PASS | Token de usuario inexistente | rechazado con sqlstate=42501 |
| ID-11 | PASS | Token con iat en el futuro | rechazado con sqlstate=42501 |
| ID-12 | PASS | Token desmesurado (>8192 caracteres) | rechazado con sqlstate=42501 |
| ID-13 | PASS | Token personal de cliente (contexto incoherente) | rechazado con sqlstate=42501 |
| ID-14 | PASS | Revocar la membresía invalida el token vigente en la siguiente sentencia | 42501 en la sentencia siguiente a la revocación |
| ID-15 | PASS | Suspender la suscripción invalida el token vigente | 42501 con la suscripción de la empresa B suspendida |
| ID-16 | PASS | Rotación de clave: token viejo sigue válido con 'previous' | rotación: clave previa válida y revocada al eliminarla |
| AIS-01 | PASS | A1 sólo ve el producto de su empresa | 1 producto |
| AIS-02 | PASS | B1 sólo ve el producto de su empresa | 1 producto |
| AIS-03 | PASS | A1 no ve el cliente de la empresa B | 0 filas de B |
| AIS-04 | PASS | Administrador A1 ve los 2 clientes de su empresa | 2 clientes |
| AIS-05 | PASS | A1 sólo ve sus 2 filas de inventario | 2 filas |
| AIS-06 | PASS | A1 sólo ve su reserva y su venta | 1 reserva + 1 venta |
| AIS-07 | PASS | B1 no ve pagos de la empresa A | 0 pagos ajenos |
| AIS-08 | PASS | A1 no puede insertar producto con empresa_id ajeno | rechazado con sqlstate=42501 |
| AIS-09 | PASS | A1 no puede modificar el producto de la empresa B | 0 filas afectadas sobre el producto de B |
| AIS-10 | PASS | A1 no puede mover empresa_id de su propio producto | rechazado con sqlstate=42501 |
| AIS-11 | PASS | A1 administrador sí puede crear producto en su empresa | 1 producto insertado y revertido al cerrar la transacción |
| AIS-12 | PASS | El cliente A2 sólo ve su propia ficha de cliente | 1 cliente propio |
| AIS-13 | PASS | El cliente A2 sólo ve su carrito, no el de otro cliente | 1 carrito propio |
| AIS-14 | PASS | El cliente A2 no ve la reserva de otro cliente | 0 reservas ajenas |
| AIS-15 | PASS | El cliente A2 no ve bitácora, empleados ni proveedores | 0 filas de personal |
| AIS-16 | PASS | El administrador A1 no ve bitácora de otras empresas | sin filas ajenas |
| AIS-17 | PASS | El cliente A2 no puede insertar en el carrito de otro cliente | insert rechazado con sqlstate=P0001; actor_posee_carrito(4)=false |
| AIS-18 | PASS | El cliente A2 no puede crear un carrito para otro cliente | rechazado con sqlstate=42501 |
| AIS-19 | PASS | El cliente A2 no puede escribir catálogo (sólo administrador) | rechazado con sqlstate=42501 |
| AIS-20 | PASS | B1 no puede actualizar la sucursal de A | 0 filas afectadas sobre la sucursal de A |
| POOL-01 | PASS | Conexión reutilizada A1->B1->A3 sin fugas de identidad | 3 identidades + 40 reutilizaciones, 1 conexión física ({29652}) |
| POOL-02 | PASS | Conexiones concurrentes no comparten identidad | 3 hilos simultáneos sin intercambiar identidad: [(1, 101), (2, 201), (1, 103)] |
| POOL-03 | PASS | GUC no local persiste: la aplicación debe usar is_local=true | RIESGO CONFIRMADO: set_config con is_local=false deja el token en la sesión; FastAPI DEBE usar siempre el tercer argumento true |
| SP-01 | PASS | SP tenant con actor correcto crea reserva y reserva stock | reserva 3, carrito CONVERTIDO, stock_reservado=1, 1 movimiento de inventario, 1 detalle |
| SP-02 | PASS | SP tenant rechaza suplantar a otro usuario del mismo tenant | rechazado con sqlstate=42501 |
| SP-03 | PASS | SP tenant rechaza una empresa distinta a la del token | rechazado con sqlstate=42501 |
| SP-04 | PASS | La API no puede ajustar inventario por UPDATE directo | rechazado con sqlstate=42501 |
| SP-05 | PASS | La API no puede insertar ventas directamente | rechazado con sqlstate=42501 |
| SP-06 | PASS | El login platform_admin no puede ejecutar el SP tenant | rechazado con sqlstate=42501 |
| SP-07 | PASS | Carrera por la última unidad: exactamente una reserva gana | 1 éxito / 1 rechazo 23514; stock_actual=1 reservado=1 disponible=0 |
| SP-08 | PASS | Tras el SP no queda identidad residual en conexiones nuevas | sin token la siguiente conexión falla con 42501 |
| FAPI-01 | PASS | Login real (bootstrap+Argon2) y /lab/me con JWT | login OK, /lab/me={'usuario_id': 101, 'empresa_id': 1, 'rol': 'ADMINISTRADOR', 'contexto': 'personal', 'plan': 'PROFESIONAL'} |
| FAPI-02 | PASS | Login con contraseña incorrecta devuelve 401 | 401 con clave incorrecta |
| FAPI-03 | PASS | Login pidiendo empresa ajena devuelve 403 | 403 al pedir una empresa sin membresía |
| FAPI-04 | PASS | La API HTTP respeta RLS sobre el pool compartido | el cliente A2 ve 1 producto (RLS por empresa) |
| FAPI-05 | PASS | Sin token, el endpoint empresarial no responde | 401 sin token |
| FAPI-06 | PASS | Token con firma de otra clave devuelve 401 | 401 con token firmado por otra clave |
| FAPI-07 | PASS | Autorización por rol de empresa en HTTP | admin ve 2 clientes; el cliente recibe 403 |
| FAPI-08 | PASS | El SP tenant se ejecuta por HTTP con identidad firmada | reserva creada por HTTP: {'reserva_id': 6} |
| FAPI-09 | PASS | El bootstrap no filtra existencia de correos ni empresas | empresa ajena 403 y correo inexistente 401 |

**Total: 73 pruebas, 73 PASS, 0 FAIL.**
